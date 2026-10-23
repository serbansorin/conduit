# pool

The key pool and gateway tokens.

- `key.go` — `Picker` with weighted round-robin, per-key cooldown (3 failures
  triggers a 30s cooldown, exponential to 10m), quota-aware 429 handling and
  failover candidates via `PickAttempts`.
- `token.go` — `GenerateToken`, `FindToken`, `IsExpired`, `GroupAllowed`,
  `CreateToken` (with `WithMaxRequests`, `WithDescription`).
- `quota.go` — `CheckGroupQuotas` for live quota lookups.

Gateway tokens are stored in plaintext because the admin panel shows them for
copying.
