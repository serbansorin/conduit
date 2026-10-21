---
name: conduit-api-endpoint
description: >-
  Add an endpoint to the Conduit gateway, either a public /v1 route or an
  admin route. Use when touching api/router.go, api/error.go, admin/router.go
  or adding a test in api/gateway_test.go. Triggers on new endpoint, route,
  handler, middleware, auth, error envelope, CORS, request id, admin JWT or
  proxy test.
metadata:
  category: development
  suggest_for:
    filename:
      - '*.go'
---

# Conduit API Endpoints

Two routers. The public one lives in `api/router.go` and the admin one in
`admin/router.go`. They are mounted onto the same Gin engine by
`server/server.go`.

## The public API

`api/router.go` `NewRouter(p *pool.Picker) *gin.Engine`.

Public, no token needed:

| Method | Path | Handler |
| --- | --- | --- |
| `GET` | `/health` | `health` in `api/health.go` |
| `GET` | `/` | `health` |
| `GET` | `/metrics` | `metrics.render` |
| `GET` | `/v1/models` | `listModels` in `api/models.go` |
| `GET` | `/models` | `listModels` (bare alias) |

Token required:

| Method | Path | Handler |
| --- | --- | --- |
| `POST` | `/v1/chat/completions` | `proxyChat(p)` |
| `POST` | `/v1/messages` | `proxyMessages(p)` |
| `POST` | `/v1/messages/count_tokens` | `countTokens` |
| `POST` | `/v1/responses` | `proxyResponses(p)` |
| `POST` | `/chat/completions`, `/messages`, `/messages/count_tokens`, `/responses` | the same handlers, mounted on the bare group for clients that skip the `/v1` prefix |

### Middleware order

Global middleware, in this order:

```go
r.Use(gin.Recovery())
r.Use(requestLogger())
r.Use(requestIDMiddleware())
r.Use(corsMiddleware())
r.Use(bodyLimitMiddleware())
```

The auth group:

```go
auth := v1.Group("", Auth(), rateLimit, reqLimit)
```

`Auth()` from `api/middleware.go` first, then `RateLimitMiddleware()` from
`api/ratelimit.go`, then `RequestLimitMiddleware()` from `api/middleware.go`.
The last two read the `*store.Token` that `Auth()` stored under the context
key `"token"`, so they must stay after it.

### The request id middleware

`requestIDMiddleware` lives in `api/health.go`, not in `api/middleware.go`:

```go
const requestIDHeader = "X-Request-Id"

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
```

A client supplied `X-Request-Id` is preserved. The id is stored in the
context under `"request_id"` and is what the usage log records.

### CORS

`corsMiddleware` in `api/router.go` is **not** an allowlist. It sets
`Access-Control-Allow-Origin: *` and allows any origin:

```go
c.Header("Access-Control-Allow-Origin", "*")
c.Header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
c.Header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Api-Key, Anthropic-Version, Anthropic-Beta, X-Request-Id")
c.Header("Access-Control-Max-Age", "86400")
```

`OPTIONS` requests are answered with 204 and aborted. If you add a header the
panel or a client needs, add it to the `Access-Control-Allow-Headers` list.

### The body limit

`bodyLimitMiddleware` wraps the body in `http.MaxBytesReader` with
`maxRequestBodyBytes = 32 << 20` (32 MiB). The constant lives in
`api/router.go`. `proxyRequest` maps the `*http.MaxBytesError` to 413
`request_too_large`.

## Adding a public endpoint

1. Put the handler in a new file under `api/` or in the closest existing one.
   Name it after the resource: `api/models.go` holds `listModels`,
   `api/health.go` holds `health`, `api/count_tokens.go` holds `countTokens`.
   A handler that needs to sit next to the proxy belongs in `api/proxy.go`.
2. Register it in `api/router.go`. A public route follows the pattern of the
   existing ones:

```go
r.GET("/health", health)
```

An authenticated route goes on the auth group:

```go
auth := v1.Group("", Auth(), rateLimit, reqLimit)
auth.GET("/your-resource", yourHandler)
```

The group is `/v1`, so that registers `/v1/your-resource`. If the endpoint
must also answer on the bare path, register it on `auth2` as well or add it
to `registerProxyRoutes`.

3. Use `writeOpenAIError` for errors. Do not invent a new error shape.

## The admin API

`admin/router.go` `Mount(rg *gin.RouterGroup)` registers the routes on the
group it is given. `server/server.go` mounts it at `/admin` and calls
`admin.MountWithPicker(root.Group("/admin"), picker)` so the handlers can
reach the key pool.

Everything except the login sits behind `adminAuth()`:

```go
rg.POST("/login", login)

authed := rg.Group("")
authed.Use(adminAuth())
```

