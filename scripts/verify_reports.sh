#!/usr/bin/env bash
set -euo pipefail

rm -rf logs/verify
mkdir -p logs/verify

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
set -e

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

echo "PASS: reports verified (including expected failure)"
