---
name: conduit-testing
description: >-
  Verify a change in the Conduit repository. Use when running or fixing tests,
  the race detector, gofmt checks, the end-to-end smoke script, the Python SDK
  suite or the GitHub Actions workflow. Triggers on go test, race detector,
  gofmt, smoke test, pytest, CI failure, ci.yml or writing a mock upstream
  test.
metadata:
  category: development
  suggest_for:
    filename:
      - '*_test.go'
      - '*.sh'
      - '*.yml'
---

# Conduit Testing

## The commands, from the repository root

```bash
export PATH="/opt/homebrew/bin:$PATH"

go build ./...          # compile everything
go vet ./...            # CI runs this
go test ./...           # fast unit tests
go test -race ./...     # what CI runs
gofmt -l .              # must print nothing
./scripts/smoke.sh      # end to end against a real server
```

The Python SDK lives in its own directory:

```bash
cd python && python3 -m pytest tests/ -q
```

The agent framework is a third distribution:

```bash
cd agentic-crew && python3 -m pytest tests/ -q
```

It needs the SDK installed from the sibling directory first:

```bash
cd agentic-crew && python3 -m pip install -e ../python
```

The desktop app is a separate Go module and has its own build:

```bash
cd desktop && wails build
```

Do not run `go test ./...` from `desktop/`. The root module does not include
the desktop directory, and the desktop module has no tests of its own.

## What CI does

`.github/workflows/ci.yml`. It triggers on push and pull request to `main`
and has four jobs: `test`, `docker`, `python` and `crew`.

### Job `test`

On `ubuntu-latest`, with Go `1.25`:

1. `go build -v ./...`
2. `go vet ./...`
3. The formatting check:

```yaml
- name: Check formatting
  run: |
    unformatted=$(gofmt -l . | grep -v '^desktop/' || true)
    if [ -n "$unformatted" ]; then
      echo "These files need gofmt:"
      echo "$unformatted"
      exit 1
    fi
```

Note the `grep -v '^desktop/'`. The desktop module is excluded from the
formatting gate, so `gofmt -l .` on the root will list `desktop/...` paths
that CI does not fail on. Format the root module files, and run `gofmt -l .`
inside `desktop/` if you touch it.

4. `go test -race ./...`

The race detector is the gate. A test that only passes without `-race` is a
failed test.

### Job `docker`

Builds the image with `docker/build-push-action@v6`, context `.`, tag
`conduit:ci`, `push: false`. It exists to catch a broken `Dockerfile`, not to
publish anything.

The `Dockerfile` builds with `CGO_ENABLED=1 GOOS=linux`, because the SQLite
driver needs cgo. It sets `DB_PATH=/data/conduit.db`, `PORT=3000` and a
`HEALTHCHECK` against `/health`. The root module must keep building inside
the Alpine image, which is one reason Wails must never be added to the root
`go.mod`.

### Job `python`

Matrix over Python `3.9`, `3.12` and `3.13`, with `working-directory: python`:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
```

`fail-fast: false`, so one Python version failing does not cancel the others.
The SDK must keep working on 3.9.

### Job `crew`

The agent framework, with `working-directory: agentic-crew`, on Python 3.12
only:

```bash
python -m pip install --upgrade pip
python -m pip install -e ../python
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
python tools/make_teacher.py --help
```

The SDK is installed from the local `../python` directory first, because the
crew depends on the `conduit-ai-gateway` distribution by name and that
distribution is not published to PyPI.

## The test inventory

| Package | Test files |
| --- | --- |
| `api/` | `gateway_test.go`, `proxy_test.go`, `ratelimit_test.go`, `usage_timing_test.go` |
| `admin/` | `router_test.go`, `quota_test.go`, `ollama_quota_test.go`, `github_import_test.go`, `seroval_test.go` |
| `config/` | `model_mapping_test.go`, `models_test.go` |
| `internal/router/` | `router_test.go` |
| `internal/session/` | `session_test.go` |
| `internal/stream/` | `pipe_test.go` |
| `modelsync/` | `sync_test.go` |
| `pool/` | `key_test.go`, `token_test.go` |
| `protocol/` | `convert_test.go`, `stream_convert_r3_test.go`, `stream_convert_r5_test.go` |
| `store/` | `sqlite_test.go` |
| `version/` | `version_test.go` |
| `web/` | `embed_test.go` |

Python: `python/tests/` holds `test_client.py`, `test_async_client.py`,
`test_admin.py`, `test_cli.py`, `test_errors.py` and `conftest.py`, plus a
`fixtures/` directory with the JSON and SSE payloads the suite replays.

Where a test goes:

- A pure function or a helper -> a unit test next to the code.
  `api/proxy_test.go` holds the helper level tests for `copyForwardHeaders`,
  `injectUpstreamAuth` and friends.
- A route, a handler or the proxy as a whole -> `api/gateway_test.go`.
- A store or pool behaviour that needs a database -> `store/sqlite_test.go` or
  `pool/key_test.go`.

## The mock upstream pattern

`api/gateway_test.go` is the reference. It boots a fake upstream with
`httptest`, points the gateway at it, and asserts on what the upstream
received.

```go
// capturedRequest records what the mock upstream received.
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

