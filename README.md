<div align="center">

# Conduit

**One endpoint. Every model.**

A multi-key LLM gateway that pools your upstream API keys and exposes them
through a single OpenAI- / Anthropic-compatible token — so any client, any
protocol, any model.

[![Go](https://img.shields.io/badge/go-1.25%2B-00ADD8?logo=go&logoColor=white)](https://go.dev)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![CI](https://github.com/serbansorin/conduit/actions/workflows/ci.yml/badge.svg)](https://github.com/serbansorin/conduit/actions/workflows/ci.yml)

</div>

---

## What it does

Conduit sits between your clients and your model providers.

- **Pools keys.** Add as many upstream keys as you like. Conduit picks one per
  request using weighted round-robin, backs off on failure with exponential
  cooldown, and rotates away from keys that hit a quota wall.
- **Speaks every protocol.** Call `/v1/chat/completions`, `/v1/messages` or
  `/v1/responses`. Conduit converts the request to an internal representation,
  forwards it to whichever protocol the target model actually speaks, and
  converts the answer back — including streaming SSE.
- **One token for everything.** Clients authenticate with a single
  `sk-…` gateway token that you can rate-limit, cap, expire or scope by group.
- **Tells you the truth.** Per-request token accounting (input, output,
  reasoning, cache read/write), per-model cost with group multipliers, latency
  percentiles, and a Prometheus endpoint.

Conduit is protocol-agnostic on the upstream side too: it ships with adapters
for **OpenCode Go** and **Ollama Cloud**, and the model catalogue is
discovered at runtime rather than hardcoded.

---

## Acknowledgements

Conduit began as **opencode-go**, written by the
[xb0or](https://github.com/xb0or) contributors and released under AGPL-3.0.
This repository is a maintained derivative: the original licence and
copyright notices are retained in [LICENSE](LICENSE) and in the git history.

The upstream service Conduit proxies to, **OpenCode Go**, is a separate
project by OpenCode — Conduit is not affiliated with or endorsed by it.


## Table of contents

- [Features](#features)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Connecting clients](#connecting-clients)
  - [Claude Code](#claude-code)
  - [Anthropic SDK](#anthropic-sdk)
  - [OpenAI SDK](#openai-sdk)
  - [opencode](#opencode)
  - [Codex](#codex)
  - [curl](#curl)
- [Model routing](#model-routing)
- [Configuration](#configuration)
- [Public API](#public-api)
- [Admin panel](#admin-panel)
- [Admin REST API](#admin-rest-api)
- [Python SDK](#python-sdk)
- [Desktop app](#desktop-app)
- [Observability](#observability)
- [Deployment](#deployment)
- [Development](#development)
- [License](#license)

---

## Features

### Gateway

- **Weighted key pool** — per-key weight, failure counts, exponential-backoff
  cooldown, usage counters, and automatic rotation past exhausted quotas.
- **Full cross-protocol conversion** — any client protocol reaches any model
  protocol, in both request and response direction, with streaming SSE intact.
- **Universal endpoints** — `/v1/chat/completions`, `/v1/messages`,
  `/v1/responses`, `/v1/messages/count_tokens`, `/v1/models`.
- **Gateway token auth** — `Authorization: Bearer <token>` or `x-api-key`,
  with per-token rate limiting, total request caps, expiry and group scopes.
- **Session affinity** — forwards the client's session header, or derives a
  stable per-conversation id when the client sends none, so the upstream can
  reuse its prompt cache between turns.
- **Failover** — a model route can name several upstreams; Conduit tries the
  next one when a key is cooling down or a quota is exhausted.
- **Embedded SQLite** (GORM, WAL mode) — keys, tokens, routes, mappings and
  usage logs in one file.
- **Single binary** — no external services, no build step for the admin panel.

### Observability

- **Token accounting** — input, output, reasoning, cache read and cache
  creation tokens, per call and aggregated per token.
- **Cost tracking** — per-model pricing with optional group multipliers.
- **Latency percentiles** — p50/p95/p99, time to first response and time to
  first token.
- **Prometheus metrics** at `/metrics`.
- **Request IDs** — every response carries `X-Request-Id`.

### Operations

- **Web admin panel** at `/admin` — dashboard, charts, key/token/route CRUD,
  quota lookup, dark and light themes, Chinese and English.
- **Admin REST API** for everything the panel does.
- **Config backup and restore** — export the whole configuration as JSON and
  restore it on another instance.
- **Usage CSV export** — one row per request.
- **CLI** — `conduit serve`, `conduit version`, `conduit help`.
- **Python SDK** — typed sync and async clients for both the gateway and the
  admin API.
- **Desktop app** — the admin panel as a native macOS application.

---

## Architecture

```
Client (any protocol, any SDK)
  │
  ▼
┌──────────────────────────────────────────────┐
│  Conduit                                     │
│  ┌────────────────────────────────────────┐  │
│  │ Auth · Rate limit · Request cap        │  │
│  ├────────────────────────────────────────┤  │
│  │ Protocol decoder                      │  │  ← Chat / Messages / Responses
│  ├────────────────────────────────────────┤  │
│  │ IR — intermediate representation      │  │  ← one shape every model speaks
│  ├────────────────────────────────────────┤  │
│  │ Protocol encoder                      │  │
│  ├────────────────────────────────────────┤  │
│  │ Weighted key picker                   │  │  ← round-robin + backoff
│  ├────────────────────────────────────────┤  │
│  │ Upstream proxy                        │  │  ← SSE passthrough
│  └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
  │
  ▼
OpenCode Go · Ollama Cloud · any OpenAI-compatible upstream
```

The IR is the trick that makes cross-protocol work: a request is decoded once
into a protocol-neutral shape, and the response is decoded from the upstream's
native shape back into whatever the client asked for.

---

## Quick start

### Run it locally

```bash
git clone https://github.com/serbansorin/conduit.git
cd conduit
go build -o conduit .
```

Conduit refuses to start with insecure defaults, so set them first:

```bash
export ADMIN_PASSWORD="a-strong-password"
export JWT_SECRET="$(openssl rand -hex 32)"
```

```bash
./conduit serve
```

On first run a bootstrap gateway token is printed to stdout:

```
==================================================
 Bootstrap gateway token created:
   sk-xxxxxxxxxxxxxxxx
 Use this as the api key in opencode / clients.
==================================================
```

The admin panel is at <http://localhost:9812/admin>.

### Run it with Docker

```bash
docker build -t conduit .
docker run -p 9812:9812 \
  -e ADMIN_PASSWORD="a-strong-password" \
  -e JWT_SECRET="$(openssl rand -hex 32)" \
  -v "$PWD/data:/data" \
  conduit
```

---

## Connecting clients

Conduit accepts the gateway token in either of two places, so it works with
every SDK out of the box:

```
Authorization: Bearer sk-...
x-api-key: sk-...
```

### Claude Code

Claude Code speaks the Anthropic Messages API. Point it at Conduit:

```bash
export ANTHROPIC_BASE_URL="http://localhost:9812"
export ANTHROPIC_AUTH_TOKEN="sk-..."          # the Conduit gateway token
```

```bash
claude
```

Two details make this work:

- Claude Code asks for model ids like `claude-sonnet-4-5-20250929`. Conduit
  ships a `claude-*` family alias that resolves those to the Claude model
  available on the upstream, so you are not forced to pick a model by hand.
- Conduit implements `POST /v1/messages/count_tokens`, the endpoint Claude
  Code calls before every turn to decide whether the conversation still fits
  the context window.

To pin a specific model:

```bash
export ANTHROPIC_MODEL="claude-haiku-5-5"
```

### Anthropic SDK

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="http://localhost:9812",
    api_key="sk-...",       # the Conduit gateway token
)

message = client.messages.create(
    model="claude-haiku-5-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Why is the sky blue?"}],
)
print(message.content[0].text)
```

### OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:9812/v1",
    api_key="sk-...",
)

stream = client.chat.completions.create(
    model="glm-5.3",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

### opencode

```jsonc
// opencode.json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "conduit-chat": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Conduit (Chat)",
      "options": {
        "baseURL": "http://localhost:9812/v1",
        "apiKey": "{env:CONDUIT_TOKEN}"
      },
      "models": {
        "glm-5.3": { "name": "GLM-5.3" },
        "kimi-k2.7-code": { "name": "Kimi K2.7 Code" }
      }
    },
    "conduit-messages": {
      "npm": "@ai-sdk/anthropic",
      "name": "Conduit (Messages)",
      "options": {
        "baseURL": "http://localhost:9812",
        "apiKey": "{env:CONDUIT_TOKEN}",
        "headers": { "anthropic-version": "2023-06-01" }
      },
      "models": {
        "minimax-m3": { "name": "MiniMax M3" },
        "qwen3.7-plus": { "name": "Qwen3.7 Plus" }
      }
    }
  },
  "model": "conduit-chat/glm-5.3"
}
```

> For the Anthropic provider, point `baseURL` at the gateway root without
> `/v1` — the SDK appends `/v1/messages` itself. For the OpenAI-compatible
> provider, include `/v1`.

### Codex

```toml
# ~/.codex/config.toml
model = "glm-5.3"
model_provider = "conduit"

[model_providers.conduit]
name = "Conduit"
base_url = "http://localhost:9812/v1"
env_key = "CONDUIT_TOKEN"
```

### curl

```bash
curl http://localhost:9812/v1/messages \
  -H "x-api-key: sk-..." \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-haiku-5-5",
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

---

## Model routing

Conduit resolves a client model id through four steps, in order:

1. **Exact mapping** — `MODEL_MAPPINGS` rewrites the id before anything else.
2. **Route lookup** — a model registered in the catalogue resolves to its
   configured upstream, protocol and key group.
3. **Family alias** — a rule like `claude-*` catches ids that are not
   registered under their own name. The longest matching prefix wins, and a
   registered model always beats an alias.
4. **Fallback** — `MODEL_FALLBACK` serves one model for anything still
   unknown. Leave it empty to reject unknown models with `404`.

If none of these match, the request is rejected with `model_not_found`
unless `PASSTHROUGH_MODE=go` is set, which forwards unknown models as-is.

### Exact mappings

```bash
MODEL_MAPPINGS='{"gpt-5.5":"glm-5.3","gpt-5.5-mini":"glm-5.3-flash"}'
```

or from a file:

```bash
MODEL_MAPPING_FILE=./config/model-mapping.example.json
```

### Family aliases

```bash
MODEL_ALIASES='{"claude-*":"claude-haiku-5-5"}'
```

### Fallback

```bash
MODEL_FALLBACK=claude-haiku-5-5
```

All three are editable from the admin panel under **Model Mappings** and are
persisted in SQLite.

### Model catalogue

The catalogue is discovered, not hardcoded. At startup and every six hours
afterwards, Conduit fetches the routable upstream catalogues and enriches them
with metadata (context length, architecture, supported parameters, pricing,
capability tags) from OpenRouter.

| Upstream | Models |
| --- | --- |
| OpenCode Go | `glm-5.3`, `glm-5.3-flash`, `kimi-k3`, `kimi-k2.7-code`, `claude-haiku-5-5`, `minimax-m3`, `qwen3.8-max`, `deepseek-v4-pro`, `grok-4.7`, `gpt-6-luna`, and more |
| Ollama Cloud | the models that upstream exposes |

Admins can disable a route, edit its metadata and pricing, or add their own
routes. Manual edits are recorded as customised fields and survive later
automatic syncs.

---

## Configuration

Conduit reads its configuration from the environment, and from a `.env` file
in the working directory if one exists. See [`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `9812` | TCP port to listen on |
| `ADMIN_PASSWORD` | — | Admin panel password. Startup fails if left at `admin` |
| `JWT_SECRET` | — | JWT signing key. Must be ≥ 32 bytes. Startup fails on the default |
| `DB_PATH` | `./data/conduit.db` | SQLite database file |
| `GO_BASE_URL` | `https://opencode.ai/zen/go` | OpenCode Go upstream |
| `GO_API_KEY` | — | OpenCode Go API key. Auto-added to the "go" key pool on startup. Optional — you can also add keys through the admin panel |
| `OLLAMA_BASE_URL` | `https://ollama.com` | Ollama Cloud upstream |
| `MODEL_MAPPINGS` | — | JSON map of client model → upstream model |
| `MODEL_MAPPING_FILE` | — | Path to a JSON file with the same shape |
| `MODEL_ALIASES` | — | JSON map of family aliases, e.g. `{"claude-*":"claude-haiku-5-5"}` |
| `MODEL_FALLBACK` | — | Model to serve for unknown ids. Empty means reject with 404 |
| `GROUP_MULTIPLIERS` | `1.0` | Per-group cost multipliers, JSON or `group=1.2` list |
| `UPSTREAM_TIMEOUT` | `0` | Non-streaming upstream timeout in seconds. `0` disables it |
| `PASSTHROUGH_MODE` | `disabled` | `disabled` rejects unknown models, `go` forwards them |

### Command line

```
conduit [serve] [flags]   start the gateway (default)
conduit version           print the build version
conduit help              show usage

  --port int        TCP port to listen on (default: PORT env, then 9812)
  --bind string     interface to bind (default: all interfaces)
  --no-model-sync   skip the upstream model catalog sync at startup
```

---

## Public API

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/health` | no | Liveness, version, uptime, key pool summary |
| `GET` | `/metrics` | no | Prometheus exposition |
| `GET` | `/v1/models` | no | Enabled model catalogue |
| `POST` | `/v1/chat/completions` | yes | OpenAI Chat Completions |
| `POST` | `/v1/messages` | yes | Anthropic Messages |
| `POST` | `/v1/messages/count_tokens` | yes | Anthropic token counting |
| `POST` | `/v1/responses` | yes | OpenAI Responses |

Errors use a consistent envelope:

```json
{
  "error": {
    "type": "model_not_found",
    "message": "model not found: claude-sonnet-4-5"
  }
}
```

---

## Admin panel

Open `http://<gateway>/admin` and sign in with `ADMIN_PASSWORD`.

- **Dashboard** — total, today and last-hour calls, success rate, RPM/TPM/QPS,
  p50/p95/p99 latency, a 24-hour timeline, calls by model and by protocol, token
  breakdown by kind, cost totals, and a recent-call log.
- **API Keys** — add, edit, toggle and delete upstream keys; set a weight or a
  proxy URL; reset a cooldown; import OpenCode Go keys through GitHub
  auto-login; and look up live rolling, weekly and monthly quota.
- **Tokens** — create, edit, copy and delete `sk-` gateway tokens with a name,
  description, rate limit, total request cap, expiry and group scope.
- **Models** — sync the catalogue, configure primary and failover upstreams,
  enable or disable routes, edit metadata and pricing, and rebuild the
  catalogue atomically.
- **Model Mappings** — manage exact mappings, family aliases and the fallback.
- **Usage logs** — paginated history with filters for model, protocol, token,
  group, status, streaming and time range, plus a CSV export.

The panel is a Vue 3 SPA served as plain ES modules. There is no build step,
no npm install and no bundler in the loop — the binary embeds the assets.

Its two third-party libraries, Vue and Chart.js, are vendored under
`web/vendor/` and embedded in the binary. The panel makes **no outbound
requests**: it works offline, behind an air gap, and without inheriting
someone else's CDN as a supply-chain dependency.

The visual system follows Apple's own: the iOS system palette, hairline
separators, translucency on the top bar and sidebar, and motion kept to a
few hundred milliseconds. It is a quiet application, not a light show.

---

## Admin REST API

Every endpoint except `login` requires `Authorization: Bearer <admin-jwt>`.
Obtain one with `POST /admin/login` and the admin password; the token is
valid for twelve hours.

| Method & path | Description |
| --- | --- |
| `POST /admin/login` | Exchange the admin password for a JWT |
| `GET /admin/health` | Key pool health: enabled, disabled, cooldowns |
| `GET /admin/version` | Running version and latest GitHub release |
| `GET /admin/keys` · `POST /admin/keys` | List and create upstream keys |
| `POST /admin/keys/import-github` | Auto-login through GitHub and import Go keys |
| `PATCH /admin/keys/:id` | Edit a key |
| `POST /admin/keys/:id/toggle` | Enable or disable a key |
| `POST /admin/keys/:id/reset` | Clear a key's cooldown |
| `GET /admin/keys/:id/quota` | Look up live Go plan quota |
| `DELETE /admin/keys/:id` | Delete a key |
| `GET /admin/tokens` · `POST /admin/tokens` | List and create gateway tokens |
| `PATCH /admin/tokens/:id` · `DELETE /admin/tokens/:id` | Edit and delete a token |
| `GET /admin/stats` | Dashboard aggregates |
| `GET /admin/usage` | Paginated, filterable usage logs with a summary |
| `GET /admin/usage.csv` | Usage logs as CSV |
| `GET /admin/backup` | Export the whole configuration as JSON |
| `POST /admin/restore` | Restore a configuration export |
| `GET /admin/models` · `POST /admin/models` | List and create model routes |
| `PATCH /admin/models/:id` | Edit a route |
| `POST /admin/models/:id/toggle` | Enable or disable a route |
| `DELETE /admin/models/:id` | Delete a route |
| `POST /admin/models/sync` | Incremental catalogue sync |
| `POST /admin/models/rebuild` | Atomic catalogue rebuild |
| `GET /admin/model-mappings` · `POST /admin/model-mappings` | List and create mapping rules |
| `DELETE /admin/model-mappings/:source` | Delete a mapping rule |

> The backup export contains upstream key values, gateway tokens and session
> cookies. Treat it as a secret.

---

## Python SDK

A typed Python client for both the gateway and the admin API, in `python/`.

```bash
pip install -e python/
```

```python
from conduit import Client

client = Client(base_url="http://localhost:9812", api_key="sk-...")

# Anthropic Messages
message = client.messages.create(
    model="claude-haiku-5-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
)
print(message.content[0].text)

# OpenAI Chat Completions, streaming
for chunk in client.chat.completions.create(
    model="glm-5.3",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

An async client with the same surface is available as `AsyncClient`, and
`AdminClient` covers key, token, route and usage management.

The SDK also ships a `conduit-cli` command for quick calls from a terminal:

```bash
conduit-cli models
conduit-cli ask claude-haiku-5-5 "why is the sky blue?"
```

See [`python/README.md`](python/README.md) for the full API.

---

## Desktop app

The admin panel is also available as a native macOS application. The desktop
app runs the gateway in-process on a loopback port and opens a native window
pointed at the admin panel, already signed in. It generates its own admin
credentials on first launch, so there is no password prompt.

```bash
cd desktop
wails build
```

The bundle lands in `desktop/build/bin/conduit.app`. See
[`desktop/README.md`](desktop/README.md).

---

## Observability

### Prometheus

```bash
curl http://localhost:9812/metrics
```

```promql
# request rate by model
sum by (model) (rate(conduit_model_requests_total[5m]))

# error ratio
rate(conduit_request_errors_total[5m]) / rate(conduit_requests_total[5m])
```

Exported series: `conduit_uptime_seconds`, `conduit_requests_total`,
`conduit_request_errors_total`, `conduit_inflight_requests`,
`conduit_responses_total{status}`, `conduit_model_requests_total{model}` and
`conduit_model_failures_total{model}`.

### Request IDs

Every response carries an `X-Request-Id` header. Send your own and Conduit
will preserve it, which makes correlating client logs with gateway logs
straightforward.

---

## Deployment

### Zeabur

The repository ships a `zeabur.json` that builds the image, exposes port
`3000` and mounts a persistent volume at `/data`.

1. Create a new project and deploy from this repository.
2. Set `ADMIN_PASSWORD` and `JWT_SECRET` as service variables.
3. Set `DB_PATH=/data/conduit.db`.
4. Open the domain and append `/admin`.

### Docker

See [Quick start](#quick-start).

### Behind a reverse proxy

Conduit streams SSE responses, so the proxy must not buffer them:

```nginx
location / {
    proxy_pass http://127.0.0.1:9812;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_read_timeout 3600s;
}
```

---

## Development

```bash
git clone https://github.com/serbansorin/conduit.git
cd conduit
go mod download
go test ./...
go build -o conduit .
```

Run the gateway against a local database:

```bash
ADMIN_PASSWORD=dev JWT_SECRET=$(openssl rand -hex 32) go run . serve
```

### Layout

```
api/          public API: routing, auth, proxying, protocol conversion
admin/        admin REST API and the panel's data endpoints
config/       runtime configuration, model routes, mapping rules
internal/     router (model resolution) and session helpers
modelsync/    upstream catalogue discovery and enrichment
pool/         key picking, cooldowns, token validation
protocol/     Chat / Messages / Responses encoding and conversion
server/       server bootstrap shared by the CLI and the desktop app
store/        SQLite schema and queries
upstream/     HTTP client and transport cache
web/          the admin panel (embedded, no build step)
python/       Python SDK
desktop/      Wails desktop application
```

---

## Agent crew

`agentic-crew/` is a file-based agent crew. The orchestrator is code; every
specialist is a YAML file in `agents/`, so adding one is a file drop rather
than a code change. It ships with five agents for the GetUBetter platform and
sixty-three teacher agents for the EPiC master.

```bash
cd agentic-crew
pip install -e ../python -e ".[dev]"
export CONDUIT_BASE_URL="http://localhost:9812"
export CONDUIT_API_KEY="sk-..."
agentic-crew serve
```

See [`agentic-crew/README.md`](agentic-crew/README.md) and
[`docs/improving-coding-agents.md`](docs/improving-coding-agents.md).

## License

[AGPL-3.0](LICENSE).

---

<div align="center">
<sub>Conduit — one endpoint, every model.</sub>
</div>
