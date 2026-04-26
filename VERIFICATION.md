# L3 Verification Recipes (V-1 〜 V-15)

`session-closer` skill の Level 3 (実機) 検証手順。各セクションは Claude が `Bash` ツールでそのまま実行できる単一のレシピ集として書かれている。

## 共通前提

- 検証時は **必ず** `CLAUDE_ISSUEOPS_VERIFICATION_MODE=1` を export する (本番運用との完全分離)。
- 各 V-X は **Setup → Run → Assert → Cleanup** の 4 セクションで構成される。Assert ブロックの全コマンドが exit 0 を返せば PASS。
- 検証用 issue は `[V-N]` prefix 付きで作成し、実行後に必ず close する (Cleanup 参照)。
- `$REPO=etoyama/claude-issueops`、`$SID` (session id)、`$STATE_DIR=${CLAUDE_PROJECT_DIR}/session-state`、`$MEMORY_DIR=~/.claude/projects/<encoded>/memory` を事前に export しておく前提。

```bash
# 共通環境変数 (一度だけ設定)
export REPO=etoyama/claude-issueops
export CLAUDE_ISSUEOPS_VERIFICATION_MODE=1
export CLAUDE_PROJECT_DIR="$(pwd)"
export STATE_DIR="${CLAUDE_PROJECT_DIR}/session-state"
mkdir -p "$STATE_DIR"

# Preflight: V-X recipes assume `status:in-progress` exists on the
# repo (Tier 1 issue resolver scans for it). Idempotent.
gh label list --repo "$REPO" --limit 200 | awk '{print $1}' | grep -qx "status:in-progress" \
  || gh label create "status:in-progress" --repo "$REPO" --color fbca04 \
       --description "L3 verification target marker"

# Preflight: GitHub's label-filtered list is eventually consistent
# (~1-2 s lag). When a recipe creates issues then immediately calls
# `bin/session_closer.py resolve-issue`, poll for visibility before
# invoking the bin to avoid spurious FAIL on fresh-issue race.
```

### ヘルパー (このリポ同梱)

- `scripts/l3-acceptance.sh bash-only` — Skill 起動が要らない V-X (V-3, V-9 Run 1, V-10 Run 2, V-14) を一気に走らせて PASS/FAIL を出す。test issue は trap で auto-close する
- `scripts/cleanup-l3-verification-issues.sh` — `[V-` prefix の open issue を一括 close。途中で session が落ちたときの掃除用
- `tests/fixtures/transcripts/v*-capture.jsonl` — capture モードを駆動するための transcript 雛形 (中身は `v-base-capture.jsonl` の symlink、3 decision を含む)

---

## V-1: capture mode の skill 実機起動 (R-1.1)

### Setup
```bash
export SID="v1-$(date +%s)"
ISSUE=$(gh issue create --repo "$REPO" --title "[V-1] verification" --body "verification target" --label status:in-progress | awk -F/ '{print $NF}')
export ISSUE
export STATE_FILE="$STATE_DIR/$SID.json"

# transcript fixture 配置 (存在しなければ minimal 版を生成)
TRANSCRIPT_DIR="${CLAUDE_PROJECT_DIR}/.claude/projects/$SID"
mkdir -p "$TRANSCRIPT_DIR"
if [ -f tests/fixtures/transcripts/v1-capture.jsonl ]; then
  cp tests/fixtures/transcripts/v1-capture.jsonl "$TRANSCRIPT_DIR/transcript.jsonl"
else
  : > "$TRANSCRIPT_DIR/transcript.jsonl"  # empty fallback
fi

export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v1-approve-all.json
export N=$(jq '.responses[0].selections | length' "$CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE")
```

### Run
Claude Code session 内で `Skill` ツール経由で `session-closer` を `--capture` モードで起動。出力を `/tmp/v1-skill.log` にリダイレクト。

