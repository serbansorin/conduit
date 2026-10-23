# api

The public API of the Conduit gateway.

| File | Purpose |
| --- | --- |
| `router.go` | `NewRouter` — middleware chain and route registration (`/health`, `/metrics`, `/v1/models`, `/v1/chat/completions`, `/v1/messages`, `/v1/messages/count_tokens`, `/v1/responses`) plus the bare-path mirrors |
| `middleware.go` | `Auth()` (token validation), `extractToken`, `RequireGroup()`, `RequestLimitMiddleware()` |
| `ratelimit.go` | `RateLimitMiddleware()` — per-token sliding window |
| `proxy.go` | `proxyRequest` and the per-upstream attempt logic, header forwarding, upstream auth injection |
| `decode.go` | `parseRequestBody` — pulls `model` and `stream` out of the JSON body |
| `models.go` | `listModels` — `GET /v1/models` |
| `ollama.go` | the Ollama upstream path |
| `stream.go` | same-protocol and cross-protocol SSE streaming |
| `usage.go` | usage accounting and cost estimation |
| `error.go` | `writeOpenAIError` — the error envelope |
| `health.go` | `/health` and `/metrics` handlers |
| `metrics.go` | the Prometheus collector |

See the root `README.md` for the request lifecycle and the upstream session-header contract.
