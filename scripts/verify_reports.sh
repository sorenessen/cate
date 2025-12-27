#!/usr/bin/env bash
set -euo pipefail

outdir="logs/verify"
wordlist="${outdir}/payloads_demo.txt"

fuzz_ok="${outdir}/fuzz_ok"
fuzz_404="${outdir}/fuzz_404"
fuzz_fail="${outdir}/fuzz_fail"

pass="${outdir}/pass"
fail="${outdir}/fail"

rm -rf "$outdir"
mkdir -p "$outdir"

die() { echo "ERROR: $*" >&2; exit 1; }

assert_file() { test -f "$1" || die "missing file: $1"; }

assert_contains() {
  local needle="$1" file="$2"
  grep -q "$needle" "$file" || die "expected '$needle' in $file"
}

assert_json_valid() {
  local file="$1"
  python - <<PY "$file" >/dev/null
import json,sys
with open(sys.argv[1],"r",encoding="utf-8") as f:
    json.load(f)
PY
}

assert_signals_schema_min() {
  local file="$1"
  assert_json_valid "$file"
  assert_contains '"kind"' "$file"
  assert_contains '"ok"' "$file"
  assert_contains '"severity"' "$file"
  assert_contains '"counts"' "$file"
}

assert_summary_schema_min() {
  local file="$1"
  assert_json_valid "$file"
  # Keep this lightweight: just prove it's JSON and has something "summary-ish".
  # Adjust keys later if your summary schema stabilizes.
  assert_contains '{' "$file"
}

assert_report_markers() {
  local prefix="$1"
  assert_file "${prefix}.report.html"
  assert_file "${prefix}.report.md"
  assert_contains 'id="cate-data"' "${prefix}.report.html"
  assert_contains "<title>CATE Report" "${prefix}.report.html"
  assert_contains "## Executive summary" "${prefix}.report.md"
}

assert_not_high_severity() {
  local signals_json="$1" label="$2"
  if grep -qi '"severity": "high"' "$signals_json"; then
    echo "---- ${label} signals ----" >&2
    cat "$signals_json" >&2
    die "${label} severity is HIGH"
  fi
}

assert_is_high_severity() {
  local signals_json="$1" label="$2"
  if ! grep -qi '"severity": "high"' "$signals_json"; then
    echo "---- ${label} signals ----" >&2
    cat "$signals_json" >&2
    die "expected ${label} severity to be HIGH"
  fi
}

# -------------------------
# Wordlist
# -------------------------
cat > "$wordlist" <<'EOF'
test
cate
"quote
<svg/onload=1>
../
%00
EOF

# -------------------------
# Fuzz sanity (reachable endpoint)
# -------------------------
cate http-fuzz \
  --url "https://httpbingo.org/get?b={payload}" \
  --wordlist "$wordlist" \
  --output "$fuzz_ok"

assert_file "$fuzz_ok"
assert_file "${fuzz_ok}.summary.json"
assert_file "${fuzz_ok}.signals.json"
assert_report_markers "$fuzz_ok"
assert_signals_schema_min "${fuzz_ok}.signals.json"
assert_summary_schema_min "${fuzz_ok}.summary.json"
assert_not_high_severity "${fuzz_ok}.signals.json" "fuzz_ok"

# -------------------------
# Fuzz sanity (reachable non-200 status)
# Purpose: ensure non-200 does NOT automatically become HIGH.
# -------------------------
cate http-fuzz \
  --url "https://httpbingo.org/status/404?b={payload}" \
  --wordlist "$wordlist" \
  --output "$fuzz_404"

assert_file "$fuzz_404"
assert_file "${fuzz_404}.summary.json"
assert_file "${fuzz_404}.signals.json"
assert_report_markers "$fuzz_404"
assert_signals_schema_min "${fuzz_404}.signals.json"
assert_summary_schema_min "${fuzz_404}.summary.json"
assert_not_high_severity "${fuzz_404}.signals.json" "fuzz_404"

# -------------------------
# Fuzz fail sanity (unreachable endpoint; expected HIGH signal)
# Note: http-fuzz may still exit 0. We assert via signals + artifacts.
# -------------------------
set +e
cate http-fuzz \
  --url "https://example.invalid/get?b={payload}" \
  --wordlist "$wordlist" \
  --output "$fuzz_fail"
fuzz_fail_rc=$?
set -e

# rc is informational right now; don't gate on it (documented behavior)
: "${fuzz_fail_rc:?}"

assert_file "$fuzz_fail"
assert_file "${fuzz_fail}.summary.json"
assert_file "${fuzz_fail}.signals.json"
assert_report_markers "$fuzz_fail"
assert_signals_schema_min "${fuzz_fail}.signals.json"
assert_summary_schema_min "${fuzz_fail}.summary.json"
assert_is_high_severity "${fuzz_fail}.signals.json" "fuzz_fail"

# -------------------------
# Flow sanity (pass + expected fail)
# -------------------------
cate http-flow \
  --flows-file flows/tmp-redirect.toml \
  --flow redirect-demo \
  --output "$pass" \
  --mode recon

set +e
cate http-flow \
  --flows-file flows/tmp-redirect-fail.toml \
  --flow redirect-demo \
  --output "$fail" \
  --mode recon
fail_rc=$?
set -e

if [[ $fail_rc -eq 0 ]]; then
  die "expected failing flow to return non-zero, got 0"
fi

for prefix in "$pass" "$fail"; do
  assert_report_markers "$prefix"
done

echo "PASS: flow + fuzz reports/signals verified (including expected failure)"
