# config

Runtime configuration and the model catalog.

- `config.go` — `Config`, `Load()` (reads the environment and a `.env` file),
  `ValidateSecurity()`, `GroupMultiplier`.
- `models.go` — `ModelRoute`, `UpstreamTarget`, the registry
  (`RegisterModel`, `LookupModel`, `ReplaceModels`), `BaseURLFor`,
  `SeedFamilyAliases`.
- `model_mapping.go` — exact rewrites and `*` family aliases.
- `aliases.go` — seeds the `claude-*` family alias.
- `model-mapping.example.json` — example mapping file.