func (c *capturedRequest) snapshot() (http.Header, string, string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.headers.Clone(), c.path, c.body
}
```

The mock upstream itself:

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

Setup helpers:

```go
// setupGatewayTestDB initialises an isolated database for a test.
func setupGatewayTestDB(t *testing.T) {
	t.Helper()
	if err := store.InitForTest("file:api_gateway_test?mode=memory&cache=shared"); err != nil {
		t.Fatalf("init test db: %v", err)
	}
}

// registerTestModel installs a model route pointing at the mock upstream.
func registerTestModel(t *testing.T, id string) {
	t.Helper()
	config.ReplaceModels([]config.ModelRoute{{
		ID:        id,
		Name:      id,
		Upstream:  config.UpstreamGo,
		Protocol:  config.ProtocolMessages,
		RealModel: id,
		Group:     "go",
		Status:    config.ModelStatusPtr(config.ModelStatusEnabled),
	}})
}

// insertTestKey stores an enabled upstream key in the test database.
func insertTestKey(t *testing.T, value string) {
	t.Helper()
	k := &store.Key{
		Value:   value,
		Group:   "go",
		Label:   "test",
		Enabled: true,
		Weight:  1,
	}
	if err := store.DB().Create(k).Error; err != nil {
		t.Fatalf("insert key: %v", err)
	}
}
```

The full test shape:

```go
func TestProxySendsSessionHeaderUpstream(t *testing.T) {
	setupGatewayTestDB(t)

	captured := &capturedRequest{}
	upstream := newMockUpstream(t, captured)

	originalBase := config.Get().GoBaseURL
	config.Get().GoBaseURL = upstream.URL
	t.Cleanup(func() { config.Get().GoBaseURL = originalBase })

	insertTestKey(t, "test-key")
	registerTestModel(t, "minimax-m3")
	tok, err := pool.CreateToken("test-token", "", 0, nil)
	if err != nil {
		t.Fatalf("create token: %v", err)
	}

	gin.SetMode(gin.TestMode)
	router := NewRouter(pool.NewPicker())

	body := `{"model":"minimax-m3","max_tokens":16,"messages":[{"role":"user","content":"hello"}]}`
	req := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+tok.Token)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", rec.Code, rec.Body.String())
	}

	headers, path, _ := captured.snapshot()
	if path != "/v1/messages" {
		t.Fatalf("upstream path = %q, want /v1/messages", path)
	}
	if got := headers.Get("X-Opencode-Session"); got == "" {
		t.Fatalf("upstream request is missing %s; headers=%v", "X-Opencode-Session", headers)
	}
}
```

Points worth copying:

- `store.InitForTest` uses a shared-cache in-memory SQLite DSN. Give each test
  its own DSN name when tests run in parallel, otherwise they share a table.
- `config.ReplaceModels` replaces the whole registry, so a test never leaks a
  route into the next one.
- The upstream base URL is restored with `t.Cleanup`. `config.Get()` returns
  a pointer to the live config, so mutating `GoBaseURL` is enough to redirect
  the gateway.
- `gin.SetMode(gin.TestMode)` keeps the output quiet.
- A test asserts on the upstream side (`captured.snapshot()`) as well as on
  the client side (`rec.Code`, `rec.Body`).
- Failures print the body: `t.Fatalf("status = %d, want 200; body=%s",
  rec.Code, rec.Body.String())`.

The session header test is the one to keep. The upstream rejects a request
with no session header, so a regression there breaks every call.

## The smoke script

`./scripts/smoke.sh` builds the binary, starts a real server on port 19812
with a throwaway database, and exercises the endpoints with curl.

What it does, in order:

1. `go build -o "$WORK/conduit" .` into a temp dir.
2. Starts the server with `ADMIN_PASSWORD=smoke-password`, a random
   `JWT_SECRET`, `DB_PATH` in the temp dir, `PORT=19812` and
   `serve --no-model-sync`. The server log goes to a file.
3. Polls `/health` up to 50 times, 0.2s apart.
4. Scrapes the bootstrap token out of the server log with
   `grep -oE 'sk-[A-Za-z0-9]+'`. That is why the server prints the bootstrap
   token banner on a fresh database.
5. Runs the checks with a `check` helper that does a substring match and
   collects failures in `FAILED`.
6. Cleans up the server and the temp dir through an `EXIT` trap.

The checks:

| Check | Expectation |
| --- | --- |
| `GET /health` | contains `"service":"conduit"` and `"version"` |
| `GET /v1/models` | contains `"object":"list"` |
| `GET /metrics` | contains `conduit_uptime_seconds` |
| `POST /v1/messages/count_tokens` with `x-api-key` | contains `"input_tokens"` |
| `POST /v1/messages` without a token | HTTP 401 |
| `POST /admin/login` | returns a JWT |
| `GET /admin/backup` | contains `"version"` and `"keys"` |
| `GET /admin/usage.csv` | first line contains `request_id` |
| `GET /admin/keys` | contains `"data"` |
| `POST /v1/messages` with an unknown model | HTTP 404 |
| `GET /health` response headers | contain `X-Request-Id` |

It exits 1 and dumps the server log on the first failure set.

`--no-model-sync` matters. Without it the server tries to reach the upstream
catalog APIs on startup, which makes the script slow and network dependent.
The script is not in CI, so run it locally before you call a change done.

## The Python SDK tests

```bash
cd python && python3 -m pytest tests/ -q
```

The suite uses `pytest`, `httpx` and `pytest-asyncio`. `conftest.py` provides
the shared fixtures. The tests replay recorded payloads from
`python/tests/fixtures/`: `chat_completion.json`, `chat_stream.txt`,
`message.json`, `messages_stream.txt`, `response.json`,
`responses_stream.txt`, `count_tokens.json`, `models.json`, `health.json`,
`admin_login.json`, `admin_keys.json`, `admin_models.json`,
`admin_stats.json`, `admin_tokens.json`, `admin_usage.json`, and the error
fixtures `error_401.json`, `error_403.json`, `error_404.json`,
`error_429.json` and `error_500.json`.

When you change the SDK, add a test in the matching file:

| Change | Test file |
| --- | --- |
| sync client behaviour | `test_client.py` |
| async client behaviour | `test_async_client.py` |
| admin client behaviour | `test_admin.py` |
| the `conduit-cli` command | `test_cli.py` |
| the exception hierarchy | `test_errors.py` |

To match CI exactly, install the dev extra first:

```bash
cd python && python3 -m pip install -e ".[dev]" && python3 -m pytest tests/ -q
```

## Conventions the tests follow

- Errors are wrapped with `%w` and never swallowed. A test that swallows an
  error hides the bug it is meant to catch.
- Test names say what is proven, not what is called.
  `TestProxySendsSessionHeaderUpstream` proves a property;
  `TestProxyForwardsClientSessionHeader` proves a property.
- `t.Helper()` on every setup helper.
- `t.Cleanup` for teardown, not `defer`, so the cleanup also runs after a
  `t.Fatalf`.
- Assert on the exact value when the exact value matters. Substring matching
  is for payload shapes, not for a status code.

## Traps

- `go test ./...` from the repository root does not cover `desktop/`, which
  is a separate module.
- `gofmt -l .` from the root lists `desktop/` files that CI ignores. The gate
  is `gofmt -l . | grep -v '^desktop/'`.
- CI pins Go `1.25`, the local toolchain is newer. Do not use a language
  feature that the `go 1.25.0` directive in `go.mod` does not allow.
- The CI Python matrix starts at 3.9. No `match` statements, no `X | Y` type
  syntax at runtime unless `from __future__ import annotations` is present.
- `scripts/smoke.sh` needs port 19812 free. It also needs `openssl` and
  `curl` on the PATH.
- `config.Get()` returns a live pointer. A test that mutates `GoBaseURL`
  without restoring it breaks every later test in the package.

## The order to run things in

```bash
export PATH="/opt/homebrew/bin:$PATH"
gofmt -l . | grep -v '^desktop/'   # format first, it is the cheapest gate
go vet ./...
go test -race ./...
./scripts/smoke.sh
cd python && python3 -m pytest tests/ -q
cd ../agentic-crew && python3 -m pytest tests/ -q
```
