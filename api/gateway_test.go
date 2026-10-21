package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/serbansorin/conduit/config"
	"github.com/serbansorin/conduit/pool"
	"github.com/serbansorin/conduit/store"
)

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

// newMockUpstream starts a fake upstream that records the request it received.
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

// TestProxySendsSessionHeaderUpstream proves the gateway always attaches a
// session header the upstream recognises. The upstream rejects requests
// without one, so a missing header breaks every call.
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

// TestProxyForwardsClientSessionHeader proves a client-supplied session id
// survives the hop unchanged, so the upstream keeps a conversation pinned to
// the same instance.
func TestProxyForwardsClientSessionHeader(t *testing.T) {
	setupGatewayTestDB(t)

	captured := &capturedRequest{}
	upstream := newMockUpstream(t, captured)

	originalBase := config.Get().GoBaseURL
	config.Get().GoBaseURL = upstream.URL
	t.Cleanup(func() { config.Get().GoBaseURL = originalBase })

	insertTestKey(t, "test-key-2")
	registerTestModel(t, "minimax-m3")
	tok, err := pool.CreateToken("test-token-2", "", 0, nil)
	if err != nil {
		t.Fatalf("create token: %v", err)
	}

	gin.SetMode(gin.TestMode)
	router := NewRouter(pool.NewPicker())

	body := `{"model":"minimax-m3","max_tokens":16,"messages":[{"role":"user","content":"hello"}]}`
	req := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+tok.Token)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Session-Id", "client-conversation-42")
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", rec.Code, rec.Body.String())
	}

	headers, _, _ := captured.snapshot()
	if got := headers.Get("X-Session-Id"); got != "client-conversation-42" {
		t.Fatalf("X-Session-Id = %q, want %q", got, "client-conversation-42")
	}
	if got := headers.Get("X-Opencode-Session"); got != "client-conversation-42" {
		t.Fatalf("%s = %q, want %q", "X-Opencode-Session", got, "client-conversation-42")
	}
}

// TestProxyReplacesGenericUserAgent proves the gateway identifies itself
// instead of forwarding a bare HTTP library default, which the upstream
// refuses to route efficiently.
func TestProxyReplacesGenericUserAgent(t *testing.T) {
	setupGatewayTestDB(t)

	captured := &capturedRequest{}
	upstream := newMockUpstream(t, captured)

	originalBase := config.Get().GoBaseURL
	config.Get().GoBaseURL = upstream.URL
	t.Cleanup(func() { config.Get().GoBaseURL = originalBase })

	insertTestKey(t, "test-key-3")
	registerTestModel(t, "minimax-m3")
	tok, err := pool.CreateToken("test-token-3", "", 0, nil)
	if err != nil {
		t.Fatalf("create token: %v", err)
	}

	gin.SetMode(gin.TestMode)
	router := NewRouter(pool.NewPicker())

	body := `{"model":"minimax-m3","max_tokens":16,"messages":[{"role":"user","content":"hello"}]}`
	req := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+tok.Token)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "python-httpx/0.27.0")
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", rec.Code, rec.Body.String())
	}

	headers, _, _ := captured.snapshot()
	ua := headers.Get("User-Agent")
	if strings.HasPrefix(strings.ToLower(ua), "python-httpx") {
		t.Fatalf("upstream user agent = %q, want a replacement for the generic default", ua)
	}
	if !strings.HasPrefix(ua, "conduit/") {
		t.Fatalf("upstream user agent = %q, want it to start with conduit/", ua)
	}
}

// TestCountTokensEndpoint checks the Anthropic-compatible token counter that
// Claude Code calls before every turn.
func TestCountTokensEndpoint(t *testing.T) {
	setupGatewayTestDB(t)

	tok, err := pool.CreateToken("count-token", "", 0, nil)
	if err != nil {
		t.Fatalf("create token: %v", err)
	}

	gin.SetMode(gin.TestMode)
	router := NewRouter(pool.NewPicker())

	body := `{"model":"claude-sonnet-4-5","messages":[{"role":"user","content":"hello world"}]}`
	req := httptest.NewRequest(http.MethodPost, "/v1/messages/count_tokens", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+tok.Token)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", rec.Code, rec.Body.String())
	}

	var out struct {
		InputTokens int `json:"input_tokens"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("decode response: %v; body=%s", err, rec.Body.String())
	}
	if out.InputTokens <= 0 {
		t.Fatalf("input_tokens = %d, want a positive count", out.InputTokens)
	}
}

// TestHealthReportsServiceIdentity checks the liveness payload.
func TestHealthReportsServiceIdentity(t *testing.T) {
	setupGatewayTestDB(t)

	gin.SetMode(gin.TestMode)
	router := NewRouter(pool.NewPicker())

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	var out map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if out["service"] != "conduit" {
		t.Fatalf("service = %v, want conduit", out["service"])
	}
	if out["version"] == nil {
		t.Fatalf("health payload is missing the version: %s", rec.Body.String())
	}
}

// TestMetricsEndpoint checks the Prometheus exposition.
func TestMetricsEndpoint(t *testing.T) {
	setupGatewayTestDB(t)

	gin.SetMode(gin.TestMode)
	router := NewRouter(pool.NewPicker())

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	body := rec.Body.String()
	for _, want := range []string{"conduit_uptime_seconds", "conduit_requests_total", "conduit_inflight_requests"} {
		if !strings.Contains(body, want) {
			t.Fatalf("metrics output is missing %q:\n%s", want, body)
		}
	}
}

// TestRequestIDHeader proves every response carries a request identifier.
func TestRequestIDHeader(t *testing.T) {
	setupGatewayTestDB(t)

	gin.SetMode(gin.TestMode)
	router := NewRouter(pool.NewPicker())

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Header().Get("X-Request-Id") == "" {
		t.Fatal("response is missing the X-Request-Id header")
	}
}
