---
name: session-closer
description: |
  capture mode (--capture): Decision を抽出・確認・投稿し、セッション継続。
  close mode (default): capture フロー + summary 投稿 + cross-issue scope の memory 昇格。
triggers:
  - session-closer
  - セッションを閉じて
  - セッション終了
  - session log
  - ログを残して
  - capture decisions
  - close session
  - decisions まとめて
---

# session-closer skill

claude-issueops プラグインの **書き戻し側メインエントリ** スキル。Claude Code セッション中に下された Decision を Issue コメントに persistent memory として書き戻し、close モードでは要約と cross-issue scope の memory 昇格まで実行する。

## モード

- `--capture`: capture モード。Decision を抽出・確認・投稿してセッションへ制御を戻す。要約投稿と memory 昇格はスキップ。
- 引数なし: close モード (既定)。capture フロー全体に加え、summary コメント投稿と cross-issue scope の memory 昇格を実行。

## アーキテクチャ概観

3 層構造で実装される:

1. **Skill 層 (本ドキュメント / Claude Code セッション内)**: LLM 推論、`AskUserQuestion` でのユーザー対話、subcommand のオーケストレーション。
2. **Bin adapter 層 (`bin/session_closer.py`)**: stdin/stdout JSON で skill とやりとりする Python 唯一のエントリポイント。subcommand を dispatch するだけで業務ロジックは持たない。
3. **Pure module 層 (`src/issueops/`)**: 副作用を持たない関数群 (`session_closer`, `transcript_reader`, `decision_extractor`, `dedup_checker`, `issue_resolver`, `state_writer`, `pending_decisions`, `gh_adapters`, `memory_escalate` 等)。

**重要**: `AskUserQuestion` は **必ずこの SKILL.md (Claude Code セッション内) から呼ぶ**。Python 側からは呼ばない。Python は対話結果 (`UserDecision[]` 等) を入力 JSON として受け取り、結果を出力 JSON として返すだけの純粋関数として設計されている。

## Skill ↔ bin Contract (stdin/stdout JSON, schema_version: 1)

### 共通入力スキーマ

```jsonc
{
  "schema_version": 1,
  "subcommand": "<subcommand 名>",
  "session_id": "<sid>",
  "project_dir": "/abs/path",
  // subcommand 固有のフィールド
}
```

### 共通出力スキーマ

```jsonc
// 成功時
{
  "schema_version": 1,
  "ok": true,
  "result": { /* subcommand 固有 */ },
  "warnings": ["..."]
}

// 失敗時
{
  "schema_version": 1,
  "ok": false,
  "error": {
    "kind": "transcript-missing" | "issue-resolution" | "gh-failure" | "extractor-parse" | "internal",
    "message": "...",
    "gh_failure_kind": "auth" | "network" | "rate-limit" | "unknown",
    "hint": "gh auth status を実行してください"
  }
}
```

`schema_version` の不一致を検出したら、即座にエラーで止めること。バージョン違いの bin と通信し続けない。

## Subcommand 一覧 (8 種)

すべて `bin/session_closer.py` に対し `echo '<JSON>' | uv run python bin/session_closer.py` で叩く。

### 1. `read-transcript`

transcript ファイルを `offset` 以降のバイト範囲で読み出す。

```jsonc
// in
{
  "schema_version": 1,
  "subcommand": "read-transcript",
  "session_id": "abc123",
  "project_dir": "/repo",
  "transcript_path": "/repo/.claude/projects/abc123/transcript.jsonl",
  "offset": 0
}
// out
{
  "schema_version": 1,
  "ok": true,
  "result": { "content": "...", "end_offset": 12345 }
}
```

### 2. `resolve-issue`

対象 Issue 番号を Tier 1 (`status:in-progress` ラベル) → branch hint 交差 → Tier 2 (branch pattern) の順で解決。曖昧なら `ambiguous_candidates` を返すので、SKILL.md 側で `AskUserQuestion (single)` を呼んで再投入する。

```jsonc
// in
{
  "schema_version": 1,
  "subcommand": "resolve-issue",
  "session_id": "abc123",
  "project_dir": "/repo",
  "branch": "feat/8-session-closer-skill"
}
// out (確定)
{ "schema_version": 1, "ok": true, "result": { "issue_number": 8, "tier": "tier1" } }
// out (曖昧)
{ "schema_version": 1, "ok": true, "result": { "ambiguous_candidates": [7, 8, 12] } }
```

### 3. `filter-dedup`

Tier 1 (ローカル `captured_slugs`) と Tier 2 (Issue 既存コメントの marker scan) で候補を除外。Tier 2 の `gh` 失敗時は `tier2_skipped: true` を返し abort しない。

```jsonc
// in
{
  "schema_version": 1,
  "subcommand": "filter-dedup",
  "session_id": "abc123",
  "project_dir": "/repo",
  "issue_number": 8,
  "candidates": [ /* Candidate[] */ ],
  "captured_slugs": ["already-posted-slug"]
}
// out
{
  "schema_version": 1,
  "ok": true,
  "result": { "filtered_candidates": [ /* ... */ ], "tier2_skipped": false }
}
```

