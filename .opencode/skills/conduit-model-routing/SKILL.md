---
name: conduit-model-routing
description: >-
  Add or change a model route in the Conduit gateway. Use when working on
  internal/router, config/model_mapping.go, config/models.go, config/aliases.go,
  modelsync/ or the admin model endpoints. Triggers on model mapping, family
  alias, MODEL_ALIASES, MODEL_FALLBACK, PASSTHROUGH_MODE, catalog sync, model
  registry or the admin models API.
metadata:
  category: development
  suggest_for:
    filename:
      - '*.go'
      - '*.json'
---

# Conduit Model Routing

Two different mechanisms carry a client model id to an upstream:

- **`config/model_mapping.go`** holds rewrite rules. Exact rules rewrite the
  id. Family rules, whose source ends in `*`, are aliases that only apply to
  models that are not registered routes.
- **The `ModelRoute` registry** in `config/models.go` holds the real routes.
  Each route names an upstream, a protocol, a real upstream model id and a
  key-pool group.

Keep them apart. A mapping changes the id the client asked for. A route
describes where the request goes.

## The four step resolution order

`internal/router/router.go` `Resolve(model string, inbound config.Protocol)
Resolution` is the only resolver. It returns:

```go
type Resolution struct {
	Route         config.ModelRoute
	IsPassthrough bool // true when the model was not in the registry
	NotFound      bool // true when the model is unknown and passthrough is disabled
}
```

The order:

1. **Exact mapping.** `config.LookupModelMapping(model)`. If the client id is a
   key of the exact mapping table, it is replaced and the registry lookup uses
   the new id.
2. **Registry.** `config.LookupModel(model)`. A registered route always wins
   over a family alias.
3. **Family alias.** `config.LookupModelAlias(model)` resolves the pattern to
   a target id, and that target is looked up in the registry. This step only
   runs when step 2 found nothing.
4. **Fallback.** `config.Get().ModelFallback` names a route to serve when
   nothing else matched.

If nothing matched:

- `PASSTHROUGH_MODE=go` -> `Resolution{Route: passthroughRoute(model, inbound),
  IsPassthrough: true}`. The passthrough route uses `UpstreamGo`, the inbound
  protocol, `RealModel` equal to the requested id and group `"go"`.
- Otherwise -> `Resolution{NotFound: true}` and the handler returns 404
  `model_not_found`.

`api/proxy.go` `proxyRequest` treats a not-found route as 404, and a route
whose `IsEnabled()` is false as 403 `model_disabled`. A passthrough route is
never disabled-checked.

`LookupModelAlias` matches the longest prefix. The pattern `claude-*` becomes
the prefix `claude`, and an empty prefix is skipped. When two patterns match,
the longer prefix wins.

## Exact rules versus family aliases

`config/model_mapping.go` keeps two maps behind one `sync.RWMutex`:

```go
var (
	modelMappings   = map[string]string{}
	modelAliases    = map[string]string{}
	modelMappingsMu sync.RWMutex
)
```

A source ending in `*` is a family alias, anything else is an exact rule. The
public API:

| Function | Purpose |
| --- | --- |
| `RegisterModelMapping(from, to)` | add or replace one rule. A `from` ending in `*` goes into the alias map, otherwise into the exact map. |
| `RemoveModelMapping(from)` | delete the rule from both maps. |
| `RegisterModelMappings(m)` | replace every rule at once. |
| `LookupModelMapping(model)` | exact lookup only. Never matches a family pattern. |
| `LookupModelAlias(model)` | longest matching prefix lookup over the alias map. |
| `AllModelMappings()` | merged snapshot of both maps. |
| `LoadModelMappings()` | load from env and file, then apply over the current set. |
| `ParseModelMappings(r io.Reader)` | decode a JSON object of `{"from":"to"}`. |
| `LoadModelMappingsFromFile(path)` | read and decode a JSON file. |

`LoadModelMappings` merges in this order, so later sources win on a duplicate
source id:

1. `MODEL_MAPPINGS` (inline JSON).
2. `MODEL_MAPPING_FILE` (path to a JSON file).
3. `MODEL_ALIASES` (inline JSON, family patterns).

If a source fails to parse it logs a warning and leaves the existing in-memory
rules untouched. It never clears them.

