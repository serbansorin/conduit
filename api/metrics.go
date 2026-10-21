package api

import (
	"fmt"
	"net/http"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gin-gonic/gin"
)

// metricsCollector keeps the counters exposed on /metrics.
//
// The counters are process-local: they describe the running instance, not the
// persisted history. Durable usage accounting lives in the database and is
// reported by the admin API.
type metricsCollector struct {
	total    atomic.Int64
	inflight atomic.Int64
	errors   atomic.Int64

	mu       sync.Mutex
	byModel  map[string]*modelCounters
	started  time.Time
	statuses map[int]*atomic.Int64
}

type modelCounters struct {
	requests atomic.Int64
	failures atomic.Int64
}

// newMetricsCollector builds a collector with the counters initialised.
func newMetricsCollector() *metricsCollector {
	m := &metricsCollector{
		byModel:  map[string]*modelCounters{},
		statuses: map[int]*atomic.Int64{},
		started:  time.Now(),
	}
	for _, code := range []int{200, 400, 401, 403, 404, 408, 429, 500, 502, 503} {
		m.statuses[code] = &atomic.Int64{}
	}
	return m
}

// observe records the outcome of one proxied request.
func (m *metricsCollector) observe(model string, status int) {
	m.total.Add(1)
	if counter, ok := m.statuses[status]; ok {
		counter.Add(1)
	} else {
		m.mu.Lock()
		counter, ok := m.statuses[status]
		if !ok {
			counter = &atomic.Int64{}
			m.statuses[status] = counter
		}
		m.mu.Unlock()
		counter.Add(1)
	}
	if status >= 400 {
		m.errors.Add(1)
	}
	if model == "" {
		return
	}
	m.mu.Lock()
	counters, ok := m.byModel[model]
	if !ok {
		counters = &modelCounters{}
		m.byModel[model] = counters
	}
	m.mu.Unlock()
	counters.requests.Add(1)
	if status >= 400 {
		counters.failures.Add(1)
	}
}

// metricsMiddleware counts every request that reaches the proxy endpoints.
func (m *metricsCollector) middleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		m.inflight.Add(1)
		defer m.inflight.Add(-1)
		c.Next()
		model, _ := c.Get("model")
		name, _ := model.(string)
		m.observe(name, c.Writer.Status())
	}
}

// render writes the Prometheus text exposition format.
func (m *metricsCollector) render(w http.ResponseWriter) {
	var b strings.Builder

	b.WriteString("# HELP conduit_uptime_seconds Seconds since the gateway started.\n")
	b.WriteString("# TYPE conduit_uptime_seconds gauge\n")
	fmt.Fprintf(&b, "conduit_uptime_seconds %.0f\n", time.Since(m.started).Seconds())

	b.WriteString("# HELP conduit_requests_total Total proxied requests.\n")
	b.WriteString("# TYPE conduit_requests_total counter\n")
	fmt.Fprintf(&b, "conduit_requests_total %d\n", m.total.Load())

	b.WriteString("# HELP conduit_request_errors_total Proxied requests answered with a 4xx or 5xx status.\n")
	b.WriteString("# TYPE conduit_request_errors_total counter\n")
	fmt.Fprintf(&b, "conduit_request_errors_total %d\n", m.errors.Load())

	b.WriteString("# HELP conduit_inflight_requests Requests currently being served.\n")
	b.WriteString("# TYPE conduit_inflight_requests gauge\n")
	fmt.Fprintf(&b, "conduit_inflight_requests %d\n", m.inflight.Load())

	b.WriteString("# HELP conduit_responses_total Responses by HTTP status code.\n")
	b.WriteString("# TYPE conduit_responses_total counter\n")
	m.mu.Lock()
	codes := make([]int, 0, len(m.statuses))
	for code := range m.statuses {
		codes = append(codes, code)
	}
	sort.Ints(codes)
	statuses := make(map[int]int64, len(codes))
	for _, code := range codes {
		statuses[code] = m.statuses[code].Load()
	}
	m.mu.Unlock()
	for _, code := range codes {
		fmt.Fprintf(&b, "conduit_responses_total{status=\"%d\"} %d\n", code, statuses[code])
	}

	b.WriteString("# HELP conduit_model_requests_total Proxied requests by requested model.\n")
	b.WriteString("# TYPE conduit_model_requests_total counter\n")
	m.mu.Lock()
	models := make([]string, 0, len(m.byModel))
	for name := range m.byModel {
		models = append(models, name)
	}
	sort.Strings(models)
	perModel := make(map[string][2]int64, len(models))
	for _, name := range models {
		perModel[name] = [2]int64{m.byModel[name].requests.Load(), m.byModel[name].failures.Load()}
	}
	m.mu.Unlock()
	for _, name := range models {
		counts := perModel[name]
		fmt.Fprintf(&b, "conduit_model_requests_total{model=\"%s\"} %d\n", escapeLabel(name), counts[0])
		fmt.Fprintf(&b, "conduit_model_failures_total{model=\"%s\"} %d\n", escapeLabel(name), counts[1])
	}

	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(b.String()))
}

// escapeLabel escapes a value used in a Prometheus label.
func escapeLabel(v string) string {
	v = strings.ReplaceAll(v, `\`, `\\`)
	v = strings.ReplaceAll(v, `"`, `\"`)
	v = strings.ReplaceAll(v, "\n", `\n`)
	return v
}
