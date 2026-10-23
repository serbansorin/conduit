# admin

The admin REST API, mounted under `/admin` by `admin.MountWithPicker`.

`router.go` holds every endpoint: login, keys, tokens, stats, usage, models,
model mappings, backup and restore. `backup.go` is the export/import
implementation. `github_oauth.go` and `github_import.go` drive the GitHub
auto-login that imports Go keys, the auth cookie and the workspace id.
`go_quota.go`, `ollama_quota.go` and `quota.go` fetch live quota for a key.

The admin panel itself is served from `web/embed.go` at `/admin`.