`adminAuth()` requires `Authorization: Bearer <jwt>`. The JWT is issued by
`POST /admin/login`:

```go
claims := jwt.MapClaims{
	"role": "admin",
	"iss":  "conduit",
	"aud":  "admin",
	"iat":  now.Unix(),
	"jti":  jti,
	"exp":  now.Add(12 * time.Hour).Unix(),
}
```

Validation is strict: `jwt.WithValidMethods([]string{"HS256"})`,
`jwt.WithIssuer("conduit")`, `jwt.WithAudience("admin")`,
`jwt.WithExpirationRequired()` and `jwt.WithIssuedAt()`. The handler also
rejects a token with no `iat` claim and any token whose `role` is not
`admin` (403 `not admin`).

The signing secret is `config.Get().JWTSecret`.

Admin routes, all behind `adminAuth()`:

| Method | Path | Handler |
| --- | --- | --- |
| `GET` | `/admin/health` | `poolHealth` |
| `GET` | `/admin/version` | `versionInfo` |
| `GET` | `/admin/keys` | `listKeys` |
| `POST` | `/admin/keys` | `createKey` |
| `POST` | `/admin/keys/import-github` | `importGithubKeys` |
| `PATCH` | `/admin/keys/:id` | `updateKey` |
| `POST` | `/admin/keys/:id/toggle` | `toggleKey` |
| `POST` | `/admin/keys/:id/reset` | `resetKeyCooldown` |
| `GET` | `/admin/keys/:id/quota` | `fetchKeyQuota` |
| `DELETE` | `/admin/keys/:id` | `deleteKey` |
| `GET` | `/admin/tokens` | `listTokens` |
| `POST` | `/admin/tokens` | `createTokenAdmin` |
| `PATCH` | `/admin/tokens/:id` | `updateToken` |
| `DELETE` | `/admin/tokens/:id` | `deleteToken` |
| `GET` | `/admin/stats` | `stats` |
| `GET` | `/admin/usage` | `listUsageLogs` |
| `GET` | `/admin/usage.csv` | `exportUsageCSV` |
| `GET` | `/admin/backup` | `exportBackup` |
| `POST` | `/admin/restore` | `restoreBackup` |
| `GET` | `/admin/models` | `listModelsAdmin` |
| `POST` | `/admin/models/sync` | `syncModels` |
| `POST` | `/admin/models/rebuild` | `rebuildModels` |
| `POST` | `/admin/models` | `upsertModel` |
| `PATCH` | `/admin/models/:id` | `updateModel` |
| `POST` | `/admin/models/:id/toggle` | `toggleModel` |
| `DELETE` | `/admin/models/:id` | `deleteModel` |
| `GET` | `/admin/model-mappings` | `listModelMappingsAdmin` |
| `POST` | `/admin/model-mappings` | `upsertModelMapping` |
| `DELETE` | `/admin/model-mappings/*source` | `deleteModelMapping` |

Admin handlers answer with a flat `{"error": "..."}` object, not the OpenAI
error envelope. The admin API is not consumed by LLM SDKs.

Note `DELETE /admin/model-mappings/*source` uses a wildcard parameter, because
a model id can contain slashes.

### Adding an admin endpoint

```go
authed := rg.Group("")
authed.Use(adminAuth())
{
	authed.GET("/usage", listUsageLogs)
	authed.GET("/your-thing", yourThing)
}
```

Read the body with `c.ShouldBindJSON`. Use a pointer field for every optional
value so you can tell "absent" from "zero":

```go
var body struct {
	Name  *string `json:"name"`
	Count *int    `json:"count"`
}
```

That is the pattern the model handlers use, and it is what lets them record
which fields the admin actually changed.

## The error envelope

`api/error.go` `writeOpenAIError(c *gin.Context, status int, errType,
message string)` is the only way to write a public API error:

```go
func writeOpenAIError(c *gin.Context, status int, errType, message string) {
	c.JSON(status, gin.H{
		"type": "error",
		"error": gin.H{
			"type":    errType,
			"message": message,
		},
	})
}
```

The body carries both shapes on purpose. The Anthropic SDK looks for the
top-level `"type": "error"` marker, OpenAI clients read the nested `error`
object. Keep both.

Error types used by the public API:

