# upstream

The HTTP client and transport cache.

`client.go` builds and caches `*http.Client` instances keyed by proxy URL
(`NewClient`, `NewClientForProxy`, `CloseIdleConnections`, `Timeout`).

It does **not** issue requests and sets no headers. The request is issued in
`api/proxy.go` (Go upstream) and `api/ollama.go` (Ollama upstream).
`upstream/client.go` only exists to keep transports alive.