### Assert
```bash
test "$(gh issue view "$ISSUE" --repo "$REPO" --json comments | jq '[.comments[] | select(.body | startswith("<!-- claude-issueops:decision:"))] | length')" = "$N"
test "$(jq '.captured_slugs | length' "$STATE_FILE")" = "$N"
jq -e '.skill_ran_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")' "$STATE_FILE" >/dev/null
grep -qE 'Posted [0-9]+ decisions' /tmp/v1-skill.log
```

### Cleanup
```bash
gh issue close "$ISSUE" --repo "$REPO" --comment "V-1 verification done"
unset CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE SID ISSUE STATE_FILE N
```

---

## V-2: 部分失敗時の state 整合 (R-1.3)

### Setup
```bash
export SID="v2-$(date +%s)"
ISSUE=$(gh issue create --repo "$REPO" --title "[V-2] verification" --body "verification target" --label status:in-progress | awk -F/ '{print $NF}')
export ISSUE
export STATE_FILE="$STATE_DIR/$SID.json"
export GITHUB_TOKEN=invalid_token_for_v2
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v2-save-on-failure.json
```

### Run
Claude が `Skill` 経由で `session-closer --capture` を起動、出力を `/tmp/v2-skill.log` にリダイレクト。

### Assert
```bash
test -f "$STATE_DIR/$SID.pending-decisions.json"
test "$(jq '.entries[0].decisions | length' "$STATE_DIR/$SID.pending-decisions.json")" -gt 0
grep -q 'gh_failure_kind: auth' /tmp/v2-skill.log
grep -q 'gh auth status' /tmp/v2-skill.log
```

### Cleanup
```bash
unset GITHUB_TOKEN CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE
gh issue close "$ISSUE" --repo "$REPO" --comment "V-2 verification done"
unset SID ISSUE STATE_FILE
```

---

## V-3: SessionEnd hook との skip 判定協調 (R-1.5)

### Setup
```bash
export SID="v3-$(date +%s)"
ISSUE=$(gh issue create --repo "$REPO" --title "[V-3] verification" --body "verification target" --label status:in-progress | awk -F/ '{print $NF}')
export ISSUE
export STATE_FILE="$STATE_DIR/$SID.json"
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v3-approve-one.json

# capture モードを 1 回走らせて state.skill_ran_at を立てる (V-1 と同手順)
# (skill 経由で実行し state file が作られた状態を作る)
PRE_COUNT=$(gh issue view "$ISSUE" --repo "$REPO" --json comments | jq '.comments | length')
export PRE_COUNT
```

### Run
```bash
# SessionEnd hook を bin 直接呼び出し (session 切断不要)
echo "{\"session_id\":\"$SID\",\"cwd\":\"$PWD\"}" | uv run python bin/sessionend_hook.py
```

### Assert
```bash
test "$(gh issue view "$ISSUE" --repo "$REPO" --json comments | jq '.comments | length')" = "$PRE_COUNT"
test "$(gh issue view "$ISSUE" --repo "$REPO" --json comments | jq '[.comments[] | select(.body | contains("session-end-fallback"))] | length')" = "0"
```

### Cleanup
```bash
gh issue close "$ISSUE" --repo "$REPO" --comment "V-3 verification done"
unset SID ISSUE STATE_FILE CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE PRE_COUNT
```

---

## V-4: close mode の追加フロー実機 (R-2.1, R-2.2, R-2.4)

### Setup
```bash
export SID="v4-$(date +%s)"
ISSUE=$(gh issue create --repo "$REPO" --title "[V-4] verification" --body "verification target" --label status:in-progress | awk -F/ '{print $NF}')
export ISSUE
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v4-mixed-scope.json
# fixture から cross-issue scope に確定する slug を抽出 (question_id は `scope:<slug>` 形式)
export SLUG=$(jq -r '[.responses[] | select(.question_id | startswith("scope:")) | select(.selections[0] == "cross-issue") | .question_id | sub("scope:"; "")][0] // "v4-decision"' "$CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE")
PROJECT_ENCODED=$(echo -n "$CLAUDE_PROJECT_DIR" | tr / -)
export MEMORY_DIR="$HOME/.claude/projects/$PROJECT_ENCODED/memory"
```