| `error.type` | Status | Raised by |
| --- | --- | --- |
| `authentication_error` | 401 | `abortAuth` in `api/middleware.go` |
| `permission_denied` | 403 | `RequireGroup`, the failover loop, and the no-upstream case |
| `request_limit_exceeded` | 403 | `RequestLimitMiddleware` |
| `rate_limit_exceeded` | 429 | `RateLimitMiddleware` |
| `service_unavailable` | 503 | `RequestLimitMiddleware` on a database error |
| `model_not_found` | 404 | `proxyRequest` |
| `model_disabled` | 403 | `proxyRequest` |
| `invalid_request_error` | 400 | `proxyRequest` on a body read failure |
| `request_too_large` | 413 | `proxyRequest` on `*http.MaxBytesError` |
| `conversion_error` | 400 | `proxyRequest` when the body cannot be converted |
| `upstream_error`, `upstream_timeout`, `permission_denied`, `rate_limit_error` | upstream status | `upstreamErrorType` |

Never echo the upstream error body to the client. Use
`genericUpstreamMessage(status)` for the client facing message and
`summarizeUpstreamError(status, body)` for the admin usage log. The summary
redacts bearer tokens and api keys with `sensitiveErrorPatterns`.

## Testing a new endpoint

`api/gateway_test.go` has the pattern. It starts a mock upstream with
`httptest`, points the gateway at it, and asserts on what the upstream
received.

```go
type capturedRequest struct {
	mu      sync.Mutex
	headers http.Header
	body    string
	path    string
}

func (c *capturedRequest) record(r *http.Request) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.headers = r.Header.Clone()
	c.path = r.URL.Path
	buf := make([]byte, 4096)
	n, _ := r.Body.Read(buf)
	c.body = string(buf[:n])
}
```

The mock upstream:

```go
func newMockUpstream(t *testing.T, captured *capturedRequest) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		captured.record(r)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"id":"msg_1","type":"message","role":"assistant","model":"minimax-m3","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":3,"output_tokens":2}}`))
	}))
	t.Cleanup(srv.Close)
	return srv
}
```

The test skeleton:

```go
func TestYourEndpoint(t *testing.T) {
	setupGatewayTestDB(t)

	gin.SetMode(gin.TestMode)
	router := NewRouter(pool.NewPicker())

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", rec.Code, rec.Body.String())
	}
}
```

Helpers you will reuse:

- `setupGatewayTestDB(t)` calls `store.InitForTest("file:api_gateway_test?mode=memory&cache=shared")`.
  The DSN is shared, so give each test its own name if they run in parallel.
- `insertTestKey(t, "test-key")` inserts an enabled key in group `go`.
- `registerTestModel(t, "minimax-m3")` calls `config.ReplaceModels` with a
  route that speaks `ProtocolMessages` in group `go`.
- `pool.CreateToken(name, allowedGroups, rateLimit, expiresAt)` creates a
  gateway token. Send it as `Authorization: Bearer <token>` or as `x-api-key`.

When the test needs a real upstream, point the gateway at the mock server and
restore the original afterwards:

```go
originalBase := config.Get().GoBaseURL
config.Get().GoBaseURL = upstream.URL
t.Cleanup(func() { config.Get().GoBaseURL = originalBase })
```

`api/proxy_test.go` holds the unit level tests for the helper functions
(`copyForwardHeaders`, `injectUpstreamAuth` and friends). Put a pure function
test there. Put a route or handler test in `api/gateway_test.go`.

## Traps

- `Auth()` must run before any middleware that reads the `"token"` context
  value.
- `requestIDMiddleware` must stay before the handlers, and its header must
  keep the exact spelling `X-Request-Id`.
- CORS is wide open by design. Do not narrow it to an allowlist without
  changing the panel and the SDK behaviour deliberately.
- The bare group `auth2` duplicates the `/v1` routes. A new authenticated
  route added to only one of them will behave differently depending on the
  path the client used.
- `admin.Mount` is called on the group, so the paths inside it are relative.
  `admin.MountWithPicker(root.Group("/admin"), picker)` makes them
  `/admin/...`.
- `RequireGroup()` in `api/middleware.go` is defined but is not registered on
  any router. The group permission check actually happens inside
  `api/proxy.go`, per upstream, in the failover loop. Do not assume the
  middleware is running.
- The panel is served from `GET /admin` plus a `NoRoute` fallback for any
  `/admin/...` path. A new admin **API** route must not collide with a panel
  path.
- The admin panel calls the admin API from the browser, so a new admin
  endpoint needs a matching function in `web/js/api.js`. See the
  `conduit-admin-panel` skill.

## Verify a change

```bash
export PATH="/opt/homebrew/bin:$PATH"
go build ./...
go test -race ./...
gofmt -l .
./scripts/smoke.sh
```

`./scripts/smoke.sh` starts a real server and exercises `/health`,
`/v1/models`, `/metrics`, `/v1/messages/count_tokens`, the 401 path,
`POST /admin/login`, `/admin/backup`, `/admin/usage.csv`, `/admin/keys`, the
404 for an unknown model and the `X-Request-Id` header. Add a check there when
you add a route that matters end to end.