`server/server.go` `loadModelMappings` loads the persisted rules from SQLite
first via `store.LoadModelMappings()`, registers them with
`config.RegisterModelMappings`, and only then calls `config.LoadModelMappings()`.
So the env and file rules override the DB managed rules.

### The shape of a family alias

Family aliases are a JSON object. The source ends in `*`, the target is a
concrete model id.

In `.env`:

```bash
MODEL_ALIASES={"claude-*":"claude-haiku-5-5"}
```

In a mapping file, `config/model-mapping.example.json`:

```json
{
  "gpt-5.5": "glm-51",
  "claude-*": "claude-haiku-5-5"
}
```

A source ending in `*` is treated as an alias wherever it appears, including
in `MODEL_MAPPINGS`. `RegisterModelMapping` moves it into the alias map
automatically.

Exact rules use the same shape without the star:

```bash
MODEL_MAPPINGS={"gpt-5.5":"glm-5.1"}
```

### The seeded family alias

`config/aliases.go` seeds one alias automatically:

```go
var clientModelFamilies = []string{"claude"}
```

`SeedFamilyAliases()` picks the first enabled model in the registry whose id
starts with `claude-` and registers `claude-*` for it. It never overwrites an
alias an operator configured with a different target. `server/server.go` calls
`SeedFamilyAliases()` after every catalog sync.

## The registry

`config/models.go` holds `ModelRoute`:

```go
type ModelRoute struct {
	ID                  string                      `json:"id"`
	Name                string                      `json:"name"`
	Upstream            Upstream                    `json:"upstream"`
	Upstreams           []Upstream                  `json:"upstreams,omitempty"`
	UpstreamGroups      map[Upstream]string         `json:"upstream_groups,omitempty"`
	Targets             map[Upstream]UpstreamTarget `json:"targets,omitempty"`
	Protocol            Protocol                    `json:"protocol"`
	RealModel           string                      `json:"real_model"`
	Group               string                      `json:"group"`
	ContextLen          int                         `json:"context_len"`
	Status              *int                        `json:"status,omitempty"`
	Priority            int                         `json:"priority"`
	Tags                []string                    `json:"tags,omitempty"`
	IsCustomized        bool                        `json:"is_customized,omitempty"`
	CustomizedFields    []string                    `json:"customized_fields,omitempty"`
	OpenRouterID        string                      `json:"openrouter_id,omitempty"`
	OpenRouterName      string                      `json:"openrouter_name,omitempty"`
	OpenRouterMatchedBy string                      `json:"openrouter_matched_by,omitempty"`
	Architecture        *ModelArchitecture          `json:"architecture,omitempty"`
	Pricing             map[string]string           `json:"pricing,omitempty"`
	SupportedParameters []string                    `json:"supported_parameters,omitempty"`
	Description         string                      `json:"description,omitempty"`
	KnowledgeCutoff     string                      `json:"knowledge_cutoff,omitempty"`
}
```

`Upstream` is `"go"` or `"ollama"`. `Protocol` is `"chat"`, `"messages"`,
`"responses"` or `"google"` (google is declared but not implemented).

`Status` is a pointer: `nil` means enabled. `config.ModelStatusEnabled` is 1
and `config.ModelStatusDisabled` is 0. `ModelStatusPtr` builds the pointer.

Per upstream overrides live in `Targets` (a `map[Upstream]UpstreamTarget` with
`real_model`, `protocol` and `group`). The accessors are:

| Method | Priority |
| --- | --- |
| `ResolveUpstreamGroup(u)` | `Targets[u].Group`, then `UpstreamGroups[u]`, then the legacy route level `Group` when `u == m.Upstream`, then `string(u)`. |
| `TargetRealModel(u)` | `Targets[u].RealModel`, then the route level `RealModel`. |
| `TargetProtocol(u)` | `Targets[u].Protocol`, then the route level `Protocol`. |
| `TargetGroup(u)` | `Targets[u].Group`, then `UpstreamGroup(u)`. |
| `UpstreamGroup(u)` | `UpstreamGroups[u]`, then `string(u)`. |

`ResolveUpstreamGroup` is the single authoritative group resolver. Call it
once and pass the result down. `api/proxy.go` calls it per failover iteration.