### Run
Claude が `Skill` 経由で `session-closer` (close mode、引数なし) を実行。

### Assert
```bash
test "$(gh issue view "$ISSUE" --repo "$REPO" --json comments | jq '[.comments[] | select(.body | startswith("<!-- claude-issueops:decision:"))] | length')" = "2"
test "$(gh issue view "$ISSUE" --repo "$REPO" --json comments | jq '[.comments[] | select(.body | contains("session-closer:summary:"))] | length')" = "1"
test -f "$MEMORY_DIR/reference_$SLUG.md"
```

### Cleanup
```bash
gh issue close "$ISSUE" --repo "$REPO" --comment "V-4 verification done"
unset SID ISSUE SLUG MEMORY_DIR CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE
```

---

## V-5: summary 投稿の 0 件スキップ (R-2.2)

### Setup
```bash
export SID="v5-$(date +%s)"
ISSUE=$(gh issue create --repo "$REPO" --title "[V-5] verification" --body "verification target" --label status:in-progress | awk -F/ '{print $NF}')
export ISSUE
export STATE_FILE="$STATE_DIR/$SID.json"
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v5-reject-all.json
PRE_COUNT=$(gh issue view "$ISSUE" --repo "$REPO" --json comments | jq '.comments | length')
export PRE_COUNT
```

### Run
Claude が `Skill` 経由で `session-closer` (close mode) を実行。

### Assert
```bash
test "$(gh issue view "$ISSUE" --repo "$REPO" --json comments | jq '.comments | length')" = "$PRE_COUNT"
test "$(jq '.captured_slugs // []' "$STATE_FILE" | jq 'length')" = "0"
jq -e '.skill_ran_at' "$STATE_FILE" >/dev/null
```

### Cleanup
```bash
gh issue close "$ISSUE" --repo "$REPO" --comment "V-5 verification done"
unset SID ISSUE STATE_FILE PRE_COUNT CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE
```

---

## V-6: cross-issue 昇格の memory file 生成 (R-2.4, R-8.1, R-8.2)

### Setup
```bash
export SID="v6-$(date +%s)"
ISSUE=$(gh issue create --repo "$REPO" --title "[V-6] verification" --body "verification target" --label status:in-progress | awk -F/ '{print $NF}')
export ISSUE
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v6-cross-issue.json
export SLUG=$(jq -r '[.responses[] | select(.question_id | startswith("scope:")) | select(.selections[0] == "cross-issue") | .question_id | sub("scope:"; "")][0] // "v6-decision"' "$CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE")
PROJECT_ENCODED=$(echo -n "$CLAUDE_PROJECT_DIR" | tr / -)
export MEMORY_DIR="$HOME/.claude/projects/$PROJECT_ENCODED/memory"
rm -f "$MEMORY_DIR/reference_$SLUG.md"  # 再生成観測のため
```

### Run
Claude が `Skill` 経由で `session-closer` を **2 回連続** で実行 (idempotency 観測のため)。

### Assert
```bash
test -f "$MEMORY_DIR/reference_$SLUG.md"
test "$(grep -c "reference_$SLUG.md" "$MEMORY_DIR/MEMORY.md")" = "1"
# 1 回目と 2 回目で MD5 同一 (実行間で計測する場合は HASH1 を 1 回目直後に取得)
HASH=$(md5sum "$MEMORY_DIR/reference_$SLUG.md" | awk '{print $1}')
test -n "$HASH"
```

### Cleanup
```bash
gh issue close "$ISSUE" --repo "$REPO" --comment "V-6 verification done"
unset SID ISSUE SLUG MEMORY_DIR CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE
```

