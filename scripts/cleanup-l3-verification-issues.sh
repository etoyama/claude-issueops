#!/usr/bin/env bash
# Close every open issue whose title starts with "[V-" — the safety
# net for L3 verification runs that died midway and left test issues
# open. Idempotent (a clean repo is a no-op).
#
# Usage:
#   scripts/cleanup-l3-verification-issues.sh                # close all [V- prefix open issues
#   scripts/cleanup-l3-verification-issues.sh --dry-run      # list, do not close
#   REPO=other/repo scripts/cleanup-l3-verification-issues.sh
set -euo pipefail

REPO="${REPO:-etoyama/claude-issueops}"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

NUMBERS=$(
  gh issue list \
    --repo "$REPO" \
    --state open \
    --search '"[V-" in:title' \
    --json number,title \
    -q '.[] | select(.title | startswith("[V-")) | .number'
)

if [[ -z "$NUMBERS" ]]; then
  echo "No open [V- issues. Nothing to clean up."
  exit 0
fi

COUNT=$(echo "$NUMBERS" | wc -l | tr -d ' ')
echo "Found $COUNT open [V- issue(s):"
gh issue list \
  --repo "$REPO" \
  --state open \
  --search '"[V-" in:title' \
  --json number,title \
  -q '.[] | "  #\(.number)  \(.title)"'

if [[ $DRY_RUN -eq 1 ]]; then
  echo "(dry run — no changes made)"
  exit 0
fi

for n in $NUMBERS; do
  gh issue close "$n" --repo "$REPO" --comment "L3 verification cleanup (auto-close)" >/dev/null
  echo "  closed #$n"
done

echo "Done."
