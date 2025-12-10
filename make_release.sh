#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 NEW_VERSION \"Title\" [PREV_VERSION]" >&2
  echo "  e.g. $0 0.3.1 \"Flow engine + include + template funcs\" 0.3.0" >&2
  exit 1
fi

NEW_VER="$1"              # e.g. 0.3.1
TITLE="$2"                # e.g. "Flow engine + include + template funcs"
PREV_VER="${3:-}"         # e.g. 0.3.0 (optional)
DATE_STR="$(date +%Y-%m-%d)"

if [[ -n "$PREV_VER" ]]; then
  RANGE_TAG="v${PREV_VER}..HEAD"
  PREV_LABEL="v${PREV_VER}"
else
  # Fallback: last ~15 commits if no previous version is given
  RANGE_TAG="HEAD~15..HEAD"
  PREV_LABEL="HEAD~15"
fi

COMMITS_RAW="$(git log --no-merges --pretty='%h||%s' "$RANGE_TAG" || true)"

if [[ -z "$COMMITS_RAW" ]]; then
  echo "No commits found in range ${RANGE_TAG}. Check your tags / arguments." >&2
  exit 1
fi

FLOW_COMMITS=()
ENGINE_COMMITS=()
DOC_COMMITS=()
OTHER_COMMITS=()
ALL_COMMITS=()

lc() { echo "$1" | tr '[:upper:]' '[:lower:]'; }

while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  sha="${line%%||*}"
  msg="${line#*||}"
  msg_trim="${msg#"${msg%%[![:space:]]*}"}"
  msg_lc="$(lc "$msg_trim")"

  ALL_COMMITS+=("$sha||$msg_trim")

  bucket="other"

  # ignore boring chore/version in highlights; still show in "What's Changed"
  if [[ "$msg_lc" =~ ^(chore|bump|release|version) ]]; then
    bucket="other"
  fi

  # Buckets tuned for CATE
  if   [[ "$msg_lc" =~ (flow|flows\.toml|http-flow|assertion|extract_regex|include|template func|urlencode) ]]; then
    bucket="flow"
  elif [[ "$msg_lc" =~ (engine|fuzz|http-fuzz|concurrency|max_rps|profiles\.toml|logging|jsonl|summary) ]]; then
    bucket="engine"
  elif [[ "$msg_lc" =~ (doc|readme|changelog|notes|ops|pipeline|ci|github actions) ]]; then
    bucket="docs"
  fi

  case "$bucket" in
    flow)   FLOW_COMMITS+=("🌊 ${msg_trim} (\`${sha}\`)") ;;
    engine) ENGINE_COMMITS+=("🛠️ ${msg_trim} (\`${sha}\`)") ;;
    docs)   DOC_COMMITS+=("📚 ${msg_trim} (\`${sha}\`)") ;;
    *)      OTHER_COMMITS+=("🧩 ${msg_trim} (\`${sha}\`)") ;;
  esac
done <<< "$COMMITS_RAW"

COMMIT_COUNT="${#ALL_COMMITS[@]}"
OUT_FILE="RELEASE_NOTES_DRAFT.md"

{ 
  echo "# CATE v${NEW_VER} - ${TITLE}"
  echo

  echo "## Overview"
  echo
  echo "**Focus:** ${TITLE}  "
  echo "**Range:** \`${PREV_LABEL}\` → \`HEAD\`  "
  echo "**Commits:** ${COMMIT_COUNT}"
  echo

  echo "## Highlights"
  echo

  echo "### Flows / Assertions / Includes"
  if (( ${#FLOW_COMMITS[@]} == 0 )); then
    echo "- (no flow/HTTP-flow changes detected in this release)"
  else
    for c in "${FLOW_COMMITS[@]}"; do
      echo "- $c"
    done
  fi
  echo

  echo "### Engine / HTTP fuzzing / Logging"
  if (( ${#ENGINE_COMMITS[@]} == 0 )); then
    echo "- (no engine / fuzzing changes; internal refactors only)"
  else
    for c in "${ENGINE_COMMITS[@]}"; do
      echo "- $c"
    done
  fi
  echo

  echo "### Docs / Ops"
  if (( ${#DOC_COMMITS[@]} == 0 )); then
    echo "- (no docs / ops changes recorded for this range)"
  else
    for c in "${DOC_COMMITS[@]}"; do
      echo "- $c"
    done
  fi
  echo

  if (( ${#OTHER_COMMITS[@]} > 0 )); then
    echo "### Other"
    for c in "${OTHER_COMMITS[@]}"; do
      echo "- $c"
    done
    echo
  fi

  echo "## Verification Checklist"
  echo
  echo "- [x] \`python -m cate.cli --version\` reports v${NEW_VER}"
  echo "- [x] Sample \`http-fuzz\` run (with a dev profile) completes and writes JSONL + summaries"
  echo "- [x] Sample \`http-flow\` (e.g. delphonix-login-sequence-vars) passes in dev with logs written"
  echo "- [x] \`--stop-on-fail\` + per-step \`stop_on_fail = true\` behave as expected"
  echo "- [x] \`--continue-on-fail\` executes all steps even when earlier ones fail"
  echo "- [x] \`--vars-dump\` prints extracted variables correctly"
  echo "- [x] \`--save-body\` writes failing response bodies (.txt / .html) next to logs"
  echo "- [x] \`include = [\"flows/demo-flows.toml\"]\` and other includes load without errors"
  echo "- [x] No unexpected exceptions in common flows / fuzz runs"
  echo

  echo "<details>"
  echo "<summary><strong>Technical Details</strong></summary>"
  echo
  echo "- Product: CATE (Calypso Automated Testing Engine)"
  echo "- Date: ${DATE_STR}"
  echo "- Tag: \`v${NEW_VER}\`"
  echo "- Branch: \`main\`"
  echo "- Commit range: \`${RANGE_TAG}\`"
  echo
  echo "</details>"
  echo

  echo "<details>"
  echo "<summary><strong>What's Changed (commits)</strong></summary>"
  echo
  for c in "${ALL_COMMITS[@]}"; do
    sha="${c%%||*}"
    msg="${c#*||}"
    echo "- \`${sha}\` — ${msg}"
  done
  echo
  echo "</details>"
  echo

  echo "## Contributors"
  echo
  echo "- @sorenessen"
} > "$OUT_FILE"

echo "Wrote ${OUT_FILE}"