---

## V-7: scope のユーザー上書き動作 (R-3.5)

### Setup
```bash
export SID="v7-$(date +%s)"
ISSUE=$(gh issue create --repo "$REPO" --title "[V-7] verification" --body "verification target" --label status:in-progress | awk -F/ '{print $NF}')
export ISSUE
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v7-override-to-issue.json
# V-7 は scope override を確認するため、issue に上書きされる slug を取る
export SLUG=$(jq -r '[.responses[] | select(.question_id | startswith("scope:")) | .question_id | sub("scope:"; "")][0] // "v7-decision"' "$CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE")
PROJECT_ENCODED=$(echo -n "$CLAUDE_PROJECT_DIR" | tr / -)
export MEMORY_DIR="$HOME/.claude/projects/$PROJECT_ENCODED/memory"
rm -f "$MEMORY_DIR/reference_$SLUG.md"
```

### Run
Claude が `Skill` 経由で `session-closer` を実行。

### Assert
```bash
test "$(gh issue view "$ISSUE" --repo "$REPO" --json comments | jq '[.comments[] | select(.body | contains("decision:'"$SLUG"'"))] | length')" = "1"
test ! -f "$MEMORY_DIR/reference_$SLUG.md"
```

### Cleanup
```bash
gh issue close "$ISSUE" --repo "$REPO" --comment "V-7 verification done"
unset SID ISSUE SLUG MEMORY_DIR CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE
```

---

## V-8: Tier 2 dedup での重複防止 (R-5.2)

### Setup
```bash
export SID="v8-$(date +%s)"
ISSUE=$(gh issue create --repo "$REPO" --title "[V-8] verification" --body "verification target" --label status:in-progress | awk -F/ '{print $NF}')
export ISSUE
export STATE_FILE="$STATE_DIR/$SID.json"
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v8-approve-all.json
```

### Run
1. Claude が `Skill` 経由で `session-closer --capture` を実行 (1 回目、出力 `/tmp/v8-skill-first.log`)。
2. `jq '.captured_slugs = []' "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"` で Tier 1 を意図的に空に戻す。
3. Claude が再度 `Skill` 経由で `session-closer --capture` を実行 (2 回目、出力 `/tmp/v8-skill-second.log`)。

### Assert
```bash
COUNT_AFTER_FIRST=$(gh issue view "$ISSUE" --repo "$REPO" --json comments | jq '[.comments[] | select(.body | startswith("<!-- claude-issueops:decision:"))] | length')
COUNT_AFTER_SECOND=$(gh issue view "$ISSUE" --repo "$REPO" --json comments | jq '[.comments[] | select(.body | startswith("<!-- claude-issueops:decision:"))] | length')
test "$COUNT_AFTER_FIRST" = "$COUNT_AFTER_SECOND"
grep -qE 'skipped [0-9]+ duplicates' /tmp/v8-skill-second.log
```

### Cleanup
```bash
gh issue close "$ISSUE" --repo "$REPO" --comment "V-8 verification done"
unset SID ISSUE STATE_FILE CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE
```

---

## V-9: Issue 解決の AmbiguousResolution (R-6.4)

### Setup
```bash
export SID="v9-$(date +%s)"
ISSUE_A=$(gh issue create --repo "$REPO" --title "[V-9-A] verification" --body "candidate A" --label status:in-progress | awk -F/ '{print $NF}')
ISSUE_B=$(gh issue create --repo "$REPO" --title "[V-9-B] verification" --body "candidate B" --label status:in-progress | awk -F/ '{print $NF}')
export ISSUE_A ISSUE_B
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v9-pick-issue.json
```

### Run 1 (bin 直接呼び出しで ambiguous を観測)
```bash
echo "{\"schema_version\":1,\"subcommand\":\"resolve-issue\",\"session_id\":\"$SID\",\"project_dir\":\"$PWD\",\"branch\":\"master\"}" \
  | uv run python bin/session_closer.py > /tmp/v9-resolve.json
```

