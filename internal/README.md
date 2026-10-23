# internal

Shared helpers used by `api/`, `admin/` and `server/`.

- `router/` — `Resolve(model, inbound)`: mapping → registry → family alias →
  fallback → 404 (or passthrough in `go` mode). Also `RewriteRequestModel`.
- `session/` — `session.Resolve` / `session.Apply`: the upstream session
  header the OpenCode Go upstream requires on every request.
- `stream/` — `Pipe` for streaming response bodies.