Registry functions: `RegisterModel`, `RegisterModels`, `ReplaceModels`,
`LookupModel`, `AllModels`, `AllEnabledModels`, `RemoveModel`,
`BaseURLFor(upstream)`. `RegisterModel` and friends apply
`applyLocalModelDefaults`, which fills `Upstream` with `go`, `Upstreams` with
`[Upstream]`, `Group` with `"go"`, `RealModel` with the id, `Name` with the
id, `Status` with enabled, deduplicates `Upstreams`, normalizes `Tags` and
`CustomizedFields`, and logs a warning if `ValidateModelRoute` fails.

`DefaultModels()` returns nil. The catalog is discovered, never seeded from
source.

`ValidateModelRoute` enforces the invariants: the primary upstream must be
valid and must appear in `Upstreams`, every entry of `Upstreams` must be
valid, and every key of `UpstreamGroups` and `Targets` must be valid and must
appear in `Upstreams`. `ValidateAndNormalizeUpstreams` does the same for a
list plus a group map and returns a deduplicated list.

## The catalogue sync

`modelsync/sync.go` discovers the catalogue from three sources and enriches
it:

| Source | Default URL |
| --- | --- |
| OpenCode Go | `https://opencode.ai/zen/go/v1/models` |
| Ollama Cloud | `https://ollama.com/api/tags` |
| OpenRouter (metadata only) | `https://openrouter.ai/api/v1/models` |

Entry points:

```go
func Sync(ctx context.Context, opts Options) (Result, error)
func Rebuild(ctx context.Context, opts Options) (Result, error)
func StartBackground(ctx context.Context, interval time.Duration, opts Options, onResult func(Result, error))
func ReloadRuntimeFromStore() error
```

`Options` carries the three URLs, an `*http.Client` and a `Now func() time.Time`
clock. `Result` reports `OpenCodeCount`, `OllamaCount`, `OpenRouterCount`,
`MatchedCount`, `CreatedCount`, `UpdatedCount`, `UnchangedCount`,
`DeletedCount`, `DisabledCount`, `TotalCount` and `Warnings`.

How a run works:

1. The three sources are fetched concurrently.
2. The Go and Ollama lists are merged by model id. A model offered by both
   gets both upstreams plus a per-upstream target, so Go and Ollama can use
   different real model ids and protocols for the same gateway model.
3. The protocol for a Go model is inferred from its id by `inferProtocol`:
   ids starting with `minimax-` or `qwen` speak Messages, everything else
   speaks Chat.
4. OpenRouter metadata is matched with `config.MatchOpenRouterModel` and
   applied with `config.ApplyOpenRouterMetadata`, which attaches context
   length, pricing, modality, supported parameters, tags, description and
   knowledge cutoff.
5. Rows are written with `store.SaveModelRoute`, which upserts on the id.

### Manual edits survive a sync

A route carries `IsCustomized` and `CustomizedFields`. `buildMergedRoute`
consults `config.IsModelFieldCustomized(route, field)` for every field it is
about to write, so an admin edit to `name`, `protocol`, `real_model`,
`upstream`, `upstreams`, `upstream_groups`, `targets`, `group` or `tags`
survives the next sync.

`config.NormalizeCustomizedFields` canonicalises the field names, so
`context_length` becomes `context_len`, `capabilities` becomes `tags`,
`price`, `prices`, `billing` and `billing_rates` become `pricing`, and
`display_name` becomes `name`.

`config.ApplyOpenRouterMetadata` honours the same mechanism: it only writes
`context_len`, `pricing` and `tags` when those fields are not customized.

`admin/router.go` records the customization. `upsertModel` and `updateModel`
build a `changed` list of the JSON fields the request actually touched and
call `mergeModelCustomizedFields`, which merges the existing row's
customized fields with the new ones. `toggleModel` adds `"status"`.

### Removed models are reconciled, not deleted

A model that exists in the DB but not in the merged catalogue is reconciled
per provider. If that provider's fetch succeeded and the model is absent from
its catalogue, the provider is removed from the route. When no upstreams remain
the route is disabled rather than deleted. Admin customized `upstreams` and
`status` are preserved. If both are customized the row is left alone.

### Rebuild is the safe variant

`Rebuild` refuses to run unless both routable sources fetched successfully and
the merged catalogue is non-empty:

```go
if !goFetched || !ollamaFetched {
	return result, fmt.Errorf("safe rebuild requires successful OpenCode Go and Ollama catalog fetches")
}
```

It then replaces every persisted route in one SQLite transaction through
`store.ReplaceModelRoutes`, so a reader never sees a half built catalogue.

