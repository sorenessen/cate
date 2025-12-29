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
# Usage:
#   ./scripts/verify.sh
# Optional:
#   VERIFY_OUTDIR=logs/verify ./scripts/verify.sh
#   VERIFY_NO_SNAPSHOT=1 ./scripts/verify.sh   (CI-friendly)
# -----------------------------

VERIFY_OUTDIR="${VERIFY_OUTDIR:-logs/verify}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_PREFIX="${VERIFY_OUTDIR}/pass_${RUN_ID}"

mkdir -p "${VERIFY_OUTDIR}"

echo "[verify] output prefix: ${OUT_PREFIX}"

# Prefer installed CLI, but fall back to module invocation for CI robustness
if command -v cate >/dev/null 2>&1; then
  CATE_CMD=(cate)
else
  CATE_CMD=(python -m cate.cli)
fi

SNAPSHOT_ARGS=()
if [[ "${VERIFY_NO_SNAPSHOT:-}" != "1" ]]; then
  SNAPSHOT_ARGS=(--exit-snapshot)
fi

# Run a known-safe demo flow
"${CATE_CMD[@]}" http-flow \
  --flows-file flows.toml \
  --flow demo-template-funcs \
  --output "${OUT_PREFIX}" \
  --mode recon \
  "${SNAPSHOT_ARGS[@]}"

# Required artifacts (snapshot optional)
req=(
  "${OUT_PREFIX}.jsonl"
  "${OUT_PREFIX}.summary.json"
  "${OUT_PREFIX}.summary.md"
  "${OUT_PREFIX}.signals.json"
  "${OUT_PREFIX}.signals.md"
  "${OUT_PREFIX}.report.md"
  "${OUT_PREFIX}.report.html"
)

if [[ "${VERIFY_NO_SNAPSHOT:-}" != "1" ]]; then
  req+=("${OUT_PREFIX}.exit.pass.png")
fi

for f in "${req[@]}"; do
  test -f "$f" || { echo "[verify] missing: $f"; exit 1; }
done

# Content assertions
grep -q 'id="cate-data"' "${OUT_PREFIX}.report.html"
grep -q '<title>CATE Report' "${OUT_PREFIX}.report.html"
grep -q "## Executive summary" "${OUT_PREFIX}.report.md"

echo "[verify] OK: artifacts + content checks passed."
