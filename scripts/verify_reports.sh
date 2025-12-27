#!/usr/bin/env bash
set -euo pipefail

rm -rf logs/verify
mkdir -p logs/verify

# Passing flow
cate http-flow \
  --flows-file flows/tmp-redirect.toml \
  --flow redirect-demo \
  --output logs/verify/pass \
  --mode recon

# Failing flow
cate http-flow \
  --flows-file flows/tmp-redirect-fail.toml \
  --flow redirect-demo \
  --output logs/verify/fail \
  --mode recon

# Assertions
for f in pass fail; do
  test -f logs/verify/$f.report.html
  test -f logs/verify/$f.report.md

  grep -q 'id="cate-data"' logs/verify/$f.report.html
done

echo "PASS: reports verified"
