# store

The SQLite schema and queries.

- `sqlite.go` — `Init`, `DB`, `TryReserveRequest`, `ReleaseRequest`,
  `LoadModelRoutes`, `SaveModelRoute`, `ReplaceModelRoutes`,
  `LoadModelMappings`, `SaveModelMapping`, `DeleteModelMapping`,
  `InitForTest`.
- Tables: `Key`, `Token`, `ModelRouteRow`, `ModelMappingRow`, `UsageLog`.
- The database file is forced to mode 0600 and its directory to 0700.
