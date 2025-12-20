#!/usr/bin/env bash
set -euo pipefail\nset -x\nset -x\nset -x\nset -x

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p logs/demo

cat > /tmp/payloads_demo.txt <<'PAY'
test
admin
%00
../../etc/passwd
' OR 1=1 --
PAY

echo "[1/4] Starting lab (colima + docker)..."
command -v colima >/dev/null && colima start >/dev/null || true
docker compose -f lab/docker-compose.yml up -d --build

echo "[2/4] NO ENCODE run..."
cate http-fuzz \
  --url "http://localhost:8080/login?b={payload}" \
  --wordlist /tmp/payloads_demo.txt \
  --concurrency 10 \
  --max-rps 5 \
  --timeout 2 \
  --output logs/demo/no-encode.jsonl

echo "Effective URLs (no-encode):"
tail -n 5 logs/demo/no-encode.jsonl | sed -n 's/.*"effective_url": "\(.*\)".*/\1/p'

echo "[3/4] ENCODE run..."
cate http-fuzz \
  --url "http://localhost:8080/login?b={payload}" \
  --wordlist /tmp/payloads_demo.txt \
  --concurrency 10 \
  --max-rps 5 \
  --timeout 2 \
  --urlencode-payload \
  --output logs/demo/encode.jsonl

echo "Effective URLs (encode):"
tail -n 5 logs/demo/encode.jsonl | sed -n 's/.*"effective_url": "\(.*\)".*/\1/p'

echo "[4/4] Summaries:"
echo "--- no-encode ---"
cat logs/demo/no-encode.summary.md
echo "--- encode ---"
cat logs/demo/encode.summary.md

echo "✅ Demo complete. Artifacts in logs/demo/"
