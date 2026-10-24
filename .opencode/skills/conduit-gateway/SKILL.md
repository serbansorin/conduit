---
name: conduit-gateway
description: >-
  Change the core of the Conduit gateway: the request lifecycle from API
  middleware through model resolution, key-pool picking and the upstream hop.
  Use when touching api/proxy.go, api/middleware.go, api/ratelimit.go,
  internal/router, internal/session, pool/, protocol/, store/sqlite.go or
  server/. Triggers on proxying, failover, key cooldown, session headers,
  protocol conversion, SQLite schema changes or startup wiring.
metadata:
  category: development
  suggest_for:
    filename:
      - '*.go'
---

# Conduit Gateway

Conduit is a multi-key LLM gateway. It pools upstream API keys behind one
gateway token and speaks the OpenAI Chat, Anthropic Messages and OpenAI
Responses protocols interchangeably.

The upstream service is **OpenCode Go** (`https://opencode.ai/zen/go`), a
separate product by OpenCode. It is not Conduit. Never rename it and never
confuse the two.

## The request lifecycle, end to end

`api/router.go` `NewRouter(p *pool.Picker) *gin.Engine` is the only place the
public API is assembled. Middleware order matters and is fixed:

```go
r := gin.New()
r.Use(gin.Recovery())
r.Use(requestLogger())
r.Use(requestIDMiddleware())
r.Use(corsMiddleware())
r.Use(bodyLimitMiddleware())
```

`bodyLimitMiddleware` wraps the body in `http.MaxBytesReader` with
`maxRequestBodyBytes = 32 << 20` (32 MiB). `proxyRequest` maps the
`*http.MaxBytesError` back to 413. Raise the constant in `api/router.go`, do
not add a second limit elsewhere.

Routes:

```go
r.GET("/health", health)
r.GET("/", health)
r.GET("/metrics", func(c *gin.Context) { metrics.render(c.Writer) })

v1 := r.Group("/v1")
v1.GET("/models", listModels)          // public, no token

auth := v1.Group("", Auth(), rateLimit, reqLimit)
registerProxyRoutes(auth, p, metrics)

// bare aliases, some OpenCode clients hit these without /v1
auth2 := r.Group("", Auth(), rateLimit, reqLimit)
registerProxyRoutes(auth2, p, metrics)
r.GET("/models", listModels)
```

`registerProxyRoutes` registers the four proxy endpoints:

```go
rg.POST("/chat/completions", metrics.middleware(), proxyChat(p))
rg.POST("/messages", metrics.middleware(), proxyMessages(p))
rg.POST("/messages/count_tokens", countTokens)
rg.POST("/responses", metrics.middleware(), proxyResponses(p))
```

Order of the three middlewares on the auth group is load bearing:

1. `Auth()` in `api/middleware.go` validates the gateway token and stores the
   `*store.Token` in the gin context under the key `"token"`.
2. `RateLimitMiddleware()` in `api/ratelimit.go` enforces the per-token
   sliding window (`Token.RateLimit` requests per minute, 0 means unlimited).
3. `RequestLimitMiddleware()` in `api/middleware.go` enforces the total request
   cap (`Token.MaxRequests`).

The last two read `"token"` from the context, so they must stay after
`Auth()`. `RateLimitMiddleware` returns 429 with `Retry-After`,
`X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`.
`RequestLimitMiddleware` returns 403 `request_limit_exceeded`, or 503
`service_unavailable` when the database errors. Never turn that 503 into a
403: a broken SQLite must not masquerade as an exhausted quota.

`RequestLimitMiddleware` reserves the slot with `store.TryReserveRequest`,
which does the increment and the limit check in one conditional SQL UPDATE so
concurrent requests cannot all pass when only one slot remains. If a request
fails and must not count, call `store.ReleaseRequest(tok.ID)`.

