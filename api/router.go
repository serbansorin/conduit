package api

import (
	"log"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/serbansorin/conduit/pool"
)

// maxRequestBodyBytes limits the total request body size (default 32 MiB to
// accommodate large JSON with base64-encoded images). Requests exceeding this
// are rejected with 413.
const maxRequestBodyBytes = 32 << 20 // 32 MiB

// bodyLimitMiddleware wraps the request body in a MaxBytesReader to enforce
// a hard limit on incoming payload size, preventing memory exhaustion from
// oversized or never-ending request bodies.
func bodyLimitMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxRequestBodyBytes)
		c.Next()
	}
}

// NewRouter builds the public API router: health + the four universal endpoints.
//
//	GET  /health
//	GET  /v1/models                 (public)
//	POST /v1/chat/completions       (auth)
//	POST /v1/messages               (auth)
//	POST /v1/responses              (auth)
func NewRouter(p *pool.Picker) *gin.Engine {
	metrics := newMetricsCollector()

	r := gin.New()
	r.Use(gin.Recovery())
	r.Use(requestLogger())
	r.Use(requestIDMiddleware())
	r.Use(corsMiddleware())
	r.Use(bodyLimitMiddleware())

	r.GET("/health", health)
	r.GET("/", health)
	r.GET("/metrics", func(c *gin.Context) { metrics.render(c.Writer) })

	v1 := r.Group("/v1")
	v1.GET("/models", listModels)

	rateLimit := RateLimitMiddleware()
	reqLimit := RequestLimitMiddleware()

	auth := v1.Group("", Auth(), rateLimit, reqLimit)
	registerProxyRoutes(auth, p, metrics)

	// OpenCode clients sometimes hit the bare endpoints without /v1; mirror them.
	auth2 := r.Group("", Auth(), rateLimit, reqLimit)
	registerProxyRoutes(auth2, p, metrics)
	r.GET("/models", listModels)

	return r
}

// registerProxyRoutes registers the three proxy endpoints on a router group.
func registerProxyRoutes(rg *gin.RouterGroup, p *pool.Picker, metrics *metricsCollector) {
	rg.POST("/chat/completions", metrics.middleware(), proxyChat(p))
	rg.POST("/messages", metrics.middleware(), proxyMessages(p))
	rg.POST("/messages/count_tokens", countTokens)
	rg.POST("/responses", metrics.middleware(), proxyResponses(p))
}

// requestLogger logs one line per request at the access level.
func requestLogger() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		latency := time.Since(start)
		log.Printf("[%s] %s %s %d %v",
			c.Request.Method, c.Request.URL.Path,
			c.ClientIP(), c.Writer.Status(), latency,
		)
	}
}

// corsMiddleware allows cross-origin requests from any origin.
func corsMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Header("Access-Control-Allow-Origin", "*")
		c.Header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
		c.Header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Api-Key")
		c.Header("Access-Control-Max-Age", "86400")

		if c.Request.Method == http.MethodOptions {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}
		c.Next()
	}
}