### 4. `post-decisions`

`UserDecision[]` を `gh issue comment` で投稿。**state は触らない** (commit-state と分離)。部分失敗を許容し `posted_slugs[]` と `failed_slugs[]` を返す。

```jsonc
// in
{
  "schema_version": 1,
  "subcommand": "post-decisions",
  "session_id": "abc123",
  "project_dir": "/repo",
  "issue_number": 8,
  "user_decisions": [
    { "candidate": { /* ... */ }, "final_scope": "issue" }
  ]
}
// out
{
  "schema_version": 1,
  "ok": true,
  "result": {
    "posted_slugs": ["use-bin-adapter"],
    "failed_slugs": [
      { "slug": "isolate-gh", "gh_failure_kind": "auth", "hint": "gh auth status を実行してください" }
    ]
  }
}
```

### 5. `commit-state`

state file の merge update を atomic write で実行する **唯一のチャネル**。`patch` の中身は SKILL.md がユーザー選択を反映して組み立てる (State Writes Table 参照)。

```jsonc
// in
{
  "schema_version": 1,
  "subcommand": "commit-state",
  "session_id": "abc123",
  "project_dir": "/repo",
  "patch": {
    "skill_ran_at": "2026-04-26T05:30:00Z",
    "last_processed_offset": 12345,
    "captured_slugs": ["use-bin-adapter"]
  }
}
// out
{ "schema_version": 1, "ok": true, "result": { "state_path": "/repo/session-state/abc123.json" } }
```

### 6. `summary`

close モードでのみ呼ぶ。`<!-- claude-issueops:session-closer:summary:<session_id> -->` marker の既存検査 → 未投稿なら summary コメントを投稿。idempotent。

```jsonc
// in
{
  "schema_version": 1,
  "subcommand": "summary",
  "session_id": "abc123",
  "project_dir": "/repo",
  "issue_number": 8,
  "captured_slugs_total": ["use-bin-adapter", "isolate-gh"]
}
// out
{ "schema_version": 1, "ok": true, "result": { "posted_summary": true } }
// または
{ "schema_version": 1, "ok": true, "result": { "skipped": "idempotent" } }
```

### 7. `escalate`

`final_scope == "cross-issue"` の Decision のみを Claude memory ディレクトリに昇格。`memory_escalate.write_memory_file` + `update_memory_index` を呼ぶ。

```jsonc
// in
{
  "schema_version": 1,
  "subcommand": "escalate",
  "session_id": "abc123",
  "project_dir": "/repo",
  "decisions": [ /* PostedDecision[] with final_scope=cross-issue */ ],
  "memory_dir": "/Users/u/.claude/projects/abc/memory"
}
// out
{ "schema_version": 1, "ok": true, "result": { "escalated_paths": ["/Users/u/.claude/projects/abc/memory/reference_use-bin-adapter.md"] } }
```

### 8. `save-pending`

gh 失敗で「ローカルに保存」を選んだ未投稿 Decision を `<sid>.pending-decisions.json` に追記。schema_version=1 を含む。

```jsonc
// in
{
  "schema_version": 1,
  "subcommand": "save-pending",
  "session_id": "abc123",
  "project_dir": "/repo",
  "issue_number": 8,
  "decisions": [ /* UserDecision[] (未投稿分) */ ]
}
// out
{ "schema_version": 1, "ok": true, "result": { "pending_path": "/repo/session-state/abc123.pending-decisions.json" } }
```

## オーケストレーション 10 ステップ

以下を SKILL.md (= 本セッション内の Claude) が忠実に順番通りに実行する。各ステップで返ってきた `ok: false` は即座にユーザーに状況を伝え、必要に応じて中断する。

### Step 1. モード決定

引数 `--capture` の有無を確認する。あれば capture モード、なければ close モード。

### Step 2. transcript の差分読み込み

state file から `last_processed_offset` (既定 0) を読み、`read-transcript` を呼ぶ。返ってきた `content` を LLM プロンプトに食わせて Decision 候補 (`Candidate[]`) を JSON で抽出する。slug は kebab-case、4 フィールド (what / why / alternatives / consequences) すべて非空、`scope_hint` は `"issue"` か `"cross-issue"` のどちらかで初期推定。JSON parse 失敗は `extractor-parse` エラーで abort。

### Step 3. 対象 Issue の解決

`resolve-issue` を呼ぶ。

- `issue_number` が返ってきたらそれを使う。
- `ambiguous_candidates: [7, 8, 12]` が返ってきたら **`AskUserQuestion (single select)`** で「どの Issue に投稿しますか?」とユーザーに選ばせる。選択結果を `branch_pattern` か `--issue-number-override` 相当の追加フィールドで再投入。
- ユーザーがキャンセル / どの tier でも確定不能 → abort。`commit-state` で `skill_ran_at` のみ patch する (Issue 解決失敗で skill が起動した事実は残す)。

### Step 4. 二段階 dedup