`api/proxy.go` `proxyRequest(c, p, inbound config.Protocol, upstreamPath)` is
the single orchestrator. Its own doc comment says it must not grow business
logic. The flow:

1. Read the original body. It is immutable and is the starting point for
   every per-upstream conversion.
2. `parseRequestBody` (in `api/decode.go`) extracts only `model` and `stream`.
3. `router.Resolve(head.Model, inbound)` resolves the route.
4. If the route is disabled: 403 `model_disabled`.
5. `c.Set("group", route.Group)`.
6. Failover loop over `route.Upstreams` (deduplicated by `uniqueUpstreams`).
   Each upstream is tried at most once, no circular retry.
7. Per upstream: `route.ResolveUpstreamGroup(currentUpstream)` is the single
   authoritative group resolver. Its result goes to the permission check, to
   `PickAttempts`, to the Ollama and Go handlers, to the usage log and to
   billing. Do not re-resolve the group anywhere else.
8. Per upstream: `buildUpstreamBody` converts the body if the upstream speaks
   a different protocol, then rewrites `model` to the real model id, then
   enables `stream_options.include_usage` for Chat streaming, then strips
   `tool_choice` for reasoning models.
9. Per key: build the request, forward headers, apply the session header,
   apply the user agent, inject the upstream auth, send it.
10. On failure, mark the key, log usage, and try the next key, then the next
    upstream.

## The upstream session header contract

`internal/session/session.go` is not optional. The OpenCode Go upstream
answers a request with no session header with `MissingSessionID` and refuses
to route it. The request dies before a model is ever consulted.

Rules:

- The canonical header the upstream reads is `X-Opencode-Session`
  (`session.Header`).
- `X-Conduit-Session` (`session.OverrideHeader`) is the gateway override. It
  wins over a derived id but not over a client supplied upstream session
  header.
- Client spellings that are forwarded untouched (`session.clientSessionHeaders`):
  `X-Opencode-Session`, `X-Session-Id`, `Session-Id`, `X-Conversation-Id`,
  `Conversation-Id`.
- `session.Resolve(clientHeaders, body, scope)` returns the override first,
  then the client value, then a derived id.
- The derived id is `derive(conversationSeed(body), scope)`, which is
  `"conduit-" + hex(sha256(scope + "|" + seed)[:16])`. The seed is the digest
  of the first user message, so every turn of a conversation maps to the same
  session and the upstream can reuse its prompt cache.
- `scope` comes from `tokenScope(c)` in `api/proxy.go`, which is
  `"t" + tokenID` or `"anonymous"`. Two tenants never share a derived id.
- `session.Apply(dst, clientHeaders, body, scope)` mirrors a client value onto
  the canonical header as well, so the upstream sees a session regardless of
  which spelling it prefers.

**Never remove `internal/session` from the outbound request path.** It is
applied in `proxyGoUpstream` right after `copyForwardHeaders`:

```go
copyForwardHeaders(req.Header, c.Request.Header)
session.Apply(req.Header, c.Request.Header, originalBody, tokenScope(c))
applyUpstreamUserAgent(req.Header, c.Request.Header)
applyAnthropicVersion(req.Header, upstreamProto)
setContentLength(req, len(upstreamBody))
injectUpstreamAuth(req.Header, key.Value)
```

`copyForwardHeaders` strips `Authorization`, `X-Api-Key`, `Api-Key`,
`Content-Length`, `Host`, `Accept-Encoding` and the hop-by-hop headers, then
`injectUpstreamAuth` sets the real upstream credential as both
`Authorization: Bearer <key>` and `X-Api-Key: <key>`.

`applyUpstreamUserAgent` keeps a client agent that identifies itself (Claude
Code sends its own and the upstream relies on it) and replaces bare library
defaults such as `python-httpx`, `curl/`, `go-http-client` with
`conduit/<version>`. The list is `genericUserAgents` in `api/proxy.go`.

## The intermediate representation

Everything goes through the IR in `protocol/`. The pipeline is:

