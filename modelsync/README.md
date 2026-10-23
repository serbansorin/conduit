# modelsync

Discovers and enriches the model catalog.

- `sync.go` — `Sync`, `Rebuild`, `StartBackground`, `ReloadRuntimeFromStore`.
- Sources: OpenCode Go (`/v1/models`), Ollama Cloud (`/api/tags`) and
  OpenRouter (metadata only).
- `Rebuild` is the safe variant: it requires both routable sources to have
  fetched and a non-empty catalog before replacing the table in one
  transaction.
- Manual admin edits are recorded as customized fields and survive later
  automatic syncs.
