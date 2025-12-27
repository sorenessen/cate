#!/usr/bin/env bash
set -euo pipefail

rm -rf logs/verify
mkdir -p logs/verify

# Fuzz sanity (should succeed and be non-HIGH)
cat > logs/verify/payloads_demo.txt <<'EOF'
test
cate
"quote
<svg/onload=1>
../
%00
EOF

cate http-fuzz \
  --url "https://httpbingo.org/get?b={payload}" \
  --wordlist logs/verify/payloads_demo.txt \
  --output logs/verify/fuzz_ok

# Fuzz artifacts exist
test -f "logs/verify/fuzz_ok"
test -f "logs/verify/fuzz_ok.report.html"
test -f "logs/verify/fuzz_ok.report.md"
test -f "logs/verify/fuzz_ok.summary.json"
test -f "logs/verify/fuzz_ok.signals.json"

# Fuzz report markers
grep -q 'id="cate-data"' "logs/verify/fuzz_ok.report.html"
grep -q "<title>CATE Report" "logs/verify/fuzz_ok.report.html"
grep -q "## Executive summary" "logs/verify/fuzz_ok.report.md"

# Fuzz severity should be none for benign endpoint
grep -qi '"severity": "high"' "logs/verify/fuzz_ok.signals.json" && {
  echo "ERROR: fuzz_ok severity is HIGH"
  exit 1
}


# Passing flow (must succeed)
cate http-flow \
  --flows-file flows/tmp-redirect.toml \
  --flow redirect-demo \
  --output logs/verify/pass \
  --mode recon

# Failing flow (must fail)
set +e
cate http-flow \
  --flows-file flows/tmp-redirect-fail.toml \
  --flow redirect-demo \
  --output logs/verify/fail \
  --mode recon
fail_rc=$?
set -euo pipefail

if [[ $fail_rc -eq 0 ]]; then
  echo "ERROR: expected failing flow to return non-zero, got 0"
  exit 1
fi

# Assertions
for f in pass fail; do
  test -f "logs/verify/$f.report.html"
  test -f "logs/verify/$f.report.md"

  grep -q 'id="cate-data"' "logs/verify/$f.report.html"
  grep -q "<title>CATE Report" "logs/verify/$f.report.html"
  grep -q "## Executive summary" "logs/verify/$f.report.md"
done

echo "PASS: flow + fuzz reports/signals verified (including expected failure)"

