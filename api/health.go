package api

import (
	"crypto/rand"
	"encoding/hex"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/serbansorin/conduit/config"
	"github.com/serbansorin/conduit/pool"
	"github.com/serbansorin/conduit/version"
)

// requestIDHeader is the header used to carry a request identifier.
const requestIDHeader = "X-Request-Id"

// requestIDMiddleware assigns every request an identifier so a client can
// correlate its call with the gateway logs. An identifier supplied by the
// client is preserved.
func requestIDMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.GetHeader(requestIDHeader)
		if id == "" {
			id = newRequestID()
		}
		c.Set("request_id", id)
		c.Writer.Header().Set(requestIDHeader, id)
		c.Next()
	}
}

// newRequestID returns a short random request identifier.
func newRequestID() string {
	var buf [8]byte
	if _, err := rand.Read(buf[:]); err != nil {
		return strconv.FormatInt(time.Now().UnixNano(), 36)
	}
	return hex.EncodeToString(buf[:])
}

// health is the liveness probe. It reports the service identity, the running
// version and how much of the key pool is currently usable.
func health(c *gin.Context) {
	keys := 0
	enabled := 0
	if all, err := pool.AllByGroup("go"); err == nil {
		keys = len(all)
		for _, k := range all {
			if k.Enabled {
				enabled++
			}
		}
	}

	c.JSON(http.StatusOK, gin.H{
		"status":  "ok",
		"service": "conduit",
		"version": version.Version,
		"uptime":  time.Since(startedAt).Round(time.Second).String(),
		"models":  len(config.AllEnabledModels()),
		"keys": gin.H{
			"total":   keys,
			"enabled": enabled,
		},
	})
}

// startedAt records the process start time for the uptime report.
var startedAt = time.Now()
