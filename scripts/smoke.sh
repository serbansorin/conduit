#!/usr/bin/env bash
# End-to-end smoke test for the Conduit gateway.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
PORT=19812
BASE="http://127.0.0.1:${PORT}"

cleanup() {
  [[ -n "${SERVER_PID:-}" ]] && kill "$SERVER_PID" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

cd "$ROOT"
export PATH="/opt/homebrew/bin:$PATH"

echo "==> building"
go build -o "$WORK/conduit" .

echo "==> starting gateway on port ${PORT}"
ADMIN_PASSWORD=smoke-password \
JWT_SECRET="$(openssl rand -hex 32)" \
DB_PATH="$WORK/conduit.db" \
PORT="$PORT" \
"$WORK/conduit" serve --no-model-sync > "$WORK/server.log" 2>&1 &
SERVER_PID=$!

# Wait for the server to accept connections.
for _ in $(seq 1 50); do
  if curl -fsS "$BASE/health" > /dev/null 2>&1; then break; fi
  sleep 0.2
done

TOKEN=$(grep -oE 'sk-[A-Za-z0-9]+' "$WORK/server.log" | head -1)
if [[ -z "$TOKEN" ]]; then
  echo "FAIL: no bootstrap token in server log"
  cat "$WORK/server.log"
  exit 1
fi
echo "==> bootstrap token: ${TOKEN:0:12}..."

check() {
  local name="$1" expected="$2" actual="$3"
  if [[ "$actual" == *"$expected"* ]]; then
    echo "  PASS  $name"
  else
    echo "  FAIL  $name (expected to contain '$expected', got '$actual')"
    FAILED=1
  fi
}

FAILED=0

echo "==> GET /health"
check "health reports service" '"service":"conduit"' "$(curl -fsS "$BASE/health")"
check "health reports version" '"version"' "$(curl -fsS "$BASE/health")"

echo "==> GET /v1/models (public)"
check "models list" '"object":"list"' "$(curl -fsS "$BASE/v1/models")"

echo "==> GET /metrics (public)"
check "metrics uptime" 'conduit_uptime_seconds' "$(curl -fsS "$BASE/metrics")"

echo "==> POST /v1/messages/count_tokens (auth)"
CT=$(curl -fsS -X POST "$BASE/v1/messages/count_tokens" \
  -H "x-api-key: $TOKEN" -H "content-type: application/json" \
  -d '{"model":"claude-sonnet-4-5-20250929","messages":[{"role":"user","content":"hello world"}]}')
check "count_tokens returns input_tokens" '"input_tokens"' "$CT"

echo "==> auth enforcement"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/messages" \
  -H "content-type: application/json" -d '{"model":"x","messages":[]}')
check "unauthenticated request rejected" "401" "$CODE"

echo "==> admin login"
ADMIN_JWT=$(curl -fsS -X POST "$BASE/admin/login" \
  -H "content-type: application/json" -d '{"password":"smoke-password"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
check "admin login returns a token" "." "$ADMIN_JWT"

echo "==> GET /admin/backup"
BACKUP=$(curl -fsS "$BASE/admin/backup" -H "Authorization: Bearer $ADMIN_JWT")
check "backup has version" '"version"' "$BACKUP"
check "backup has keys array" '"keys"' "$BACKUP"

echo "==> GET /admin/usage.csv"
CSV=$(curl -fsS "$BASE/admin/usage.csv" -H "Authorization: Bearer $ADMIN_JWT" | head -1)
check "csv has header" "request_id" "$CSV"

echo "==> GET /admin/keys"
check "keys list" '"data"' "$(curl -fsS "$BASE/admin/keys" -H "Authorization: Bearer $ADMIN_JWT")"

echo "==> unknown model is rejected (strict mode)"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/messages" \
  -H "x-api-key: $TOKEN" -H "content-type: application/json" \
  -d '{"model":"totally-unknown-model","max_tokens":8,"messages":[{"role":"user","content":"hi"}]}')
check "unknown model rejected with 404" "404" "$CODE"

echo "==> request id header"
check "X-Request-Id present" "X-Request-Id" "$(curl -fsS -D - -o /dev/null "$BASE/health" | tr -d '\r')"

if [[ "${FAILED:-0}" == "1" ]]; then
  echo "==> SMOKE TEST FAILED"
  echo "--- server log ---"
  cat "$WORK/server.log"
  exit 1
fi

echo "==> ALL SMOKE TESTS PASSED"