```
inbound -> decode -> IR -> encode -> upstream
upstream -> decode -> IR -> encode -> inbound
```

Same protocol in and out is a passthrough. A cross-protocol request is rebuilt
from the IR, so protocol specific fields the IR does not model are dropped.
That is a known limitation. If you add a field, add it to the IR as well.

The IR types live in `protocol/types.go`:

- `IRRequest`, `IRMessage`, `IRContent`, `IRImageSource`, `IRTool`, `IRToolCall`
- `IRResponse`, `IRChoice`, `IRUsage`
- `IRStreamEvent`, `IRToolCallDelta`

`IRRequest.Extra`, `IRMessage.Extra` and `IRContent.Extra` are escape hatches
for fields the IR does not model yet.

Per protocol files, with matching Decode and Encode pairs:

| Protocol | File | Request | Response | Stream |
| --- | --- | --- | --- | --- |
| Chat | `protocol/chat.go` | `DecodeChatRequest` / `EncodeChatRequest` | `DecodeChatResponse` / `EncodeChatResponse` | `DecodeChatStreamChunk` / `EncodeChatStreamChunk` |
| Messages | `protocol/messages.go` | `DecodeMessagesRequest` / `EncodeMessagesRequest` | `DecodeMessagesResponse` / `EncodeMessagesResponse` | `DecodeMessagesStreamEvent` / `EncodeMessagesStreamEvent` |
| Responses | `protocol/responses.go` | `DecodeResponsesRequest` / `EncodeResponsesRequest` | `DecodeResponsesResponse` / `EncodeResponsesResponse` | `DecodeResponsesStreamEvent` / `EncodeResponsesStreamEvent` |

The generic entry points live in `protocol/convert.go`:

```go
func ConvertRequest(from, to config.Protocol, data []byte) ([]byte, error)
func ConvertResponse(from, to config.Protocol, data []byte) ([]byte, error)
func StreamConverter(dst io.Writer, src io.Reader, from, to config.Protocol) error
func StreamConverterWithUsage(dst io.Writer, src io.Reader, from, to config.Protocol) (*IRResponse, error)
```

`protocol/stream_convert.go` has `StreamConvertIncremental`, which is the
incremental decoder to encoder pipeline the cross-protocol SSE path uses so the
response is not buffered until the upstream finishes.

Streaming helpers per protocol are in `protocol/stream_chat.go`,
`protocol/stream_messages.go` and `protocol/stream_responses.go`
(`ChatStreamDecoder`, `MessagesStreamDecoder`, `ResponsesStreamDecoder`, and
the matching `New*StreamEncoder` constructors).

`protocol.StripToolChoiceForReasoning(body, model)` lives in
`protocol/chat.go`. Reasoning models such as DeepSeek reject a non-auto
`tool_choice` while thinking mode is active, so the gateway strips it before
sending.

## The key pool

`pool/key.go` holds the `Picker`. It is concurrency safe and uses weighted
round-robin scheduling with exponential-backoff cooldown.

```go
func NewPicker() *Picker {
	return &Picker{
		cursor: map[string]int64{},
		coolConf: cooldownConfig{
			threshold:    3,
			baseDuration: 30 * time.Second,
			maxDuration:  10 * time.Minute,
		},
	}
}
```

`PickAttempts(group string) ([]store.Key, error)` returns every usable key for
the group, ordered so that index 0 is the weighted round-robin pick and the
rest are fallback candidates for the same request. It filters out disabled
keys and keys whose `CooldownUntil` is in the future. Weight 0 is treated as
weight 1. An empty group means `"go"`.

`proxyGoUpstream` calls `p.PickAttempts(upstreamGroup)` and walks the returned
slice, calling `p.MarkUsed(key.ID)` before each attempt. Each attempt gets a
fresh timeout context via `upstreamRequestContext` so a timeout on one key does
not poison the next one. Streaming requests get no gateway deadline.