`filter-dedup` に `candidates` と `captured_slugs` を渡す。

- `filtered_candidates` が空 → 「新しい決定は検出されませんでした」とユーザーに通知して終了。`commit-state` で `skill_ran_at` のみ patch。
- `tier2_skipped: true` (gh 失敗) → 警告だけ表示し Tier 1 のみで続行。

### Step 5. ユーザー承認 + scope 確定

**`AskUserQuestion (multiSelect: true)`** で各候補を提示する。ラベルは `slug` + `what` の冒頭 80 文字に切り詰めた 1 行サマリ。同時に各候補に対し `scope` の二択 (`issue` / `cross-issue`) を表示し、LLM 推定値を初期選択にする。承認 + scope 確定後、`UserDecision[]` (`{candidate, final_scope}`) を構築する。

全候補が却下されたら「ユーザーが全候補を却下しました」と表示して終了し、`commit-state` で `skill_ran_at` のみ patch。

### Step 6. Decision を Issue に投稿

`post-decisions` を呼ぶ。返ってきた `posted_slugs[]` と `failed_slugs[]` を保持する。**この時点では state は変わっていない** (post-decisions は state に触らない)。

### Step 7. 失敗時の 3 択フォールバック

`failed_slugs` が空でなければ **`AskUserQuestion (single select)`** で `[ローカルに保存して後で再投稿, 破棄, 中断]` の 3 択を提示する。

- **保存**: `save-pending` を呼んで未投稿 Decision を `<sid>.pending-decisions.json` に追記。
- **破棄**: 何もしない (drop)。
- **中断**: 後段の summary / escalate を呼ばずにフローを止める。`captured_slugs` には成功分のみ追加。

### Step 8. state を atomic に更新

ユーザー選択に応じた `patch` を State Writes Table から組み立て、`commit-state` を呼ぶ。

| シナリオ | `skill_ran_at` | `last_processed_offset` | `captured_slugs` |
|----------|:--:|:--:|:--:|
| 全成功 | now | end_offset | append (成功 slug) |
| 部分失敗 → 保存 | now | end_offset | append (成功分) |
| 部分失敗 → 破棄 | now | end_offset | append (成功分) |
| 部分失敗 → 中断 | now | (更新しない) | append (成功分) |
| 全候補却下 | now | (更新しない) | -- |
| 候補 0 件で early exit | now | end_offset | -- |
| Issue 解決失敗 | now | -- | -- |
| transcript 不在 | -- | -- | -- |

### Step 9. close モード追加処理 (capture モードはスキップ)

close モードの場合のみ、capture 累計が 1 件以上あるなら以下を実行:

1. `summary` を呼ぶ。`<!-- claude-issueops:session-closer:summary:<session_id> -->` marker は session_id 込みで idempotent。既出なら自動 skip。
2. `final_scope == "cross-issue"` の Decision を集めて `escalate` を呼ぶ。memory ファイル書き込み失敗は警告のみで Issue コメントはロールバックしない。

### Step 10. 1 行サマリを stdout に出力

最終出力は次の形式:

```
Posted N decisions, escalated M to memory, skipped K duplicates.
```

エラーで abort した場合は理由 + 復旧示唆を 1 画面に収まる長さで出す。長い診断は `${CLAUDE_PROJECT_DIR}/session-state/<session_id>.skill.log` に書き出す。

## エラーハンドリング契約

| `error.kind` | 意味 | SKILL.md の動作 |
|-------------|------|---------------|
| `transcript-missing` | transcript ファイルが見つからない | 状況を表示して abort、state は触らない |
| `issue-resolution` | 全 tier で Issue 番号確定不能 | `commit-state` で `skill_ran_at` のみ patch、ユーザー誘導表示 |
| `gh-failure` | gh コマンド失敗。`gh_failure_kind` 4 種に分類済 | post-decisions 内なら 3 択フォールバック、それ以外は警告で続行 (Tier 2 dedup) |
| `extractor-parse` | LLM 抽出 JSON 不正 | 「再 invoke してください」を表示、state は触らない |
| `internal` | bin 内部例外 | スタックトレース要約 + log path を表示、state は触らない |

`gh_failure_kind == "auth"` の場合、必ず `hint` を併記してユーザーに表示する (`gh auth status を実行してください` 等)。

## 連続呼び出し安全性

- 2 回目の `--capture` は Tier 1 + Tier 2 dedup により「すべて投稿済み」と判定し clean に終了する。
- SIGINT で `commit-state` 前に中断された場合、state file は atomic write のため前回値を保持する。再 invoke で安全。
- `commit-state` は state 更新の唯一のチャネル。post 後に対話を挟む構造のため、`post-decisions` と `commit-state` を分離している。

## 検証フィクスチャ (任意)

`CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/<v-id>.json` と `CLAUDE_ISSUEOPS_VERIFICATION_MODE=1` の **両方** を設定したセッションでは、`AskUserQuestion` 応答を fixture から読み込んで bypass する。Claude が L3 verification を一気通貫で自動実行するためのもの。本番運用では絶対に有効化しない。
