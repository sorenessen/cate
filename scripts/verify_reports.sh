#!/usr/bin/env bash
set -euo pipefail

outdir="logs/verify"
fuzz_ok="${outdir}/fuzz_ok"
pass="${outdir}/pass"
fail="${outdir}/fail"
wordlist="${outdir}/payloads_demo.txt"

rm -rf "$outdir"
mkdir -p "$outdir"

# -------------------------
# Fuzz sanity (should succeed)
# -------------------------
cat > "$wordlist" <<'EOF'
test
cate
"quote
<svg/onload=1>
../
%00
EOF

cate http-fuzz \
  --url "https://httpbingo.org/get?b={payload}" \
  --wordlist "$wordlist" \
  --output "$fuzz_ok"

# Fuzz artifacts exist
test -f "$fuzz_ok"
test -f "${fuzz_ok}.report.html"
test -f "${fuzz_ok}.report.md"
test -f "${fuzz_ok}.summary.json"
test -f "${fuzz_ok}.signals.json"

# Fuzz report markers
grep -q 'id="cate-data"' "${fuzz_ok}.report.html"
grep -q "<title>CATE Report" "${fuzz_ok}.report.html"
grep -q "## Executive summary" "${fuzz_ok}.report.md"

# Fuzz should not be HIGH severity on a benign endpoint
if grep -qi '"severity": "high"' "${fuzz_ok}.signals.json"; then
  echo "ERROR: fuzz_ok severity is HIGH"
  exit 1
fi

# -------------------------
# Fuzz fail sanity (must fail, but is expected)
# -------------------------
fuzz_fail="${outdir}/fuzz_fail"

set +e
cate http-fuzz \
  --url "https://example.invalid/get?b={payload}" \
  --wordlist "$wordlist" \
  --output "$fuzz_fail"
fuzz_fail_rc=$?
set -euo pipefail

# We don't currently rely on exit code for fuzz failures.
# Assert via artifacts + HIGH severity signal instead.
test -f "${fuzz_fail}.signals.json"
test -f "${fuzz_fail}.summary.json"
test -f "${fuzz_fail}.report.md"
test -f "${fuzz_fail}.report.html"

if ! grep -qi '"severity": "high"' "${fuzz_fail}.signals.json"; then
  echo "ERROR: expected fuzz_fail severity to be HIGH"
  cat "${fuzz_fail}.signals.json"
  exit 1
fi

# -------------------------
# Flow sanity (pass + expected fail)
# -------------------------

# Passing flow (must succeed)
cate http-flow \
  --flows-file flows/tmp-redirect.toml \
  --flow redirect-demo \
  --output "$pass" \
  --mode recon

# Failing flow (must fail, but is expected)
set +e
cate http-flow \
  --flows-file flows/tmp-redirect-fail.toml \
  --flow redirect-demo \
  --output "$fail" \
  --mode recon
fail_rc=$?
set -euo pipefail

if [[ $fail_rc -eq 0 ]]; then
  echo "ERROR: expected failing flow to return non-zero, got 0"
  exit 1
fi

# Assertions for both flow runs
for prefix in "$pass" "$fail"; do
  test -f "${prefix}.report.html"
  test -f "${prefix}.report.md"

  grep -q 'id="cate-data"' "${prefix}.report.html"
  grep -q "<title>CATE Report" "${prefix}.report.html"
  grep -q "## Executive summary" "${prefix}.report.md"
done

echo "PASS: flow + fuzz reports/signals verified (including expected failure)"