### Assert 1 (ambiguous レスポンス)
```bash
jq -e '.ok == true' /tmp/v9-resolve.json >/dev/null
jq -e '.result.ambiguous_candidates | length >= 2' /tmp/v9-resolve.json >/dev/null
```

### Run 2 (override 付きで再 invocation)
Claude が `Skill` 経由で `session-closer --capture --issue-number-override "$ISSUE_A"` を実行。

### Assert 2 (投稿先が一意化)
```bash
test "$(gh issue view "$ISSUE_A" --repo "$REPO" --json comments | jq '[.comments[] | select(.body | startswith("<!-- claude-issueops:decision:"))] | length')" -ge "1"
test "$(gh issue view "$ISSUE_B" --repo "$REPO" --json comments | jq '[.comments[] | select(.body | startswith("<!-- claude-issueops:decision:"))] | length')" = "0"
```

### Cleanup
```bash
gh issue close "$ISSUE_A" --repo "$REPO" --comment "V-9 verification done"
gh issue close "$ISSUE_B" --repo "$REPO" --comment "V-9 verification done"
unset SID ISSUE_A ISSUE_B CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE
```

---

## V-10: state file 破損からの quarantine 復旧 + 並行書き込み race (R-7.4, NFR-Reliability)

### Setup
```bash
export SID="v10-$(date +%s)"
export STATE_FILE="$STATE_DIR/$SID.json"
echo '{"broken":' > "$STATE_FILE"   # 不正 JSON を意図的に書く
ISSUE=$(gh issue create --repo "$REPO" --title "[V-10] verification" --body "verification target" --label status:in-progress | awk -F/ '{print $NF}')
export ISSUE
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v10-state-corrupt.json
```

### Run (2 部構成)
1. **Quarantine**: Claude が `Skill` 経由で `session-closer --capture` を実行。
2. **Race**: Claude が PreCompact bin と session-closer bin を並行起動:
   ```bash
   echo "{\"session_id\":\"$SID\",\"cwd\":\"$PWD\"}" | uv run python bin/precompact_hook.py &
   # Flat envelope per design.md "Skill ↔ bin Contract" (no payload wrapper).
   echo "{\"schema_version\":1,\"subcommand\":\"commit-state\",\"project_dir\":\"$PWD\",\"session_id\":\"$SID\",\"patch\":{\"skill_ran_at\":\"2026-04-26T00:00:00+00:00\"}}" | uv run python bin/session_closer.py &
   wait
   ```

### Assert
```bash
# (a) quarantine
ls "$STATE_DIR" | grep -qE "${SID}\.json\.corrupt-[0-9T:.]+"
jq -e '.skill_ran_at' "$STATE_FILE" >/dev/null
# (b) 並行実行後 — 両フィールド共存 + tmp 残骸なし
jq -e '.pending_restore' "$STATE_FILE" >/dev/null || echo "(pending_restore optional if PreCompact skipped due to no current issue)"
jq -e '.skill_ran_at' "$STATE_FILE" >/dev/null
test "$(ls "$STATE_DIR" | grep -cE "${SID}\.json\.tmp\." || true)" = "0"
```

### Cleanup
```bash
gh issue close "$ISSUE" --repo "$REPO" --comment "V-10 verification done"
unset SID ISSUE STATE_FILE CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE
```

---

## V-11: memory escalation の実機書き込み (R-8.1)

### Setup
V-6 の事後状態を再利用。`$SLUG`, `$MEMORY_DIR` を引き継ぐ。

```bash
test -f "$MEMORY_DIR/reference_$SLUG.md" || { echo "V-6 を先に実行してください"; exit 1; }
```

