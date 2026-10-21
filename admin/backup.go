package admin

import (
	"encoding/csv"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/serbansorin/conduit/config"
	"github.com/serbansorin/conduit/pool"
	"github.com/serbansorin/conduit/store"
)

// backupVersion is the schema version of the export format.
const backupVersion = 1

// backupDocument is the portable representation of the gateway configuration.
//
// It contains upstream key values, gateway tokens and session cookies, so the
// export must be treated as a secret.
type backupDocument struct {
	Version    int             `json:"version"`
	ExportedAt time.Time       `json:"exported_at"`
	Keys       []backupKey     `json:"keys"`
	Tokens     []backupToken   `json:"tokens"`
	Models     []backupModel   `json:"models"`
	Mappings   []backupMapping `json:"model_mappings"`
}

type backupKey struct {
	Value       string `json:"value"`
	Group       string `json:"group"`
	Label       string `json:"label"`
	Enabled     bool   `json:"enabled"`
	Weight      int    `json:"weight"`
	ProxyURL    string `json:"proxy_url"`
	Cookie      string `json:"cookie"`
	WorkspaceID string `json:"workspace_id"`
}

type backupToken struct {
	Token         string     `json:"token"`
	Name          string     `json:"name"`
	Description   string     `json:"description"`
	Enabled       bool       `json:"enabled"`
	AllowedGroups string     `json:"allowed_groups"`
	RateLimit     int        `json:"rate_limit"`
	MaxRequests   int        `json:"max_requests"`
	ExpiresAt     *time.Time `json:"expires_at,omitempty"`
}

type backupModel struct {
	ID             string            `json:"id"`
	Name           string            `json:"name"`
	Upstream       string            `json:"upstream"`
	Upstreams      []string          `json:"upstreams,omitempty"`
	Protocol       string            `json:"protocol"`
	RealModel      string            `json:"real_model"`
	Group          string            `json:"group"`
	ContextLen     int               `json:"context_len"`
	Status         int               `json:"status"`
	Priority       int               `json:"priority"`
	Tags           []string          `json:"tags,omitempty"`
	Pricing        map[string]string `json:"pricing,omitempty"`
	UpstreamGroups map[string]string `json:"upstream_groups,omitempty"`
	Targets        map[string]any    `json:"targets,omitempty"`
}

type backupMapping struct {
	SourceModel string `json:"source_model"`
	TargetModel string `json:"target_model"`
}

// exportBackup writes the full gateway configuration as JSON.
//
//	GET /admin/backup
func exportBackup(c *gin.Context) {
	doc, err := buildBackup()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, doc)
}

// buildBackup collects the current configuration into a backup document.
func buildBackup() (*backupDocument, error) {
	doc := &backupDocument{
		Version:    backupVersion,
		ExportedAt: time.Now().UTC(),
		Keys:       []backupKey{},
		Tokens:     []backupToken{},
		Models:     []backupModel{},
		Mappings:   []backupMapping{},
	}

	for _, group := range []string{"go", "ollama"} {
		keys, err := pool.AllByGroup(group)
		if err != nil {
			return nil, fmt.Errorf("load keys (%s): %w", group, err)
		}
		for _, k := range keys {
			doc.Keys = append(doc.Keys, backupKey{
				Value:       k.Value,
				Group:       k.Group,
				Label:       k.Label,
				Enabled:     k.Enabled,
				Weight:      k.Weight,
				ProxyURL:    k.ProxyURL,
				Cookie:      k.Cookie,
				WorkspaceID: k.WorkspaceID,
			})
		}
	}

	tokens, err := pool.AllTokens()
	if err != nil {
		return nil, fmt.Errorf("load tokens: %w", err)
	}
	for _, t := range tokens {
		doc.Tokens = append(doc.Tokens, backupToken{
			Token:         t.Token,
			Name:          t.Name,
			Description:   t.Description,
			Enabled:       t.Enabled,
			AllowedGroups: t.AllowedGroups,
			RateLimit:     t.RateLimit,
			MaxRequests:   t.MaxRequests,
			ExpiresAt:     t.ExpiresAt,
		})
	}

	for _, m := range config.AllModels() {
		status := config.ModelStatusEnabled
		if !m.IsEnabled() {
			status = config.ModelStatusDisabled
		}
		doc.Models = append(doc.Models, backupModel{
			ID:             m.ID,
			Name:           m.Name,
			Upstream:       string(m.Upstream),
			Upstreams:      upstreamNames(m.Upstreams),
			Protocol:       string(m.Protocol),
			RealModel:      m.RealModel,
			Group:          m.Group,
			ContextLen:     m.ContextLen,
			Status:         status,
			Priority:       m.Priority,
			Tags:           m.Tags,
			Pricing:        m.Pricing,
			UpstreamGroups: upstreamGroupMap(m.UpstreamGroups),
			Targets:        upstreamTargetMap(m.Targets),
		})
	}

	rows, err := store.LoadModelMappings()
	if err != nil {
		return nil, fmt.Errorf("load model mappings: %w", err)
	}
	for _, r := range rows {
		doc.Mappings = append(doc.Mappings, backupMapping{
			SourceModel: r.SourceModel,
			TargetModel: r.TargetModel,
		})
	}

	return doc, nil
}