Cooldown rules:

- `MarkFailure(keyID)` increments `fail_count`. At `fail_count >= 3` it sets
  `cooldown_until = now + min(baseDuration * 2^(fails - threshold),
  maxDuration)`, so 30s, 60s, 120s, capped at 10 minutes.
- `MarkFailureWithQuota(keyID, statusCode, errBody, snapshot)` is what
  `api/proxy.go` calls. Status handling:
  - 401 -> `MarkFailure` (the normal exponential path).
  - 429 -> quota-aware cooldown. `parse429QuotaBucket` reads the key's
    `QuotaSnapshot` (JSON with `quota.rolling`, `quota.weekly`,
    `quota.monthly` buckets) and the error body, and cools the key until the
    exhausted bucket resets. Falls back to `MarkFailure` when the bucket
    cannot be determined.
  - Anything else -> `markFailureCountOnly`. The fail counter moves but
    `cooldown_until` is not set and is not extended. Only 401 and 429 are
    key-specific signals; a 5xx or a conversion error says nothing about the
    credential.
- `MarkSuccess(keyID)` resets `fail_count` and clears `cooldown_until`.
- `ResetCooldown(keyID)` is the admin manual reset behind
  `POST /admin/keys/:id/reset`.

`Picker.Stats(group)` returns `PoolStats{Total, Enabled, Available, Cooldown}`.
`pool.ErrNoAvailableKey` is returned when the group has no usable key, which
the proxy turns into 503.

`pool/token.go` holds the gateway token side: `GenerateToken`, `FindToken`,
`IsExpired`, `GroupAllowed`, `AllTokens`, `CreateToken`, and the options
`WithMaxRequests` and `WithDescription`.

## The SQLite schema

`store/sqlite.go` holds the GORM models. `store.Init()` is called once at
startup:

- The parent directory of `DB_PATH` is created with mode `0700`.
- The DSN is `DBPath + "?_journal_mode=WAL&_busy_timeout=5000"`.
- `AutoMigrate` runs over `Key`, `Token`, `UsageLog`, `ModelRouteRow` and
  `ModelMappingRow`.
- The database file is chmod'ed to `0600` so API keys, cookies and gateway
  tokens are not world readable.

Tables:

| Model | Purpose | Notable columns |
| --- | --- | --- |
| `Key` | one upstream API key | `value` (unique), `group`, `enabled`, `weight`, `proxy_url`, `cookie`, `workspace_id`, `quota_snapshot`, `fail_count`, `cooldown_until`, `last_used`, `usage_count` |
| `Token` | a gateway credential handed to clients | `token` (unique), `enabled`, `allowed_groups`, `rate_limit`, `max_requests`, `requests_used`, `expires_at` |
| `UsageLog` | one row per proxied request | `request_id`, `token_id`, `key_id`, `model`, `group`, `protocol`, `status_code`, `duration_ms`, `first_response_ms`, `ttft_ms`, token and cost columns, `error` |
| `ModelRouteRow` | a persisted model route | `id` (primary key), `upstream`, `upstreams_json`, `upstream_groups_json`, `protocol`, `real_model`, `group`, `status`, `priority`, `tags_json`, `pricing_json`, `targets_json`, `is_customized`, `customized_fields_json`, `last_synced_at` |
| `ModelMappingRow` | a UI managed model rewrite rule | `source_model` (primary key), `target_model` |

Conversion helpers: `store.ModelRouteFromRow(row)` and
`store.NewModelRouteRow(route)`. JSON encoded columns use the
`encodeJSON` / `decodeStringSlice` / `decodeStringMap` helpers, which return
empty values rather than errors so a malformed column cannot break startup.

`store.InitForTest(dsn string)` opens an in-memory SQLite for tests. It is
what `api/gateway_test.go` uses.

Gateway tokens are stored in plaintext and the admin API returns them
verbatim, because the panel has to show them for copying. That is deliberate.

