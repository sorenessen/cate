#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <prev_tag> <new_tag>" >&2
  echo "Example: $0 v0.2.0 v0.3.0" >&2
  exit 1
fi

PREV_TAG="$1"
NEW_TAG="$2"

# ISO date like 2025-12-08
DATE="$(date +%Y-%m-%d)"

REPO_URL="https://github.com/sorenessen/cate"

echo "# CATE ${NEW_TAG} — Release Notes"
echo
echo "_${DATE}_"
echo
echo "## Summary"
echo
echo "- TODO: Short human-readable summary of this release."
echo
echo "## Changes since ${PREV_TAG}"
echo

# One bullet per commit, no merge commits
git log "${PREV_TAG}..${NEW_TAG}" --no-merges --pretty='- %s'

echo
echo "## Links"
echo
echo "- [Compare diff](${REPO_URL}/compare/${PREV_TAG}...${NEW_TAG})"
echo "- [${NEW_TAG} tag](${REPO_URL}/releases/tag/${NEW_TAG})"