// restoreBackup applies a backup document to the running gateway.
//
// Existing entries are updated in place rather than duplicated, so restoring
// the same export twice is safe.
//
//	POST /admin/restore
func restoreBackup(c *gin.Context) {
	var doc backupDocument
	if err := c.ShouldBindJSON(&doc); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid body: " + err.Error()})
		return
	}
	if doc.Version > backupVersion {
		c.JSON(http.StatusBadRequest, gin.H{
			"error": fmt.Sprintf("unsupported backup version %d (this build understands up to %d)", doc.Version, backupVersion),
		})
		return
	}

	summary := map[string]int{"keys": 0, "tokens": 0, "models": 0, "model_mappings": 0}

	for _, k := range doc.Keys {
		if strings.TrimSpace(k.Value) == "" {
			continue
		}
		row := store.Key{
			Value:       k.Value,
			Group:       defaultString(k.Group, "go"),
			Label:       k.Label,
			Enabled:     k.Enabled,
			Weight:      defaultInt(k.Weight, 1),
			ProxyURL:    k.ProxyURL,
			Cookie:      k.Cookie,
			WorkspaceID: k.WorkspaceID,
		}
		if err := store.DB().Where("value = ?", row.Value).Assign(row).FirstOrCreate(&row).Error; err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "restore key: " + err.Error()})
			return
		}
		summary["keys"]++
	}

	for _, t := range doc.Tokens {
		if strings.TrimSpace(t.Token) == "" {
			continue
		}
		row := store.Token{
			Token:         t.Token,
			Name:          t.Name,
			Description:   t.Description,
			Enabled:       t.Enabled,
			AllowedGroups: t.AllowedGroups,
			RateLimit:     t.RateLimit,
			MaxRequests:   t.MaxRequests,
			ExpiresAt:     t.ExpiresAt,
		}
		if err := store.DB().Where("token = ?", row.Token).Assign(row).FirstOrCreate(&row).Error; err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "restore token: " + err.Error()})
			return
		}
		summary["tokens"]++
	}

	for _, m := range doc.Models {
		if strings.TrimSpace(m.ID) == "" {
			continue
		}
		row := store.NewModelRouteRow(modelFromBackup(m))
		if err := store.SaveModelRoute(&row); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "restore model: " + err.Error()})
			return
		}
		summary["models"]++
	}

	for _, mp := range doc.Mappings {
		if strings.TrimSpace(mp.SourceModel) == "" || strings.TrimSpace(mp.TargetModel) == "" {
			continue
		}
		row := store.ModelMappingRow{SourceModel: mp.SourceModel, TargetModel: mp.TargetModel}
		if err := store.SaveModelMapping(&row); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "restore model mapping: " + err.Error()})
			return
		}
		summary["model_mappings"]++
	}

	// Re-read the persisted state so the running gateway matches the file.
	reloadFromStore()

	c.JSON(http.StatusOK, gin.H{"restored": summary})
}

// reloadFromStore refreshes the in-memory configuration from the database.
func reloadFromStore() {
	if rows, err := store.LoadModelRoutes(); err == nil {
		var routes []config.ModelRoute
		for _, r := range rows {
			if r.Group != "go" && r.Group != "ollama" {
				continue
			}
			routes = append(routes, store.ModelRouteFromRow(r))
		}
		config.ReplaceModels(routes)
	}
	if rows, err := store.LoadModelMappings(); err == nil {
		mappings := map[string]string{}
		for _, r := range rows {
			mappings[r.SourceModel] = r.TargetModel
		}
		if len(mappings) > 0 {
			config.RegisterModelMappings(mappings)
		}
	}
	config.SeedFamilyAliases()
}

