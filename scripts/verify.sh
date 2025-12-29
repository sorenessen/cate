#!/usr/bin/env bash
set -euo pipefail

# repo root guard
if [[ ! -f "./pyproject.toml" ]] || [[ ! -d "./scripts" ]]; then
  echo "[verify] ERROR: run from the repo root (cate/):"
  echo "         ./scripts/verify.sh"
  exit 2
fi

# -----------------------------
# CATE smoke verification
# -----------------------------
# This script:
#  - runs a known-safe demo http-flow (httpbin)
#  - generates full artifact set + exit snapshot
#  - asserts files exist + key content markers exist
#
# Usage:
#   ./scripts/verify.sh
#
# Optional:
#   VERIFY_OUTDIR=logs/verify ./scripts/verify.sh
# -----------------------------

VERIFY_OUTDIR="${VERIFY_OUTDIR:-logs/verify}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_PREFIX="${VERIFY_OUTDIR}/pass_${RUN_ID}"

mkdir -p "${VERIFY_OUTDIR}"

echo "[verify] output prefix: ${OUT_PREFIX}"

# Run a deterministic-ish safe flow
# NOTE: adjust flows file path if yours isn't at repo root.
cate http-flow \
  --flows-file flows.toml \
  --flow demo-template-funcs \
  --output "${OUT_PREFIX}" \
  --mode recon \
  --exit-snapshot

# Required artifacts
req=(
  "${OUT_PREFIX}.jsonl"
  "${OUT_PREFIX}.summary.json"
  "${OUT_PREFIX}.summary.md"
  "${OUT_PREFIX}.signals.json"
  "${OUT_PREFIX}.signals.md"
  "${OUT_PREFIX}.report.md"
  "${OUT_PREFIX}.report.html"
  "${OUT_PREFIX}.exit.pass.png"
)

for f in "${req[@]}"; do
  test -f "$f" || { echo "[verify] missing: $f"; exit 1; }
done

# Content assertions (these are the “it’s really the right report” checks)
grep -q 'id="cate-data"' "${OUT_PREFIX}.report.html"
grep -q '<title>CATE Report' "${OUT_PREFIX}.report.html"
grep -q "## Executive summary" "${OUT_PREFIX}.report.md"

echo "[verify] OK: artifacts + content checks passed."