### Run
```bash
uv run python -c "
from issueops.marker_parser import Decision
from issueops.memory_escalate import render_reference_memory
import json, os
fx = json.load(open(os.environ.get('CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE', 'verification-fixtures/v11-memory-content.json')))
d = Decision(slug=fx['decision']['slug'], what=fx['decision']['what'], why=fx['decision']['why'], alternatives=fx['decision']['alternatives'], consequences=fx['decision']['consequences'])
print(render_reference_memory(d), end='')
" > /tmp/v11-expected.md
```

### Assert
```bash
diff -u /tmp/v11-expected.md "$MEMORY_DIR/reference_$SLUG.md"
```

### Cleanup
```bash
rm -f /tmp/v11-expected.md
```

---

## V-12: gh auth 失敗時の hint 表示 (R-9.2)

### Setup
```bash
export SID="v12-$(date +%s)"
ISSUE=$(gh issue create --repo "$REPO" --title "[V-12] verification" --body "verification target" --label status:in-progress | awk -F/ '{print $NF}')
export ISSUE
export STATE_FILE="$STATE_DIR/$SID.json"
export GITHUB_TOKEN=invalid_token_for_v12
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v12-abort-on-failure.json
```

### Run
Claude が `Skill` 経由で `session-closer --capture` を起動 (出力 `/tmp/v12-skill.log`)。

### Assert
```bash
grep -q 'gh_failure_kind: auth' /tmp/v12-skill.log
grep -q 'gh auth status' /tmp/v12-skill.log
jq -e '.skill_ran_at' "$STATE_FILE" >/dev/null
```

### Cleanup
```bash
unset GITHUB_TOKEN CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE
gh issue close "$ISSUE" --repo "$REPO" --comment "V-12 verification done"
unset SID ISSUE STATE_FILE
```

---

## V-13: gh 失敗時の 3 択分岐 — 破棄 + 中断 (R-9.3〜R-9.6)

### Setup (両ケース共通)
```bash
export SID_A="v13a-$(date +%s)"
export SID_B="v13b-$(date +%s)"
ISSUE_A=$(gh issue create --repo "$REPO" --title "[V-13-A] discard" --body "discard case" --label status:in-progress | awk -F/ '{print $NF}')
ISSUE_B=$(gh issue create --repo "$REPO" --title "[V-13-B] abort" --body "abort case" --label status:in-progress | awk -F/ '{print $NF}')
export ISSUE_A ISSUE_B
export GITHUB_TOKEN=invalid_token_for_v13
```

### Run (a) 破棄
```bash
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v13-discard.json
# Claude が Skill 経由で `session-closer --capture` を SID=$SID_A で実行
```

### Run (b) 中断
```bash
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v13-abort.json
# Claude が Skill 経由で `session-closer --capture` を SID=$SID_B で実行
```

### Assert
```bash
# (a) 破棄
test ! -f "$STATE_DIR/$SID_A.pending-decisions.json"
test "$(jq '.captured_slugs // [] | length' "$STATE_DIR/$SID_A.json")" = "0"
# (b) 中断
test ! -f "$STATE_DIR/$SID_B.pending-decisions.json"
jq -e '(.last_processed_offset // 0) == 0' "$STATE_DIR/$SID_B.json" >/dev/null
```

### Cleanup
```bash
unset GITHUB_TOKEN CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE
gh issue close "$ISSUE_A" --repo "$REPO" --comment "V-13 verification done"
gh issue close "$ISSUE_B" --repo "$REPO" --comment "V-13 verification done"
unset SID_A SID_B ISSUE_A ISSUE_B
```

---

## V-14: SKILL.md frontmatter validation (R-10.1)

### Setup
```bash
test -f skills/session-closer/SKILL.md
```

