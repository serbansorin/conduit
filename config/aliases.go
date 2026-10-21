package config

import (
	"log"
	"strings"
)

// clientModelFamilies lists the model families that agent clients request
// under their own vendor names. Claude Code, for example, always asks for
// "claude-sonnet-4-5-…" or "claude-opus-4-1-…" even when the upstream only
// hosts a different Claude generation.
var clientModelFamilies = []string{"claude"}

// SeedFamilyAliases registers a family alias ("claude-*") for every client
// model family the catalog can serve.
//
// The alias only ever applies to model ids that are not registered under their
// own name, so a client asking for "claude-haiku-5-5" still gets that exact
// model while "claude-sonnet-4-5-20250929" falls back to the closest
// available Claude model.
//
// Aliases that an operator configured explicitly are never overwritten.
func SeedFamilyAliases() {
	for _, family := range clientModelFamilies {
		prefix := family + "-"
		target, ok := pickFamilyModel(prefix)
		if !ok {
			log.Printf("no %s model in catalog; %s* will not be aliased", family, prefix)
			continue
		}
		pattern := prefix + "*"
		if existing, found := LookupModelAlias(pattern); found && existing != target {
			continue
		}
		RegisterModelMapping(pattern, target)
		log.Printf("model alias applied: %q -> %q", pattern, target)
	}
}

// pickFamilyModel returns the catalog model that best represents a family,
// preferring higher-priority routes and falling back to the first match in
// registry order.
func pickFamilyModel(prefix string) (string, bool) {
	for _, route := range AllEnabledModels() {
		if strings.HasPrefix(route.ID, prefix) {
			return route.ID, true
		}
	}
	return "", false
}