## Server bootstrap

`server/server.go` is the shared wiring for the CLI and the desktop app.
`main.go` only parses flags and calls it.

```go
func Run(ctx context.Context, opts Options) (*Result, error)
```

`Options`:

| Field | Meaning |
| --- | --- |
| `Port` | TCP port. Zero picks a free port. |
| `UseRandomPort` | ask the OS for an unused port. The desktop app sets it. |
| `Bind` | interface to bind. Empty binds every interface. |
| `AsyncModelSync` | run the startup catalog sync in the background. |
| `SkipModelSync` | skip the catalog sync entirely. |
| `OnReady func(*Result)` | called once the listener accepts connections. |

`Result` carries `Addr`, `BaseURL`, `AdminURL` and `Token`. `Token` is set
only when a bootstrap token was created.

`Run` does, in order:

1. `config.ValidateSecurity()`. It refuses to start when `ADMIN_PASSWORD` is
   still `admin`, when `JWT_SECRET` is still the built-in default, or when
   `JWT_SECRET` is shorter than 32 bytes.
2. `store.Init()`.
3. `ensureBootstrapToken()`. On an empty token table it creates one and prints
   it to stdout in a banner.
4. `ensureBootstrapKey()`. When `GO_API_KEY` is set it is auto-added to the
   "go" key pool (idempotent: a duplicate value is never created). Manual
   keys added through the admin panel are never touched.
5. `loadModelRoutes()` then `loadModelMappings()`. Mappings are loaded from
   the DB first, then the env and file rules are overlaid on top, so runtime
   config overrides the DB managed defaults.
6. The catalog sync, then a background sync every 6 hours.
7. `api.NewRouter(picker)`, then `admin.MountWithPicker(root.Group("/admin"),
   picker)`, then the embedded panel at `GET /admin` with a `NoRoute` fallback
   so any `/admin/...` path serves the panel.

`config.Load()` runs once behind a `sync.Once`. Anything that changes
configuration at runtime, such as the desktop app setting `DB_PATH`, must do
it before the first call to `config.Load()` or `config.Get()`.

## Traps

- The upstream rejects a request with no session header. Never drop
  `session.Apply` from the outbound path.
- The group for an upstream is resolved exactly once per iteration by
  `route.ResolveUpstreamGroup`. `proxyRequest` makes a per-iteration copy of
  the route (`routeCopy := route`) so a resolved group does not leak into the
  next upstream.
- `c.Writer.Written()` is checked before every upstream and after every
  response handler. Once bytes are committed you must not send a second
  upstream response to the client.
- 400, 404, 409, 413, 415, 422 and 405 are not key retryable. Switching keys
  or providers for them produces semantic drift. `isClientErrorNonRetryable`
  in `api/error.go` documents this.
- 408 is upstream retryable but not key retryable.
- Upstream error bodies are never echoed to the client.
  `genericUpstreamMessage(status)` produces a safe message and the raw error
  goes to the admin usage log through `summarizeUpstreamError`, which also
  redacts bearer tokens and api keys.
- `copyControlHeaders` copies `Retry-After`, `WWW-Authenticate`,
  `X-Request-Id` and the `RateLimit-*` headers from an upstream error, and
  deliberately skips the representation headers that describe the upstream
  body.
- `upstream.NewClientForProxy` caches HTTP clients by normalised proxy URL.
  Creating a client per attempt defeats keep-alive and leaks connections.
- `upstream.Timeout()` comes from `UPSTREAM_TIMEOUT` in seconds. Zero means
  no gateway deadline.

## Verify a change

```bash
export PATH="/opt/homebrew/bin:$PATH"
go build ./...
go test -race ./...
gofmt -l .          # must print nothing
./scripts/smoke.sh  # boots a real server and exercises the endpoints
```

`api/gateway_test.go` has the pattern for testing the proxy against a mock
upstream. See the `conduit-testing` skill for the details.