// exportUsageCSV writes the usage log as CSV.
//
//	GET /admin/usage.csv?page=1&page_size=200
func exportUsageCSV(c *gin.Context) {
	page := queryInt(c, "page", 1)
	if page < 1 {
		page = 1
	}
	pageSize := queryInt(c, "page_size", 200)
	if pageSize < 1 {
		pageSize = 200
	}
	if pageSize > 10000 {
		pageSize = 10000
	}

	var logs []store.UsageLog
	q := store.DB().Model(&store.UsageLog{})
	if model := strings.TrimSpace(c.Query("model")); model != "" {
		q = q.Where("model = ?", model)
	}
	if token := strings.TrimSpace(c.Query("token_id")); token != "" {
		if id, err := strconv.ParseUint(token, 10, 64); err == nil {
			q = q.Where("token_id = ?", uint(id))
		}
	}
	if err := q.Order("id desc").Limit(pageSize).Offset((page - 1) * pageSize).Find(&logs).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	filename := fmt.Sprintf("conduit-usage-%s.csv", time.Now().UTC().Format("20060102-150405"))
	c.Header("Content-Type", "text/csv; charset=utf-8")
	c.Header("Content-Disposition", `attachment; filename="`+filename+`"`)
	c.Status(http.StatusOK)

	w := csv.NewWriter(c.Writer)
	defer w.Flush()

	_ = w.Write([]string{
		"id", "created_at", "request_id", "model", "protocol", "group", "token_name",
		"status_code", "duration_ms", "first_response_ms", "ttft_ms", "stream",
		"input_tokens", "output_tokens", "reasoning_tokens",
		"cache_read_tokens", "cache_creation_tokens", "total_tokens",
		"total_cost", "actual_cost", "account_cost", "group_multiplier", "error",
	})

	for _, l := range logs {
		_ = w.Write([]string{
			strconv.FormatUint(uint64(l.ID), 10),
			l.CreatedAt.UTC().Format(time.RFC3339),
			l.RequestID,
			l.Model,
			l.Protocol,
			l.Group,
			l.TokenName,
			strconv.Itoa(l.StatusCode),
			strconv.FormatInt(l.DurationMs, 10),
			strconv.FormatInt(l.FirstResponseMs, 10),
			strconv.FormatInt(l.TTFTMs, 10),
			strconv.FormatBool(l.Stream),
			strconv.Itoa(l.InputTokens),
			strconv.Itoa(l.OutputTokens),
			strconv.Itoa(l.ReasoningTokens),
			strconv.Itoa(l.CacheReadTokens),
			strconv.Itoa(l.CacheCreationTokens),
			strconv.Itoa(l.TotalTokens),
			strconv.FormatFloat(l.TotalCost, 'f', -1, 64),
			strconv.FormatFloat(l.ActualCost, 'f', -1, 64),
			strconv.FormatFloat(l.AccountCost, 'f', -1, 64),
			strconv.FormatFloat(l.GroupMultiplier, 'f', -1, 64),
			l.Error,
		})
	}
}

// modelFromBackup converts a backup model entry into a route.
func modelFromBackup(m backupModel) config.ModelRoute {
	route := config.ModelRoute{
		ID:         m.ID,
		Name:       defaultString(m.Name, m.ID),
		Upstream:   config.Upstream(defaultString(m.Upstream, string(config.UpstreamGo))),
		Protocol:   config.Protocol(defaultString(m.Protocol, string(config.ProtocolChat))),
		RealModel:  defaultString(m.RealModel, m.ID),
		Group:      defaultString(m.Group, "go"),
		ContextLen: m.ContextLen,
		Priority:   m.Priority,
		Tags:       m.Tags,
		Pricing:    m.Pricing,
	}
	if m.Status == 0 {
		route.Status = config.ModelStatusPtr(config.ModelStatusDisabled)
	} else {
		route.Status = config.ModelStatusPtr(config.ModelStatusEnabled)
	}
	for _, u := range m.Upstreams {
		route.Upstreams = append(route.Upstreams, config.Upstream(u))
	}
	for name, group := range m.UpstreamGroups {
		if route.UpstreamGroups == nil {
			route.UpstreamGroups = map[config.Upstream]string{}
		}
		route.UpstreamGroups[config.Upstream(name)] = group
	}
	return route
}

// upstreamNames converts the upstream list into plain strings.
func upstreamNames(list []config.Upstream) []string {
	if len(list) == 0 {
		return nil
	}
	out := make([]string, 0, len(list))
	for _, u := range list {
		out = append(out, string(u))
	}
	return out
}

// upstreamGroupMap converts the per-upstream group map into plain strings.
func upstreamGroupMap(m map[config.Upstream]string) map[string]string {
	if len(m) == 0 {
		return nil
	}
	out := make(map[string]string, len(m))
	for k, v := range m {
		out[string(k)] = v
	}
	return out
}

// upstreamTargetMap converts the per-upstream target map into plain JSON.
func upstreamTargetMap(m map[config.Upstream]config.UpstreamTarget) map[string]any {
	if len(m) == 0 {
		return nil
	}
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[string(k)] = v
	}
	return out
}

// defaultString returns value when it is not empty, otherwise fallback.
func defaultString(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

// defaultInt returns value when it is positive, otherwise fallback.
func defaultInt(value, fallback int) int {
	if value <= 0 {
		return fallback
	}
	return value
}
