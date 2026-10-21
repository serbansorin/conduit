# Conduit — opencode agent context

Multi-key LLM gateway. Pools upstream API keys behind one token and speaks
the OpenAI, Anthropic and Responses protocols interchangeably.

## What this is, and what it is not

- **Conduit** is this project. A Go binary, an embedded admin panel, a Python
  SDK and a desktop app.
- **OpenCode Go** (`opencode.ai/zen/go`) is the *upstream service* Conduit
  proxies to. It is a separate product by OpenCode. Never rename it, never
  conflate it with Conduit.

## Layout

| Path | What |
| --- | --- |
| `main.go` | CLI entry point. `conduit serve \| version \| help` |
| `server/` | server bootstrap shared by the CLI and the desktop app |
| `api/` | public API: routing, auth, rate limits, the proxy, protocol conversion |
| `admin/` | admin REST API and the panel's data endpoints |
| `config/` | runtime config, model routes, mapping rules, family aliases |
| `internal/router/` | model resolution (mapping → registry → alias → fallback) |
| `internal/session/` | upstream session header resolution |
| `protocol/` | Chat / Messages / Responses encoding, decoding and conversion |
| `pool/` | key picking, cooldowns, token validation |
| `store/` | SQLite schema and queries (GORM) |
| `modelsync/` | upstream catalogue discovery and enrichment |
| `upstream/` | HTTP client and transport cache |
| `web/` | the admin panel — plain ES modules, no build step |
| `python/` | the Python SDK (`conduit-ai-gateway`) |
| `desktop/` | the Wails v2 macOS app — a separate Go module |
| `agentic-crew/` | the agent crew framework |

## Commands

```bash
go build ./...                  # build the gateway
go test ./...                   # unit tests
go test -race ./...             # what CI runs
./scripts/smoke.sh              # end-to-end against a real server
gofmt -l .                      # must be empty; CI fails otherwise

cd python && python3 -m pytest tests/ -q
cd desktop && wails build       # the macOS app
```

## Conventions

- **No `as any`, no `@ts-ignore`, no type suppression.** Go 1.25, typed all
  the way down.
- Errors are wrapped with `%w` and never swallowed.
- `gofmt` clean. The CI workflow fails on anything else.
- Doc comments on exported identifiers, and on anything whose behaviour is not
  obvious from the signature. A comment that explains a *why* is worth more
  than one that restates the code.
- The admin panel has **no build step**. Plain ES modules in `web/js/`, one
  stylesheet, and the HTML in `web/admin.html`. Adding a file under
  `web/js/pages/` needs no config change; adding one under a *new* subdirectory
  needs a new `//go:embed` pattern in `web/embed.go`.
- The panel's third-party libraries are **vendored** under `web/vendor/` and
  embedded in the binary. Do not add a CDN link.
- The panel is **English only**. The locale catalogue still holds the Chinese
  translations, but the default is English and there is no switcher.

## The upstream contract — read this before touching the proxy

The OpenCode Go upstream **rejects any request without a session header** with
`MissingSessionID`. `internal/session` handles it:

1. A session header from the client (`x-opencode-session`, `x-session-id`,
   `session_id`, `x-conversation-id`) is forwarded untouched.
2. Otherwise an id is derived from the conversation seed, so the upstream can
   reuse its prompt cache between turns.
3. `X-Conduit-Session` overrides both.

Never remove the session header from an upstream request. A request without
one fails at the upstream before it reaches a model.

The upstream also asks clients to identify themselves with a real user agent
rather than a library default — `applyUpstreamUserAgent` in `api/proxy.go`
replaces the generic ones.

## Model resolution

`internal/router.Resolve` tries, in order:

1. an **exact mapping** (`MODEL_MAPPINGS`, or a rule in the admin panel),
2. the **registry** (a route in the database),
3. a **family alias** (`MODEL_ALIASES`, e.g. `claude-*` → `claude-haiku-5-5`),
   which only applies when the model has no route of its own,
4. the **fallback** (`MODEL_FALLBACK`),
5. otherwise `404 model_not_found`, unless `PASSTHROUGH_MODE=go`.

A registered model always beats a family alias.

## Protocol conversion

Everything goes through the intermediate representation in `protocol/`:

```
inbound → decode → IR → encode → upstream
upstream → decode → IR → encode → inbound
```

Same protocol in and out is a passthrough. Cross-protocol requests are
rebuilt from the IR, which means protocol-specific fields that the IR does
not model are dropped. That is a known limitation, not an accident — if you
add a field, add it to the IR as well.

## Gotchas

- **Gateway tokens are stored in plaintext** in SQLite and returned verbatim
  by the admin API, because the panel needs to show them for copying.
- **The desktop app is a separate Go module** on purpose. Wails needs desktop
  toolkit headers that would break the Linux container build of the root
  module. Never add Wails to the root `go.mod`.
- **`config.Load()` runs once**, guarded by a `sync.Once`. Anything that
  changes configuration at runtime — the desktop app setting `DB_PATH` — must
  do it before the first call.
- **The root `.gitignore` has a bare `conduit` entry** for the compiled
  binary. `python/.gitignore` re-includes `python/src/conduit/`. If you add a
  directory named `conduit`, check that it is not being ignored.
- **`.env` is loaded from the working directory** and never overrides an
  environment variable that is already set.

## Adding things

- A model route: see the `conduit-model-routing` skill.
- An API endpoint: see the `conduit-api-endpoint` skill.
- A panel page: see the `conduit-admin-panel` skill.
- An agent for the crew: see `agentic-crew/README.md`.

## Licence

AGPL-3.0. This is a maintained derivative of `opencode-go`; the original
copyright notices are retained in `LICENSE` and in the git history.