A concurrent run returns `modelsync.ErrSyncInProgress`. The admin handlers
turn that into 409.

## Environment variables

| Variable | Default | Effect |
| --- | --- | --- |
| `MODEL_MAPPINGS` | empty | inline JSON of exact rewrite rules. A source ending in `*` becomes a family alias. |
| `MODEL_MAPPING_FILE` | empty | path to a JSON file with the same shape. Applied after `MODEL_MAPPINGS`, so it wins on duplicates. |
| `MODEL_ALIASES` | empty | inline JSON of family aliases, for example `{"claude-*":"claude-haiku-5-5"}`. Applied last. |
| `MODEL_FALLBACK` | empty | the route to serve for an unknown model. Empty means 404. |
| `PASSTHROUGH_MODE` | `disabled` | `disabled` rejects unknown models with 404. `go` forwards them to the Go upstream as-is. |
| `GO_BASE_URL` | `https://opencode.ai/zen/go` | base URL for the Go upstream. |
| `OLLAMA_BASE_URL` | `https://ollama.com` | base URL for the Ollama upstream. |
| `GROUP_MULTIPLIERS` | empty | per group billing multiplier. JSON object or `group=1.0` list. Unset groups use 1.0. |

`.env` is read from the working directory by `config.loadDotEnv`. It never
overrides a variable that is already set in the environment.

## The admin model endpoints

All of these sit behind `adminAuth()` under the `/admin` group.

| Method | Path | Body | Effect |
| --- | --- | --- | --- |
| `GET` | `/admin/models` | none | `{"data": [...]}` with every registered route. |
| `POST` | `/admin/models/sync` | none | runs `modelsync.Sync`. 409 when a sync is already running, 502 on failure. |
| `POST` | `/admin/models/rebuild` | `{"confirmation":"REBUILD"}` | runs `modelsync.Rebuild`. The confirmation string is required and is compared case insensitively after trimming. |
| `POST` | `/admin/models` | route fields | `upsertModel`. Creates or replaces a route. |
| `PATCH` | `/admin/models/:id` | route fields | `updateModel`. Partial update, records the touched fields as customized. |
| `POST` | `/admin/models/:id/toggle` | none | `toggleModel`. Flips `status` and marks it customized. |
| `DELETE` | `/admin/models/:id` | none | `deleteModel`. Removes the route from the registry and the DB. |
| `GET` | `/admin/model-mappings` | none | the persisted rewrite rules. |
| `POST` | `/admin/model-mappings` | `{"source_model":"...","target_model":"..."}` | `upsertModelMapping`. Also calls `config.RegisterModelMapping`. |
| `DELETE` | `/admin/model-mappings/*source` | none | `deleteModelMapping`. The source may contain slashes, hence the wildcard. |

`upsertModel` requires `id` and `protocol`. `upstream` must be `go` or
`ollama`. `group` defaults to the upstream name. When `upstreams` is supplied
it must contain `upstream`. Every key of `upstream_groups` and `targets` must
appear in `upstreams`.

## Traps

- A registered model always beats a family alias. Do not expect `claude-*` to
  override an explicitly registered `claude-sonnet-4-5`.
- `config.Load()` runs once behind a `sync.Once`. Changing `MODEL_FALLBACK` or
  `PASSTHROUGH_MODE` at runtime means changing `config.Get()` in place, not
  reloading the environment.
- `config.RegisterModelMappings` wipes both maps before inserting. Use
  `RegisterModelMapping` for a single rule.
- A family alias target must itself be a registered route. If
  `LookupModelAlias` returns an id that is not in the registry, the alias is
  skipped and resolution falls through to the fallback.
- `config.Get().ModelFallback` names a route **id**, not a real upstream model
  id. The fallback is resolved with `config.LookupModel`.
- Editing a route through the admin API marks the fields as customized. That
  is how the edit survives the next six hourly sync.
- `modelMapping` writes happen under `modelMappingsMu`. Read them through
  `LookupModelMapping`, `LookupModelAlias` and `AllModelMappings`, never by
  touching the maps directly.

## Verify a change

```bash
export PATH="/opt/homebrew/bin:$PATH"
go build ./...
go test -race ./...      # internal/router, config and modelsync all have tests
gofmt -l .
```

`internal/router/router_test.go`, `config/model_mapping_test.go`,
`config/models_test.go` and `modelsync/sync_test.go` are the relevant suites.
