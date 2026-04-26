#!/usr/bin/env bash
# L3 acceptance driver for the session-closer skill.
#
# Two modes:
#
#   scripts/l3-acceptance.sh bash-only
#       Runs every V-X recipe that does NOT need the live ``Skill``
#       tool (so it can run in this developer session, not just a
#       plugin-loaded one). Currently: V-3, V-9 Run 1, V-10 Run 2
#       (race), V-14. Reports PASS / FAIL per V-X.
#
#   scripts/l3-acceptance.sh helper <V-X> {setup|assert|cleanup}
#       For the Skill-required V-X (V-1, V-2, V-4-8, V-9 Run 2,
#       V-10 Run 1, V-12, V-13, V-15). Run ``setup`` first (it
#       creates the test issue and exports env vars to a sourceable
#       file), then drive the skill manually in the plugin session,
#       then run ``assert``. ``cleanup`` closes the test issue.
#
# Conventions:
# - Test issues are titled ``[V-N] verification`` so the cleanup
#   script (``scripts/cleanup-l3-verification-issues.sh``) can sweep
#   them en masse if a run dies midway.
# - All Setup blocks honour the env vars from VERIFICATION.md's common
#   preamble (REPO, CLAUDE_PROJECT_DIR, STATE_DIR, MODE).
set -euo pipefail

REPO="${REPO:-etoyama/claude-issueops}"
export REPO
export CLAUDE_ISSUEOPS_VERIFICATION_MODE=1
export CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
export STATE_DIR="${STATE_DIR:-${CLAUDE_PROJECT_DIR}/session-state}"
mkdir -p "$STATE_DIR"

PASS_COUNT=0
FAIL_COUNT=0
FAIL_LIST=()

# Preflight: the verification recipes assume `status:in-progress` exists
# on the target repo (Tier 1 issue resolver scans for it). Create on the
# fly so the script is self-bootstrapping. Idempotent — re-create returns
# a "already exists" error which we swallow.
_ensure_label() {
  local name="$1"
  if ! gh label list --repo "$REPO" --limit 200 2>/dev/null | awk '{print $1}' | grep -qx "$name"; then
    gh label create "$name" --repo "$REPO" --color "fbca04" \
      --description "L3 verification target marker" >/dev/null 2>&1 || true
  fi
}