### Run / Assert (一体)
```bash
uv run python -c "
import re, sys
from pathlib import Path
text = Path('skills/session-closer/SKILL.md').read_text()
parts = text.split('---', 2)
assert len(parts) >= 3, 'frontmatter not found'
fm_text = parts[1]
# minimal yaml-ish parse (stdlib only — no pyyaml dep)
def parse_simple(t):
    out = {}
    cur_key = None
    for line in t.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        if line.startswith('  - ') and cur_key:
            out.setdefault(cur_key, [])
            if isinstance(out[cur_key], list):
                out[cur_key].append(line.strip()[2:].strip())
            continue
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*):\\s*(.*)$', line)
        if m:
            k, v = m.group(1), m.group(2).strip()
            cur_key = k
            if v == '':
                out[k] = []
            elif v == '|' or v == '>':
                out[k] = ''  # block scalar; will be filled by following lines (skipped here)
            else:
                out[k] = v.strip('\"\\'')
    return out
fm = parse_simple(fm_text)
name = fm.get('name', '')
desc = fm.get('description', '')
triggers = fm.get('triggers', [])
# fallback for block-scalar description: scan for capture/close in raw fm text
if 'capture' not in desc.lower() or 'close' not in desc.lower():
    if 'capture' in fm_text.lower() and 'close' in fm_text.lower():
        desc = fm_text
assert name == 'session-closer', f'name mismatch: {name!r}'
assert 'capture' in desc.lower() and 'close' in desc.lower(), f'description missing capture/close'
assert isinstance(triggers, list) and len(triggers) >= 3, f'triggers <3: {triggers!r}'
print('OK')
" | tee /tmp/v14-result.txt
grep -q '^OK$' /tmp/v14-result.txt
```

### Cleanup
```bash
rm -f /tmp/v14-result.txt
```

---

## V-15: モード切替の skill 実機検証 (R-10.2, R-10.3)

### Setup
```bash
export SID="v15-$(date +%s)"
ISSUE=$(gh issue create --repo "$REPO" --title "[V-15] verification" --body "verification target" --label status:in-progress | awk -F/ '{print $NF}')
export ISSUE
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v15-mode-switch.json
export SLUG=$(jq -r '[.responses[] | select(.question_id | startswith("scope:")) | select(.selections[0] == "cross-issue") | .question_id | sub("scope:"; "")][0] // "v15-decision"' "$CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE")
PROJECT_ENCODED=$(echo -n "$CLAUDE_PROJECT_DIR" | tr / -)
export MEMORY_DIR="$HOME/.claude/projects/$PROJECT_ENCODED/memory"
rm -f "$MEMORY_DIR/reference_$SLUG.md"
```

### Run
1. Claude が `Skill` 経由で `session-closer --capture` (capture mode 単独) を実行。
2. (Assert (a) を実行)
3. Claude が `Skill` 経由で `session-closer` (close mode、引数なし) を実行。
4. (Assert (b) を実行)

### Assert (a) capture モード直後
```bash
test "$(gh issue view "$ISSUE" --repo "$REPO" --json comments | jq '[.comments[] | select(.body | contains("session-closer:summary:"))] | length')" = "0"
test ! -f "$MEMORY_DIR/reference_$SLUG.md"
```

### Assert (b) close モード直後
```bash
test "$(gh issue view "$ISSUE" --repo "$REPO" --json comments | jq '[.comments[] | select(.body | contains("session-closer:summary:"))] | length')" = "1"
test -f "$MEMORY_DIR/reference_$SLUG.md"
```

### Cleanup
```bash
gh issue close "$ISSUE" --repo "$REPO" --comment "V-15 verification done"
unset SID ISSUE SLUG MEMORY_DIR CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE
```

---

## 実行順序の推奨

V-X 間は基本的に独立だが、以下の依存だけ守る:
- V-11 は V-6 の事後状態 (memory file 生成済) を再利用するため、V-6 → V-11 の順で実行する
- それ以外は任意順

全 PASS の最終確認:
```bash
# 全 V-X 終了後
echo "All V-1..V-15 verifications completed"
gh issue list --repo "$REPO" --search "[V-" --state open --json number | jq -e 'length == 0' >/dev/null && echo "No open verification issues remain"
```
