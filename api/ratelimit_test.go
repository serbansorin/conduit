package api

import (
	"testing"

	"github.com/serbansorin/conduit/store"
)

func TestRateLimiter_UnlimitedToken(t *testing.T) {
	rl := newRateLimiter()
	tok := &store.Token{ID: 1, RateLimit: 0} // unlimited
	for i := 0; i < 1000; i++ {
		if allowed, _, _ := rl.allow(tok); !allowed {
			t.Fatalf("unlimited token should never be rate-limited (iteration %d)", i)
		}
	}
}

func TestRateLimiter_LimitedToken(t *testing.T) {
	rl := newRateLimiter()
	tok := &store.Token{ID: 2, RateLimit: 5}

	// Should allow exactly 5 requests.
	for i := 0; i < 5; i++ {
		if allowed, _, _ := rl.allow(tok); !allowed {
			t.Fatalf("request %d should be allowed", i)
		}
	}
	// 6th should be denied.
	if allowed, _, _ := rl.allow(tok); allowed {
		t.Fatal("6th request should be denied")
	}
}

func TestRateLimiter_DifferentTokensIndependent(t *testing.T) {
	rl := newRateLimiter()
	tok1 := &store.Token{ID: 10, RateLimit: 2}
	tok2 := &store.Token{ID: 11, RateLimit: 2}

	// Exhaust tok1.
	rl.allow(tok1)
	rl.allow(tok1)
	if allowed, _, _ := rl.allow(tok1); allowed {
		t.Fatal("tok1 should be exhausted")
	}

	// tok2 should still be fine.
	if allowed, _, _ := rl.allow(tok2); !allowed {
		t.Fatal("tok2 should not be affected by tok1")
	}
	if allowed, _, _ := rl.allow(tok2); !allowed {
		t.Fatal("tok2 second request should be allowed")
	}
	if allowed, _, _ := rl.allow(tok2); allowed {
		t.Fatal("tok2 should be exhausted")
	}
}

func TestRateLimiter_SlidingWindow(t *testing.T) {
	rl := newRateLimiter()
	tok := &store.Token{ID: 20, RateLimit: 3}

	// Use a custom window with timestamps in the past to simulate time passing.
	// We'll manipulate the internal state directly.
	rl.mu.Lock()
	rl.windows[tok.ID] = &window{
		limit: 3,
		// All timestamps are > 1 minute ago, so they should be purged.
	}
	rl.mu.Unlock()

	// Should allow 3 fresh requests.
	for i := 0; i < 3; i++ {
		if allowed, _, _ := rl.allow(tok); !allowed {
			t.Fatalf("request %d should be allowed after window reset", i)
		}
	}
	if allowed, _, _ := rl.allow(tok); allowed {
		t.Fatal("should be rate-limited after 3 requests")
	}
}