_pass() { echo "  PASS: $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
_fail() { echo "  FAIL: $1"; FAIL_COUNT=$((FAIL_COUNT + 1)); FAIL_LIST+=("$1"); }
_section() { echo; echo "=== $1 ==="; }

# ---------------------------------------------------------------------------
# V-14: SKILL.md frontmatter validation (pure shell — name + capture/close
# + ≥3 triggers).
# ---------------------------------------------------------------------------
v14() {
  _section "V-14 SKILL.md frontmatter"
  local skill="skills/session-closer/SKILL.md"
  if [[ ! -f "$skill" ]]; then
    _fail "V-14: SKILL.md missing"
    return
  fi
  # awk-extract the YAML-ish frontmatter block (between the first two ---).
  local fm
  fm=$(awk '/^---$/{c++; if (c==2) exit; next} c==1' "$skill")
  if ! echo "$fm" | grep -qE '^name:\s*session-closer\s*$'; then
    _fail "V-14: name != session-closer"
    return
  fi
  if ! echo "$fm" | grep -qi 'capture' || ! echo "$fm" | grep -qi 'close'; then
    _fail "V-14: description missing capture/close"
    return
  fi
  local trig_count
  trig_count=$(echo "$fm" | awk '/^triggers:/{t=1; next} /^[A-Za-z_]/{t=0} t && /^  - /{n++} END{print n+0}')
  if [[ "$trig_count" -lt 3 ]]; then
    _fail "V-14: triggers count $trig_count < 3"
    return
  fi
  _pass "V-14 (name, capture/close in desc, $trig_count triggers)"
}

# ---------------------------------------------------------------------------
# V-9 Run 1: bin returns ambiguous_candidates when ≥2 in-progress issues
# exist and the branch carries no number hint.
# ---------------------------------------------------------------------------
v9_run1() {
  _section "V-9 Run 1 ambiguous resolve-issue"
  local sid="v9r1-$(date +%s)"
  local issue_a issue_b
  issue_a=$(gh issue create --repo "$REPO" --title "[V-9-A] verification" --body "candidate A" --label status:in-progress | awk -F/ '{print $NF}')
  issue_b=$(gh issue create --repo "$REPO" --title "[V-9-B] verification" --body "candidate B" --label status:in-progress | awk -F/ '{print $NF}')
  trap "gh issue close '$issue_a' --repo '$REPO' --comment 'V-9 Run 1 cleanup' >/dev/null 2>&1 || true; gh issue close '$issue_b' --repo '$REPO' --comment 'V-9 Run 1 cleanup' >/dev/null 2>&1 || true" RETURN

  # Wait until gh's label-filtered list returns BOTH issues. The search
  # backend behind ``gh issue list --label`` is eventually consistent;
  # poll for up to ~10 s before giving up so the assertion does not
  # FAIL on a race that is unrelated to the actual resolver behaviour.
  local i seen
  for i in 1 2 3 4 5 6 7 8 9 10; do
    seen=$(gh issue list --repo "$REPO" --state open --label "status:in-progress" --json number \
      -q '[.[] | select(.number == '"$issue_a"' or .number == '"$issue_b"')] | length')
    [[ "$seen" == "2" ]] && break
    sleep 1
  done
  if [[ "${seen:-0}" != "2" ]]; then
    _fail "V-9 Run 1: gh list never showed both fresh issues (saw $seen, race / ratelimit)"
    return
  fi

  local out
  out=$(echo "{\"schema_version\":1,\"subcommand\":\"resolve-issue\",\"session_id\":\"$sid\",\"project_dir\":\"$PWD\",\"branch\":\"master\"}" \
    | uv run python bin/session_closer.py)

  local ok kind n_candidates
  ok=$(echo "$out" | jq -r '.ok')
  kind=$(echo "$out" | jq -r '.error.kind // ""')
  n_candidates=$(echo "$out" | jq -r '.result.ambiguous_candidates | length // 0' 2>/dev/null || echo 0)

  # Detect the specific failure modes for clearer reporting.
  if [[ "$ok" == "false" ]]; then
    if [[ "$kind" == "issue-resolution" ]]; then
      _fail "V-9 Run 1: bin returned issue-resolution error — fewer than 2 in-progress issues at moment of call (race)"
    else
      _fail "V-9 Run 1: bin returned ok=false (kind=$kind)"
    fi
  elif [[ "$n_candidates" -ge 2 ]]; then
    _pass "V-9 Run 1 (ambiguous_candidates length=$n_candidates)"
  else
    _fail "V-9 Run 1: ambiguous_candidates length $n_candidates (expected ≥2)"
  fi
}

# ---------------------------------------------------------------------------
# V-10 Run 2: parallel commit-state + precompact_hook on the same SID; no
# patch loss, no leftover .tmp files. Pure-bin (no skill, no real issue).
# ---------------------------------------------------------------------------
v10_run2() {
  _section "V-10 Run 2 parallel race"
  local sid="v10r2-$(date +%s)"
  local state_file="$STATE_DIR/$sid.json"
  rm -f "$state_file" "$state_file".corrupt-* "$state_file".tmp.* 2>/dev/null || true
  trap "rm -f '$state_file' '$state_file'.corrupt-* '$state_file'.tmp.* 2>/dev/null || true" RETURN

  echo "{\"session_id\":\"$sid\",\"cwd\":\"$PWD\"}" \
    | uv run python bin/precompact_hook.py >/dev/null 2>&1 &
  local pid_pre=$!
  echo "{\"schema_version\":1,\"subcommand\":\"commit-state\",\"project_dir\":\"$PWD\",\"session_id\":\"$sid\",\"patch\":{\"skill_ran_at\":\"2026-04-26T00:00:00+00:00\"}}" \
    | uv run python bin/session_closer.py >/dev/null 2>&1 &
  local pid_cs=$!
  wait "$pid_pre" "$pid_cs" 2>/dev/null || true

  if [[ ! -f "$state_file" ]]; then
    _fail "V-10 Run 2: state file not created"
    return
  fi

  if ! jq -e '.skill_ran_at' "$state_file" >/dev/null 2>&1; then
    _fail "V-10 Run 2: skill_ran_at missing — concurrent write lost the commit-state patch"
    return
  fi

  local tmp_count
  tmp_count=$(ls "$STATE_DIR" 2>/dev/null | grep -cE "${sid}\.json\.tmp\." || true)
  if [[ "$tmp_count" != "0" ]]; then
    _fail "V-10 Run 2: $tmp_count tmp file(s) remained after race"
    return
  fi

  _pass "V-10 Run 2 (skill_ran_at present, no tmp residue)"
}

# ---------------------------------------------------------------------------
# V-3: SessionEnd hook is a no-op when state file is fresh (no fallback
# comment posted).
# ---------------------------------------------------------------------------
v3() {
  _section "V-3 SessionEnd skip"
  local sid="v3-$(date +%s)"
  local issue
  issue=$(gh issue create --repo "$REPO" --title "[V-3] verification" --body "verification target" --label status:in-progress | awk -F/ '{print $NF}')
  trap "gh issue close '$issue' --repo '$REPO' --comment 'V-3 cleanup' >/dev/null 2>&1 || true" RETURN

  # Pre-create a state file so SessionEnd has something to inspect.
  echo '{"session_id":"'"$sid"'","skill_ran_at":"2026-04-26T00:00:00+00:00"}' > "$STATE_DIR/$sid.json"

  local pre_count
  pre_count=$(gh issue view "$issue" --repo "$REPO" --json comments | jq '.comments | length')

  echo "{\"session_id\":\"$sid\",\"cwd\":\"$PWD\"}" \
    | uv run python bin/sessionend_hook.py >/dev/null 2>&1 || true

  local post_count fallback_count
  post_count=$(gh issue view "$issue" --repo "$REPO" --json comments | jq '.comments | length')
  fallback_count=$(gh issue view "$issue" --repo "$REPO" --json comments \
    | jq '[.comments[] | select(.body | contains("session-end-fallback"))] | length')

  if [[ "$post_count" == "$pre_count" && "$fallback_count" == "0" ]]; then
    _pass "V-3 (no new comments, no fallback)"
  else
    _fail "V-3: comments $pre_count→$post_count, fallback=$fallback_count"
  fi
}

# ---------------------------------------------------------------------------
# Helpers for the Skill-required V-X (V-1, V-2, V-4-8, V-9 Run 2,
# V-10 Run 1, V-11, V-12, V-13, V-15). The user runs ``setup`` before
# invoking the skill, then ``assert``, then ``cleanup``.
# ---------------------------------------------------------------------------
helper() {
  local vx="${1:-}"
  local action="${2:-}"
  if [[ -z "$vx" || -z "$action" ]]; then
    cat <<EOF >&2
Usage: $0 helper <V-X> {setup|assert|cleanup}
Example: $0 helper V-1 setup
EOF
    exit 2
  fi
  echo "Helper for $vx / $action — see VERIFICATION.md for the full recipe." >&2
  echo "This driver currently provides automated runs for V-3, V-9 Run 1, V-10 Run 2, V-14 only." >&2
  echo "For other V-X, copy the Setup / Run / Assert / Cleanup blocks from VERIFICATION.md directly." >&2
  exit 2
}

main() {
  local mode="${1:-bash-only}"
  case "$mode" in
    bash-only)
      _ensure_label "status:in-progress"
      v14
      v3
      v9_run1
      v10_run2
      _section "Summary"
      echo "  PASS: $PASS_COUNT"
      echo "  FAIL: $FAIL_COUNT"
      if [[ $FAIL_COUNT -gt 0 ]]; then
        printf '  failed: %s\n' "${FAIL_LIST[@]}"
        exit 1
      fi
      echo
      echo "Bash-only V-X complete. Skill-required ones (V-1, V-2, V-4-8, V-9 Run 2, V-10 Run 1, V-11, V-12, V-13, V-15) need a plugin-loaded session."
      ;;
    helper)
      shift
      helper "$@"
      ;;
    *)
      cat <<EOF >&2
Usage:
  $0 bash-only          # automated V-3, V-9 Run 1, V-10 Run 2, V-14
  $0 helper <V-X> ...   # not implemented; copy from VERIFICATION.md
EOF
      exit 2
      ;;
  esac
}

main "$@"
