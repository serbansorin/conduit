package api

import (
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/serbansorin/conduit/store"
)

// rateLimiter is a per-token sliding-window rate limiter.
// Each token is allowed `RateLimit` requests per minute (0 = unlimited).
type rateLimiter struct {
	mu      sync.Mutex
	windows map[uint]*window // token ID -> sliding window
}

type window struct {
	timestamps []time.Time
	limit      int
}

func newRateLimiter() *rateLimiter {
	return &rateLimiter{
		windows: make(map[uint]*window),
	}
}

// allow reports whether the token is within its rate limit. It also returns
// how many requests remain in the current window and when the window resets,
// so the caller can advertise the budget to the client.
func (rl *rateLimiter) allow(tok *store.Token) (allowed bool, remaining int, reset time.Time) {
	if tok.RateLimit <= 0 {
		return true, -1, time.Time{}
	}
	rl.mu.Lock()
	defer rl.mu.Unlock()

	w, ok := rl.windows[tok.ID]
	if !ok {
		w = &window{limit: tok.RateLimit}
		rl.windows[tok.ID] = w
	}

	now := time.Now()
	cutoff := now.Add(-time.Minute)

	// Purge expired timestamps.
	valid := w.timestamps[:0]
	for _, t := range w.timestamps {
		if t.After(cutoff) {
			valid = append(valid, t)
		}
	}
	w.timestamps = valid

	if len(w.timestamps) > 0 {
		reset = w.timestamps[0].Add(time.Minute)
	} else {
		reset = now.Add(time.Minute)
	}

	if len(w.timestamps) >= w.limit {
		return false, 0, reset
	}
	w.timestamps = append(w.timestamps, now)
	return true, w.limit - len(w.timestamps), reset
}

// RateLimitMiddleware returns a Gin middleware that enforces per-token rate limits.
func RateLimitMiddleware() gin.HandlerFunc {
	rl := newRateLimiter()
	return func(c *gin.Context) {
		tokAny, exists := c.Get("token")
		if !exists {
			c.Next()
			return
		}
		tok, ok := tokAny.(*store.Token)
		if !ok || tok == nil {
			c.Next()
			return
		}
		allowed, remaining, reset := rl.allow(tok)
		if !allowed {
			retryAfter := int(time.Until(reset).Seconds())
			if retryAfter < 1 {
				retryAfter = 1
			}
			c.Header("Retry-After", strconv.Itoa(retryAfter))
			c.Header("X-RateLimit-Limit", strconv.Itoa(tok.RateLimit))
			c.Header("X-RateLimit-Remaining", "0")
			c.Header("X-RateLimit-Reset", strconv.FormatInt(reset.Unix(), 10))
			c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{
				"error": gin.H{
					"type":    "rate_limit_exceeded",
					"message": "rate limit exceeded for this token",
				},
			})
			return
		}
		if remaining >= 0 {
			c.Header("X-RateLimit-Limit", strconv.Itoa(tok.RateLimit))
			c.Header("X-RateLimit-Remaining", strconv.Itoa(remaining))
			c.Header("X-RateLimit-Reset", strconv.FormatInt(reset.Unix(), 10))
		}
		c.Next()
	}
}
