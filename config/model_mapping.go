package config

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"strings"
	"sync"
)

var (
	modelMappings   = map[string]string{}
	modelAliases    = map[string]string{}
	modelMappingsMu sync.RWMutex
)

// RegisterModelMapping adds or replaces one in-memory model rewrite rule.
//
// A source ending in "*" is a family alias: "claude-*" matches every
// claude-prefixed model id that is not itself a registered route.
func RegisterModelMapping(from, to string) {
	from = strings.TrimSpace(from)
	to = strings.TrimSpace(to)
	if from == "" || to == "" {
		return
	}
	modelMappingsMu.Lock()
	defer modelMappingsMu.Unlock()
	if strings.HasSuffix(from, "*") {
		delete(modelMappings, from)
		modelAliases[from] = to
		return
	}
	delete(modelAliases, from)
	modelMappings[from] = to
}

// RemoveModelMapping deletes one in-memory model rewrite rule.
func RemoveModelMapping(from string) {
	from = strings.TrimSpace(from)
	if from == "" {
		return
	}
	modelMappingsMu.Lock()
	defer modelMappingsMu.Unlock()
	delete(modelMappings, from)
	delete(modelAliases, from)
}

// RegisterModelMappings replaces all in-memory model rewrite rules.
func RegisterModelMappings(m map[string]string) {
	modelMappingsMu.Lock()
	defer modelMappingsMu.Unlock()
	modelMappings = map[string]string{}
	modelAliases = map[string]string{}
	for from, to := range normalizeModelMappings(m) {
		if strings.HasSuffix(from, "*") {
			modelAliases[from] = to
			continue
		}
		modelMappings[from] = to
	}
}

// LookupModelMapping returns the rewritten model name for a client-facing model.
func LookupModelMapping(model string) (string, bool) {
	modelMappingsMu.RLock()
	defer modelMappingsMu.RUnlock()
	target, ok := modelMappings[model]
	return target, ok
}

// LookupModelAlias returns the family alias target for a model id.
//
// Only exact-prefix aliases are considered, and the longest matching prefix
// wins so a specific alias beats a broader one. Callers must check the
// registry first: an alias never overrides a model that is registered under
// its own name.
func LookupModelAlias(model string) (string, bool) {
	modelMappingsMu.RLock()
	defer modelMappingsMu.RUnlock()
	bestLen := -1
	best := ""
	for pattern, target := range modelAliases {
		prefix := strings.TrimSuffix(pattern, "*")
		if prefix == "" || !strings.HasPrefix(model, prefix) {
			continue
		}
		if len(prefix) > bestLen {
			bestLen = len(prefix)
			best = target
		}
	}
	if bestLen < 0 {
		return "", false
	}
	return best, true
}

// AllModelMappings returns a snapshot of configured rewrite rules.
func AllModelMappings() map[string]string {
	modelMappingsMu.RLock()
	defer modelMappingsMu.RUnlock()
	out := make(map[string]string, len(modelMappings)+len(modelAliases))
	for from, to := range modelMappings {
		out[from] = to
	}
	for from, to := range modelAliases {
		out[from] = to
	}
	return out
}

// LoadModelMappings loads mappings from MODEL_MAPPINGS and/or
// MODEL_MAPPING_FILE. File rules are applied after env JSON and therefore win
// on duplicate source models. Invalid config logs a warning and leaves existing
// in-memory mappings untouched.
func LoadModelMappings() {
	cfg := Get()
	merged := AllModelMappings()
	loaded := false

	if strings.TrimSpace(cfg.ModelMappings) != "" {
		m, err := ParseModelMappings(strings.NewReader(cfg.ModelMappings))
		if err != nil {
			log.Printf("warn: cannot parse MODEL_MAPPINGS: %v", err)
		} else {
			for from, to := range m {
				merged[from] = to
			}
			loaded = true
		}
	}

	if strings.TrimSpace(cfg.ModelMappingFile) != "" {
		m, err := LoadModelMappingsFromFile(cfg.ModelMappingFile)
		if err != nil {
			log.Printf("warn: cannot load MODEL_MAPPING_FILE %q: %v", cfg.ModelMappingFile, err)
		} else {
			for from, to := range m {
				merged[from] = to
			}
			loaded = true
		}
	}

	if strings.TrimSpace(cfg.ModelAliases) != "" {
		m, err := ParseModelMappings(strings.NewReader(cfg.ModelAliases))
		if err != nil {
			log.Printf("warn: cannot parse MODEL_ALIASES: %v", err)
		} else {
			for from, to := range m {
				merged[from] = to
			}
			loaded = true
		}
	}

	if loaded {
		RegisterModelMappings(merged)
	}
}

// LoadModelMappingsFromFile reads model rewrite rules from a JSON file.
func LoadModelMappingsFromFile(path string) (map[string]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return ParseModelMappings(f)
}

// ParseModelMappings decodes a JSON object like {"gpt-5.5":"glm-5.1"}.
func ParseModelMappings(r io.Reader) (map[string]string, error) {
	var raw map[string]string
	if err := json.NewDecoder(r).Decode(&raw); err != nil {
		return nil, err
	}
	m := normalizeModelMappings(raw)
	if len(raw) > 0 && len(m) == 0 {
		return nil, fmt.Errorf("mapping object contains no non-empty source/target pairs")
	}
	return m, nil
}

func normalizeModelMappings(in map[string]string) map[string]string {
	out := map[string]string{}
	for from, to := range in {
		from = strings.TrimSpace(from)
		to = strings.TrimSpace(to)
		if from == "" || to == "" {
			continue
		}
		out[from] = to
	}
	return out
}
