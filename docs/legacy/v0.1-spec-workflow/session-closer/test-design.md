# session-closer skill - テスト設計書

**Spec ID**: `SPEC-20260425-session-closer`
**ベース仕様**: 新規機能 (Issue #8、Epic #7 の最後の機能要素)
**種別**: 新規機能

---

## Test Strategy Overview

session-closer は claude-issueops プラグインの **書き戻し側のメインエントリ skill** であり、Claude Code セッション内での LLM 抽出・`AskUserQuestion` ユーザー対話・`gh` 投稿・state ファイル更新・memory 昇格を一連で扱う。テスト戦略は **3 層** に分ける:

- **L1 (Unit)**: pure module 単位の関数テスト。pytest + 依存性注入で完結。`subprocess`、`gh`、Claude Code セッションを一切使わない
- **L2 (Integration)**: orchestrator (`run_capture` / `run_close`) を callable 注入で end-to-end 駆動。bin と SKILL.md は介さず、Python レイヤだけで全フローを再現する
- **L3 (Verification)**: bin adapter の subprocess 起動と SKILL.md 経由の Claude Code セッション動作を、**Claude Code セッション内で Claude (AI) が実行する verification 手順** として記述する。実行主体は Claude 自身であり、人間ユーザーが手で叩く前提ではない。各 verification は (1) Claude が事前条件を整備 (テスト用 issue を gh で開く / state file を任意の状態にセット 等) し、(2) skill / bin を起動し、(3) 副作用の落ち先 (state file / gh comment / memory file / 標準出力) を Claude が読み取り判定基準と一致するかを `grep` / `jq` / `cat` 等で機械的に確認する

**設計の根拠**: claude-issueops 既存 hook 群 (PreCompact / UserPromptSubmit / SessionEnd) と同じ層分けを踏襲することで、テスト戦略の一貫性とレビュー容易性を確保する。Python レイヤだけで Requirements の全 AC を検証できるよう orchestrator API を依存性注入対応で設計しているため、L1+L2 の自動テストでカバレッジを 100% 近くまで押し上げる。L3 は「Python だけでは到達できない範囲 = subprocess 経由の bin adapter 起動 / SKILL.md オーケストレーション全体 / 実 gh CLI / 実 memory dir / 複数プロセスを起動した race 観察」を Claude が同一セッション内で実行する形に絞る。

> **重要**: L3 の verification は Claude Code セッション内で Claude が `Bash` ツール等を使って実行する。skill 自体の起動 (`/claude-issueops:session-closer --capture` 等) は Claude Code 上で skill 呼び出し機能を介して行う。`AskUserQuestion` 経由のユーザー対話部分は実機で人間の応答が必要となるため、その応答内容を仕様化した `verification-fixtures/` (例: `respond-multiselect-all.json`) を Claude が用意し、SKILL.md に注入できるよう実装側で hook できる仕組みを Phase 4 (Tasks) で同時に整備する。これにより L3 も実質的に Claude が一気通貫で実行できる。

---

## Test Levels

| Level | Name | Automation | Tools |
|-------|------|------------|-------|
| L1 | Unit (pure module) | 自動 | pytest, dataclasses, tmp_path fixture |
| L2 | Integration (orchestrator) | 自動 | pytest, callable 注入, freezegun (時刻固定) |
| L3 | Verification (bin / skill, Claude が実行) | Claude による自動実行 | Bash, gh, jq, Claude Code skill 呼び出し, AskUserQuestion fixture |

> Level の選び方: claude-issueops は Claude Code plugin であり、ランタイム動作 (skill 発動、`AskUserQuestion`、hook 連携) はセッション内でしか駆動できない。L1+L2 で論理を厚くカバーし、L3 は「subprocess を含む副作用」「複数プロセス race」「skill 実機」だけに絞り、それを Claude Code セッション内で Claude が一連の Bash コマンド + skill 起動として実行する。CI で安定実行できる範囲 (L1+L2) と Claude 実機セッション内で実行する範囲 (L3) を分離する。

---

## 要件カバレッジマトリクス

各 Acceptance Criteria が **どの Test Level でカバーされるか** を明示する。空欄 (`-`) はそのレベルでカバーしないことを意味し、セクション末尾の備考で理由を述べる。

### 機能要件 1: capture モードによる Decision 投稿 (R-1)

| AC# | Acceptance Criteria | L1 | L2 | L3 | 期待値 |
|-----|---------------------|:--:|:--:|:--:|--------|
| 1.1 | `--capture` 呼び出しで抽出→確認→投稿、session 継続 | - | T-101 | V-1 | 全フロー成功時 `posted_slugs` が gh に渡る |
| 1.2 | 候補 0 件で early exit (`--capture`) | - | T-102 | - | 投稿なし、`{posted: 0}` 出力 |
| 1.3 | 部分成功時 `captured_slugs` に成功 slug のみ追記 | T-21 | T-103 | V-2 | 5 候補中 3 成功時、追記は 3 slug |
| 1.4 | 全候補処理完了後に `last_processed_offset` 更新 | T-61 | T-104 | - | 中断時は更新しない (State Writes Table) |
| 1.5 | 終了時に `skill_ran_at` を ISO-8601 UTC で書き込み | T-62 | T-105 | V-3 | SessionEnd hook が skip 判定できる |

### 機能要件 2: close モードによる Decision + Summary + Memory escalation (R-2)

| AC# | Acceptance Criteria | L1 | L2 | L3 | 期待値 |
|-----|---------------------|:--:|:--:|:--:|--------|
| 2.1 | close (default) で capture フローを実行 | - | T-111 | V-4 | run_close が run_capture 相当を呼ぶ |
| 2.2 | summary marker `summary:<sid>` で 1 件以上時に投稿 | T-32 | T-112 | V-5 | 0 件時は投稿しない (marker フォーマット検証) |
| 2.3 | summary 投稿前に既存 marker 検査 (idempotency) | T-31 | T-113 | - | 同一 sid の summary が既存なら skip |
| 2.4 | cross-issue scope の Decision を memory 昇格起動 | - | T-114 | V-6 | run_close が R-8 委譲を実行 |
| 2.5 | 0 件 AND cross-issue 0 件で summary + escalation skip | - | T-115 | - | `skill_ran_at` のみ更新 |

### 機能要件 3: transcript からの Decision 候補抽出 (R-3)

| AC# | Acceptance Criteria | L1 | L2 | L3 | 期待値 |
|-----|---------------------|:--:|:--:|:--:|--------|
| 3.1 | transcript を `last_processed_offset` 以降だけ読む | T-01 | T-101 | - | offset=0 と offset=N で範囲が変わる |
| 3.2 | Candidate を `{slug, what, why, alternatives, consequences, scope_hint}` 形式で生成 | T-11 | - | - | parse_candidates_json が適切に dataclass 化 |
| 3.3 | 不正候補 (slug 非 kebab-case / 必須 field 空) を破棄 | T-12 | - | - | 4 件投入で 2 件のみ通過 |
| 3.4 | transcript 不在で abort、state 変更なし | T-02 | T-106 | - | FileNotFoundError → State Writes Table 整合 |
| 3.5 | scope を AskUserQuestion でユーザー選択させる (UserDecision の `final_scope` に保持) | T-13 | T-116 | V-7 | scope_hint と final_scope が独立 |

### 機能要件 4: ユーザー確認ゲート (R-4)

| AC# | Acceptance Criteria | L1 | L2 | L3 | 期待値 |
|-----|---------------------|:--:|:--:|:--:|--------|
| 4.1 | AskUserQuestion (multiSelect) で各候補を独立選択 | - | - | V-7 | skill 実機でしか検証できない |
| 4.2 | 却下候補は `captured_slugs` 非追加 | - | T-117 | - | UserDecision[] に却下分が含まれない前提で run_capture が動く |
| 4.3 | 全候補却下時 `skill_ran_at` のみ更新 (offset/slugs 不変) | - | T-118 | - | State Writes Table 行 5 と整合 |

### 機能要件 5: 二段階 Duplicate prevention (R-5)

| AC# | Acceptance Criteria | L1 | L2 | L3 | 期待値 |
|-----|---------------------|:--:|:--:|:--:|--------|
| 5.1 | Tier 1 で `captured_slugs` 既出を除外 | T-41 | T-119 | - | 5 候補中 2 既出 → 3 件残る |
| 5.2 | Tier 2 で gh 取得後 marker_parser 経由で除外 | T-42 | T-120 | V-8 | 既存 Issue コメントから既出抽出 |
| 5.3 | gh 失敗時は Tier 1 のみで継続 + 警告 | - | T-121 | - | tier2_skipped=true が出力 |

### 機能要件 6: ターゲット Issue の解決 (R-6)

| AC# | Acceptance Criteria | L1 | L2 | L3 | 期待値 |
|-----|---------------------|:--:|:--:|:--:|--------|
| 6.1 | Tier 1 (label) 1 件で確定 | T-51 | - | - | resolve_target_issue 即返却 |
| 6.2 | Tier 1 複数 + branch hint で交差確定 | T-52 | - | - | 状態遷移表のケース 4 |
| 6.3 | Tier 1 0 or 不一致時 Tier 2 fallback | T-53 | - | - | branch_resolver 委譲 |
| 6.4 | 両 tier 経由でも複数なら AmbiguousResolution を返す | T-54 | - | V-9 | SKILL.md がユーザー選択分岐 |
| 6.5 | 一意確定不可 → IssueResolutionError | T-55 | T-122 | - | abort 時 `skill_ran_at` のみ |

### 機能要件 7: state ファイルとの統合 (R-7)

| AC# | Acceptance Criteria | L1 | L2 | L3 | 期待値 |
|-----|---------------------|:--:|:--:|:--:|--------|
| 7.1 | sibling fields を破壊しない merge update | T-63 | T-123 | - | briefing_done / pending_restore 等を保つ |
| 7.2 | `state_save.state_file_path` の path traversal 検証を流用 | T-64 | - | - | 不正 session_id で ValueError |
| 7.3 | state file 不在時、自フィールドのみで新規作成 | T-65 | - | - | 他 hook 用デフォルト値を埋めない |
| 7.4 | 不正 JSON は quarantine 退避 + 警告 + 新規作成 | T-66 | T-124 | V-10 | `*.corrupt-<ISO8601>` ファイル生成 |

### 機能要件 8: Memory escalation (cross-issue scope のみ) (R-8)

| AC# | Acceptance Criteria | L1 | L2 | L3 | 期待値 |
|-----|---------------------|:--:|:--:|:--:|--------|
| 8.1 | cross-issue 時 `write_memory_file` 呼び出し | - | T-125 | V-11 | `reference_<slug>.md` が生成 |
| 8.2 | `update_memory_index` で MEMORY.md idempotent 追記 | - | T-126 | - | 同一 slug 二度呼びで重複行なし |
| 8.3 | issue scope は memory に触らない | - | T-127 | - | memory_dir に変更なし |
| 8.4 | memory 書き込み失敗で投稿はロールバックしない | - | T-128 | - | warning に追加、posted_slugs は維持 |

> R-8.1〜8.3 は既存 `tests/test_memory_escalate.py` で個別関数のテスト済。本テスト設計では orchestrator (run_close) からの呼び出しが正しい条件で起きることに集中する。

### 機能要件 9: `gh` 失敗時の Graceful degradation (R-9)

| AC# | Acceptance Criteria | L1 | L2 | L3 | 期待値 |
|-----|---------------------|:--:|:--:|:--:|--------|
| 9.1 | gh 失敗を 4 種に分類 | T-71 | - | - | classify_gh_failure の分岐網羅 |
| 9.2 | auth 失敗時に hint 付与 | T-72 | - | V-12 | GhFailure.hint 含む |
| 9.3 | 投稿失敗時 AskUserQuestion 3 択 | - | - | V-13 | skill 実機でしか検証できない |
| 9.4 | 「保存」で pending-decisions.json に追記 | T-81 | T-129 | - | append_pending_decisions が動く |
| 9.5 | 「破棄」で記録なし drop | - | T-130 | - | state も pending も触れない |
| 9.6 | 「中断」で `skill_ran_at` のみ更新、それ以降の処理停止 | - | T-131 | - | State Writes Table 行 4 |

### 機能要件 10: Skill 登録と発見性 (R-10)

| AC# | Acceptance Criteria | L1 | L2 | L3 | 期待値 |
|-----|---------------------|:--:|:--:|:--:|--------|
| 10.1 | SKILL.md frontmatter にトリガー列挙 | T-91 | - | V-14 | YAML parse + キー検証 |
| 10.2 | `--capture` なしで close mode | - | - | V-15 | skill 実機 |
| 10.3 | `--capture` 付きで capture mode (summary/escalate skip) | - | - | V-15 | skill 実機 |

> **備考**: L1 でカバーしない AC は、性質上「callable 注入で全フロー駆動」が必要なため L2 で扱う。L3 のみのものは「skill (Claude Code セッション) または subprocess 実機実行」が必要なため自動化不能。L2 でカバーしている AC も、L3 にスポット検証 (V-X) を残しているのは「副作用が実環境で本当に出るか」を最低 1 度は確認するため (DI 偽装と現実のズレを潰す)。

---

## Level 1: Unit (pure module)

各モジュールの純粋関数を pytest + tmp_path / freezegun で検証する。subprocess・gh・MCP は一切使わない。

| Test ID | Test Function | Verifies | Requirements |
|---------|---------------|----------|--------------|
| T-01 | `test_read_transcript_since_default_offset` | `offset=0` で全文を読み、`end_offset` が末尾を指す | R-3.1 |
| T-02 | `test_read_transcript_missing_raises` | 不在ファイルで `FileNotFoundError` を上位伝播 | R-3.4 |
| T-03 | `test_read_transcript_partial_offset` | `offset>0` で先頭をスキップ、内容と end_offset 整合 | R-3.1 |
| T-11 | `test_parse_candidates_valid` | 有効 JSON が Candidate[] に変換される | R-3.2 |
| T-12 | `test_parse_candidates_invalid_dropped` | 不正 slug / 空 field のものを破棄 | R-3.3 |
| T-13 | `test_candidate_to_decision_strips_scope` | scope_hint を含まない Decision を生成 | R-3.5 (型整合) |
| T-14 | `test_parse_candidates_json_decode_error` | JSON 解析失敗で ValueError | R-3.2 (parse error path) |
| T-21 | `test_run_capture_partial_success_state` | gh 投稿の per-slug 成否を `captured_slugs` に正しく反映 | R-1.3 |
| T-31 | `test_summary_marker_idempotent` | 既存 `summary:<sid>` 検出時に skip | R-2.3 |
| T-32 | `test_summary_marker_format` | marker に session_id が含まれる | R-2.2 |
| T-41 | `test_filter_local_excludes_captured` | captured_slugs に既出のものを除外 | R-5.1 |
| T-42 | `test_filter_remote_excludes_marker_parsed` | 既存 Decision[] と一致する slug を除外 | R-5.2 |
| T-43 | `test_filter_local_empty_captured_passthrough` | 空 captured_slugs ですべて通過 | R-5 (境界) |
| T-51 | `test_resolve_tier1_single_hit` | Tier 1 が 1 件で即確定 | R-6.1 |
| T-52 | `test_resolve_tier1_multiple_intersect_branch` | Tier 1 多 + branch hint 交差で確定 | R-6.2 |
| T-53 | `test_resolve_tier1_zero_then_tier2` | Tier 1 が 0 件で Tier 2 fallback | R-6.3 |
| T-54 | `test_resolve_ambiguous_returns_candidates` | どの tier でも一意決定不可 → AmbiguousResolution | R-6.4 |
| T-55 | `test_resolve_total_failure_raises` | Tier 1+2+ user 選択でも決まらない → IssueResolutionError | R-6.5 |
| T-61 | `test_state_writer_offset_atomic` | `last_processed_offset` 更新で `os.replace` を経由 | R-1.4, R-7 (atomic) |
| T-62 | `test_state_writer_skill_ran_at_isoformat` | `skill_ran_at` が ISO-8601 UTC 形式で書かれる | R-1.5 |
| T-63 | `test_state_writer_preserves_siblings` | 既存 briefing_done / pending_restore を破壊しない | R-7.1 |
| T-64 | `test_state_writer_invalid_session_id_raises` | path traversal 試行で ValueError (state_save 経由) | R-7.2 |
| T-65 | `test_state_writer_creates_minimal` | 不在ファイルから自フィールドのみで新規作成 | R-7.3 |
| T-66 | `test_state_writer_quarantines_corrupt_json` | 不正 JSON ファイルを `*.corrupt-<ISO8601 microsec>` にリネーム | R-7.4 |
| T-67 | `test_state_writer_tmp_uniqueness` | tmp 名に pid+monotonic_ns+uuid4 が入り並行衝突しない | NFR-Reliability (atomic write) |
| T-71 | `test_classify_gh_failure_4_kinds` | network/auth/rate-limit/unknown を stderr 文字列で分類 | R-9.1 |
| T-72 | `test_classify_gh_failure_auth_hint` | auth 分類時に hint が付与される | R-9.2 |
| T-81 | `test_pending_decisions_append_idempotent` | 既存ファイルに `entries` を追記、schema_version=1 | R-9.4 |
| T-82 | `test_pending_decisions_unsafe_session_id` | 不正 session_id で ValueError | R-9 + state_save 流用 |
| T-91 | `test_skill_md_frontmatter_parse` | SKILL.md の YAML frontmatter に triggers 列挙 | R-10.1 |
| T-92 | `test_path_utils_state_file_path_normal` | `state_file_path` が正常 session_id でパスを構築 | R-7.2 (パス検証の流用先) |
| T-93 | `test_path_utils_unsafe_session_id_raises` | `/`, `\`, `..` を含む session_id で ValueError | R-7.2 |
| T-94 | `test_path_utils_empty_session_id_raises` | 空文字列で ValueError | R-7.2 |
| T-95 | `test_verification_fixture_loads_when_both_set` | 環境変数が両方揃ったときに JSON を読む | Test Design Key Decision #7 |
| T-96 | `test_verification_fixture_rejects_path_outside_dir` | path traversal を拒否 + stderr 警告 | Test Design Key Decision #7 |
| T-97 | `test_verification_fixture_rejects_when_mode_unset` | MODE=1 が無いと bypass 無視 + stderr 警告 | Test Design Key Decision #7 |
| T-98 | `test_verification_fixture_silent_when_neither_set` | 両方未設定時は警告なしで None | Test Design Key Decision #7 |

合計 **L1: 37 テスト** (T-01〜T-91 = 30 件 + T-92〜T-94 path_utils = 3 件 + T-95〜T-98 verification_fixture = 4 件、機能領域ごとに 10 番台でグループ化、欠番は意図的)

---

## Level 2: Integration (orchestrator)

`run_capture / run_close` を callable 注入で end-to-end 駆動する。bin / SKILL.md は介さず、Python レイヤだけで Requirements の挙動を再現する。

| Test ID | Test Function | Verifies | Requirements |
|---------|---------------|----------|--------------|
| T-101 | `test_run_capture_happy_path` | 全 5 候補成功投稿 + state 更新 | R-1.1, R-3.1 |
| T-102 | `test_run_capture_no_candidates_early_exit` | 候補 0 件で skill_ran_at のみ更新 | R-1.2 |
| T-103 | `test_run_capture_partial_failure` | 5 候補中 3 成功 → captured_slugs に 3 slug | R-1.3 |
| T-104 | `test_run_capture_offset_committed_on_completion` | 全候補処理後のみ offset 更新 | R-1.4 |
| T-105 | `test_run_capture_writes_skill_ran_at_always` | エラー以外のすべての終了で skill_ran_at | R-1.5 |
| T-106 | `test_run_capture_transcript_missing_no_state_change` | transcript 不在で何も書かない (T-02 の上位例外を catch) | R-3.4 |
| T-107 | `test_run_capture_sigint_keeps_previous_state` | SIGINT 等で `commit-state` が呼ばれず終了した場合、state file が atomic write のため前回値を保持 (`merge_update_state` を `KeyboardInterrupt` で raise させ、target ファイルが書き換わっていないことを assert) | NFR-Reliability (atomic), R-7 |
| T-108 | `test_user_question_payload_for_failure_3choices` | gh 失敗時に SKILL.md へ返す `failed_slugs` 構造が 3 択提示に必要なフィールドを含む (`gh_failure_kind`, `hint`, `failed_slug_summaries`) | R-9.3 (構造のみ、実 UI は L3) |
| T-109 | `test_user_question_payload_for_multiselect` | filter-dedup の戻り値が AskUserQuestion multiSelect に渡せる構造 (各候補の `slug + what 1 行サマリ`) | R-4.1 (構造のみ) |
| T-110 | `test_dedup_remote_failure_warning_message` | gh 失敗時の `tier2_skipped` 警告に失敗種別が含まれる | R-5.3 |
| T-111 | `test_run_close_invokes_capture_flow` | run_close が capture 部を実行 | R-2.1 |
| T-112 | `test_run_close_summary_when_decisions_posted` | 1 件以上投稿で summary を gh に渡す | R-2.2 |
| T-113 | `test_run_close_summary_idempotent` | 既存 marker 検出で skip | R-2.3 |
| T-114 | `test_run_close_escalates_cross_issue_only` | final_scope=cross-issue のみ memory 委譲 | R-2.4, R-8.1, R-8.3 |
| T-115 | `test_run_close_skips_when_zero_decisions` | 0 件時 summary+escalation skip | R-2.5 |
| T-116 | `test_user_decision_overrides_scope_hint` | UserDecision.final_scope が scope_hint と異なる場合の挙動 | R-3.5 |
| T-117 | `test_run_capture_rejected_candidates_skipped` | UserDecision[] にない候補は投稿対象外 | R-4.2 |
| T-118 | `test_run_capture_all_rejected_only_skill_ran_at` | 全却下時 skill_ran_at のみ更新 | R-4.3 |
| T-119 | `test_dedup_local_excludes_captured` | 注入された captured_slugs を `filter_local` に渡す | R-5.1 |
| T-120 | `test_dedup_remote_uses_marker_parser` | gh 取得 → parse_decisions → filter_remote の流れ | R-5.2 |
| T-121 | `test_dedup_gh_failure_falls_back_tier1_only` | gh 失敗で警告 + Tier 1 のみで継続 | R-5.3 |
| T-122 | `test_run_capture_issue_resolution_error` | IssueResolutionError → skill_ran_at のみ更新 | R-6.5 |
| T-123 | `test_run_capture_state_merge_preserves_siblings` | run_capture 経由で sibling fields 保持 | R-7.1 (実フロー検証) |
| T-124 | `test_run_capture_handles_corrupt_state` | quarantine + 新規作成で skill 継続 | R-7.4 |
| T-125 | `test_run_close_calls_write_memory_file_for_cross_issue` | memory_escalate.write_memory_file 呼び出し | R-8.1 |
| T-126 | `test_run_close_calls_update_memory_index_idempotent` | update_memory_index 呼び出し | R-8.2 |
| T-127 | `test_run_close_skips_memory_for_issue_scope` | issue scope のみで memory に触らない | R-8.3 |
| T-128 | `test_run_close_memory_failure_keeps_posted` | memory 例外でも posted_slugs 維持 | R-8.4 |
| T-129 | `test_run_capture_save_pending_on_failure` | 「保存」選択で pending_decisions に追記 | R-9.4 |
| T-130 | `test_run_capture_discard_on_failure` | 「破棄」で state も pending も変えない | R-9.5 |
| T-131 | `test_run_capture_abort_on_failure` | 「中断」で skill_ran_at のみ、後続候補処理停止 | R-9.6 |
| T-132 | `test_state_save_refactor_io_equivalence` | `state_save.save_pending_restore` が `state_writer` 経由でも、refactor 前と **書き込まれる JSON 構造が同一** であること (snapshot 比較)、および atomic write 下でも既存 sibling fields を破壊しないこと | NFR-Reliability + 既存 89 件互換 |
| T-133 | `test_session_end_refactor_io_equivalence` | `session_end.run_session_end` が `state_writer` 経由でも、書き込まれる `last_summary_at` フィールド等が refactor 前と同等。既存 `tests/test_session_end.py` (12 件) が green | NFR-Reliability + 既存 89 件互換 |
| T-134 | `test_pending_decisions_append_existing_file` | 既存 pending ファイルへの append (entries 末尾に追加、schema_version 維持) | R-9.4 |
| T-135 | `test_pending_decisions_issue_number_mismatch_defensive` | 異なる `issue_number` での defensive append (entries に別エントリ追加、警告ログ出力) | R-9.4 (defensive) |
| T-136 | `test_run_capture_post_without_commit_keeps_state` | **setup**: `gh_post_fn` は成功するが、`commit_state_fn` (state_writer.merge_update_state を wrap した callable) を意図的に `RuntimeError` で raise させる。**assert**: (1) `gh_post_comment` が呼ばれている (post-decisions が走った)、(2) state file の中身が事前 snapshot と完全一致 (前回値を保持)、(3) `<sid>.json.tmp.*` の残骸ファイルがないこと。これにより subcommand 分離 (post-decisions が単独で state を書かない) が機能していることを検証 | R-7 + Codex 再レビュー致命的 #1 |

合計 **L2: 36 テスト** (T-101〜T-136、欠番なし)

---

## Level 3: Verification (Claude による実機検証)

> Claude Code セッション内で Claude が一気通貫で実行する。各 V-X は **(1) 事前条件整備 → (2) skill / bin 起動 → (3) 副作用ファイルの確認** の 3 段で構成。skill のユーザー対話 (`AskUserQuestion`) は `verification-fixtures/<v-id>.json` にユーザー応答を fixture 化し、skill 実装側で `CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE` 環境変数があれば fixture を読む形にしておく (Phase 4 で実装タスク化)。実行手順 / 期待値 / 判定基準は Claude が `Bash` / `Read` / `Grep` ツールで自動評価できる粒度で記述する。

`VERIFICATION.md` には Claude 向けの実行レシピを書き出す (各 V-X を順次走らせるシェル/手順スクリプト)。

### V-1: capture mode の skill 実機起動 (R-1.1)

**事前条件 (Claude が整備):**
```bash
# (1) 検証用 issue を gh で作成し status:in-progress ラベルを付与
gh issue create --repo etoyama/claude-issueops --title "[V-1] verification" --body "verification target" --label status:in-progress
# (2) 決定候補を含む transcript を fixture から配置
cp tests/fixtures/transcripts/v1-capture.jsonl "${CLAUDE_PROJECT_DIR}/.claude/projects/<sid>/transcript.jsonl"
# (3) AskUserQuestion 応答 fixture を準備
export CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE=verification-fixtures/v1-approve-all.json
```

**実行 (Claude が skill を呼び出す):**
- Claude Code session 内で `/claude-issueops:session-closer --capture` を実行 (Skill ツール経由)

**期待される副作用:**
- gh の検証用 issue に `<!-- claude-issueops:decision:<slug> -->` 付きのコメントが N 件追加 (N = fixture で承認した候補数)
- `${CLAUDE_PROJECT_DIR}/session-state/<sid>.json` の `captured_slugs` 配列に N 件追記
- `state.skill_ran_at` が ISO-8601 UTC で書き込まれている
- skill の標準出力に `Posted N decisions, ...` の 1 行サマリが含まれる

**判定基準 (Claude が `Bash` ツールで機械評価):**
```bash
# gh comment 件数 == N
test "$(gh issue view "$ISSUE" --json comments | jq '[.comments[] | select(.body | startswith("<!-- claude-issueops:decision:"))] | length')" = "$N"
# state file の captured_slugs 件数 == N
test "$(jq '.captured_slugs | length' "$STATE_FILE")" = "$N"
# state.skill_ran_at が ISO-8601 形式
jq -e '.skill_ran_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")' "$STATE_FILE" >/dev/null
# skill 出力にサマリ行
grep -qE 'Posted [0-9]+ decisions' /tmp/v1-skill.log
```
4 つの assertion がすべて exit 0 を返せば V-1 PASS。

### V-2: 部分失敗時の state 整合 (R-1.3)

**事前条件 (Claude が整備):**
- 認証を意図的に壊す: `export GITHUB_TOKEN=invalid_token_for_v2` を Bash で実行 (`gh auth logout` は対話的なため使わない、`GITHUB_TOKEN` の上書きで gh 401 を再現)
- AskUserQuestion 応答 fixture: `verification-fixtures/v2-save-on-failure.json` (失敗時の 3 択で「保存」を選ぶ)

**実行:**
- Claude が `/claude-issueops:session-closer --capture` を skill 経由で起動 (出力を `/tmp/v2-skill.log` にリダイレクト)

**期待される副作用:**
- `$STATE_DIR/<sid>.pending-decisions.json` が新規作成され、未投稿候補の payload が `entries[].decisions` に格納
- `$STATE_DIR/<sid>.json` の `captured_slugs` は空または成功分のみ (失敗 slug は含まれない)
- skill の標準出力に `gh_failure_kind: auth` および `gh auth status を実行してください` のヒント文字列

**判定基準:**
```bash
test -f "$STATE_DIR/$SID.pending-decisions.json"
test "$(jq '.entries[0].decisions | length' "$STATE_DIR/$SID.pending-decisions.json")" -gt 0
grep -q 'gh_failure_kind: auth' /tmp/v2-skill.log
grep -q 'gh auth status' /tmp/v2-skill.log
```

**事後処理 (Claude が復旧):**
- `unset GITHUB_TOKEN` で環境変数を消す (元の認証は user の gh 設定が引き継ぐ)

### V-3: SessionEnd hook との skip 判定協調 (R-1.5)

> 元案では「Claude Code session を `/clear` 等で終了 → SessionEnd hook が走る」としていたが、それは同一 Claude 実行フロー内では成立しない (session 切断後に Claude が判定基準を評価できない)。代替として、SessionEnd hook の bin adapter (`bin/sessionend_hook.py`) を Claude が **直接 Bash で起動** することで「session 終了時の hook 動作」を再現する。

**事前条件 (Claude が整備):**
- 検証用 issue を新規作成、capture モードで 1 件投稿済の状態を作る (V-1 とほぼ同手順、AskUserQuestion fixture: `verification-fixtures/v3-approve-one.json`)
- pre-check: `gh issue view "$ISSUE" --json comments | jq '.comments | length'` を `PRE_COUNT` として記録

**実行 (Claude が Bash で hook を直接呼ぶ):**
```bash
echo '{"session_id":"'"$SID"'","cwd":"'"$PWD"'"}' | uv run python bin/sessionend_hook.py
```

**期待される副作用:**
- bin/sessionend_hook.py 内の orchestration が `state.skill_ran_at` を読んで fallback summary を skip
- gh の comment 数が変化なし (capture 由来の 1 件のみ、`session-end-fallback` marker 付きコメントは追加されない)

**判定基準:**
```bash
test "$(gh issue view "$ISSUE" --json comments | jq '.comments | length')" = "$PRE_COUNT"
test "$(gh issue view "$ISSUE" --json comments | jq '[.comments[] | select(.body | contains("session-end-fallback"))] | length')" = "0"
```

### V-4: close mode の追加フロー実機 (R-2.1, R-2.2, R-2.4)

**事前条件 (Claude が整備):**
- 検証用 issue を `status:in-progress` ラベル付きで新規作成
- 候補に `final_scope: cross-issue` を含む transcript fixture を配置
- AskUserQuestion fixture: `verification-fixtures/v4-mixed-scope.json` (2 件承認、うち 1 件を cross-issue で確定)

**実行:**
- Claude が `/claude-issueops:session-closer` (close mode) を skill 経由で実行

**期待される副作用:**
- capture フローが走り 2 件の Decision コメントが投稿される
- summary コメントが `<!-- claude-issueops:session-closer:summary:<sid> -->` marker 付きで追加される
- cross-issue scope の slug について `~/.claude/projects/<encoded>/memory/reference_<slug>.md` が生成される

**判定基準:**
```bash
test "$(gh issue view "$ISSUE" --json comments | jq '[.comments[] | select(.body | startswith("<!-- claude-issueops:decision:"))] | length')" = "2"
test "$(gh issue view "$ISSUE" --json comments | jq '[.comments[] | select(.body | contains("session-closer:summary:"))] | length')" = "1"
test -f "$MEMORY_DIR/reference_$SLUG.md"
```

### V-5: summary 投稿の 0 件スキップ (R-2.2)

**事前条件:**
- 検証用 issue 新規作成
- AskUserQuestion fixture: `verification-fixtures/v5-reject-all.json` (全候補却下)
- 投稿前の comment 数を記録 (`PRE_COUNT`)

**実行:**
- Claude が `/claude-issueops:session-closer` (close mode) を skill 経由で実行

**期待される副作用:**
- comment 数が変化しない (summary も Decision も投稿されない)
- state file には `skill_ran_at` のみ更新、`captured_slugs` は空のまま

**判定基準:**
```bash
test "$(gh issue view "$ISSUE" --json comments | jq '.comments | length')" = "$PRE_COUNT"
# captured_slugs は空または欠落
test "$(jq '.captured_slugs // []' "$STATE_FILE" | jq 'length')" = "0"
jq -e '.skill_ran_at' "$STATE_FILE" >/dev/null
```

### V-6: cross-issue 昇格の memory file 生成 (R-2.4, R-8.1, R-8.2)

**事前条件:**
- 検証用 issue 新規作成
- AskUserQuestion fixture: `verification-fixtures/v6-cross-issue.json` (1 件承認、final_scope=cross-issue)
- `~/.claude/projects/<encoded>/memory/reference_<slug>.md` を事前削除して再生成を観測可能に

**実行:**
- Claude が `/claude-issueops:session-closer` を 2 回連続で実行 (idempotency 観測のため)

**期待される副作用:**
- 1 回目で `reference_<slug>.md` 生成 + `MEMORY.md` に index 行追加
- 2 回目では Tier 2 dedup により decision 投稿は skip、memory file/index も変化しない (idempotent)

**判定基準:**
```bash
test -f "$MEMORY_DIR/reference_$SLUG.md"
test "$(grep -c "reference_$SLUG.md" "$MEMORY_DIR/MEMORY.md")" = "1"
# 1 回目と 2 回目で MD5 が同一
HASH1=$(md5sum "$MEMORY_DIR/reference_$SLUG.md" | awk '{print $1}')
# (2 回目実行後)
HASH2=$(md5sum "$MEMORY_DIR/reference_$SLUG.md" | awk '{print $1}')
test "$HASH1" = "$HASH2"
```

### V-7: scope のユーザー上書き動作 (R-3.5)

**事前条件:**
- LLM が `scope_hint=cross-issue` と推定する候補を含む transcript fixture
- AskUserQuestion fixture: `verification-fixtures/v7-override-to-issue.json` (`final_scope=issue` に上書き)
- 該当 slug の memory file を事前削除

**実行:**
- Claude が `/claude-issueops:session-closer` を実行

**期待される副作用:**
- Decision コメントは投稿される (capture flow は通常動作)
- memory file は **生成されない** (final_scope=issue が優先される)

**判定基準:**
```bash
test "$(gh issue view "$ISSUE" --json comments | jq '[.comments[] | select(.body | contains("decision:'"$SLUG"'"))] | length')" = "1"
test ! -f "$MEMORY_DIR/reference_$SLUG.md"
```

### V-8: Tier 2 dedup での重複防止 (R-5.2)

**事前条件:**
- 検証用 issue 新規作成、同じ transcript fixture を使用
- AskUserQuestion fixture: `verification-fixtures/v8-approve-all.json`
- 1 回目実行後に `state.captured_slugs` を意図的に空に書き戻す (Tier 1 を回避して Tier 2 を発動させる)

**実行:**
- Claude が `/claude-issueops:session-closer --capture` を 2 回実行 (間に state file 編集)

**期待される副作用:**
- 1 回目: N 件 Decision コメントが投稿される
- 2 回目: Tier 2 dedup が発動し新規 Decision コメントが 0 件 (skill 出力に "skipped K duplicates" を含む)

**判定基準:**
```bash
COUNT_AFTER_FIRST=$(gh issue view "$ISSUE" --json comments | jq '[.comments[] | select(.body | startswith("<!-- claude-issueops:decision:"))] | length')
# 2 回目実行
COUNT_AFTER_SECOND=$(gh issue view "$ISSUE" --json comments | jq '[.comments[] | select(.body | startswith("<!-- claude-issueops:decision:"))] | length')
test "$COUNT_AFTER_FIRST" = "$COUNT_AFTER_SECOND"
grep -qE 'skipped [0-9]+ duplicates' /tmp/v8-skill-second.log
```

### V-9: Issue 解決の AmbiguousResolution (R-6.4)

> 元の「選択 UI が出ること」は Claude セッション内から観測不能のため、bin の stdin JSON 契約 (design.md の Skill ↔ bin Contract に準拠) を直接叩いて ambiguous レスポンスを観測 → fixture 経由で再 invocation する経路で代替検証する。

**事前条件:**
- 2 件の検証用 issue (`$ISSUE_A`, `$ISSUE_B`) を `status:in-progress` ラベル付きで作成
- 現在ブランチ名が両 issue 番号にマッチしない状態 (例: `master`)

**実行 1 (bin 直接呼び出しで ambiguous を観測):**
```bash
echo '{"schema_version":1,"subcommand":"resolve-issue","session_id":"'"$SID"'","project_dir":"'"$PWD"'","branch":"master"}' \
  | uv run python bin/session_closer.py > /tmp/v9-resolve.json
```

**判定基準 1 (ambiguous レスポンス):**
```bash
# design.md の契約: result に ambiguous_candidates フィールド (オプショナル) が含まれる
jq -e '.ok == true' /tmp/v9-resolve.json >/dev/null
jq -e '.result.ambiguous_candidates | length >= 2' /tmp/v9-resolve.json >/dev/null
```

**実行 2 (override 付きで再 invocation):**
- AskUserQuestion fixture: `verification-fixtures/v9-pick-issue.json` (`$ISSUE_A` を選ぶ)
- Claude が `/claude-issueops:session-closer --capture --issue-number-override "$ISSUE_A"` を skill 経由で起動

**判定基準 2 (投稿先が一意化):**
```bash
test "$(gh issue view "$ISSUE_A" --json comments | jq '[.comments[] | select(.body | startswith("<!-- claude-issueops:decision:"))] | length')" -ge "1"
test "$(gh issue view "$ISSUE_B" --json comments | jq '[.comments[] | select(.body | startswith("<!-- claude-issueops:decision:"))] | length')" = "0"
```

### V-10: state file 破損からの quarantine 復旧 + 並行書き込み race (R-7.4, NFR-Reliability)

**事前条件:**
- 任意の `<sid>` を選び、`<sid>.json` に不正 JSON を書き込む: `echo '{"broken":' > .../<sid>.json`
- 並行書き込みテスト用に PreCompact / SessionEnd の bin adapter を直接 stdin JSON で並行起動できるよう準備

**実行 (2 部構成):**

(a) Quarantine: Claude が `/claude-issueops:session-closer --capture` を実行
(b) Race: Claude が PreCompact / session-closer を `&` で並行起動し `wait` で同期

**期待される副作用:**
- (a) `<sid>.json.corrupt-<ISO8601 microsec>` という名のファイルが生成され、新しい `<sid>.json` が skill のフィールドだけで作成
- (b) 並行起動後、`<sid>.json` には PreCompact が書く `pending_restore` と session-closer が書く `skill_ran_at` の **両方** が含まれる (atomic write が衝突せず、互いを破壊していない)

**判定基準:**
```bash
# (a)
ls "$STATE_DIR" | grep -qE '\.json\.corrupt-[0-9T:.]+'
jq -e '.skill_ran_at' "$STATE_FILE" >/dev/null
# (b) 並行実行後
jq -e '.pending_restore' "$STATE_FILE" >/dev/null
jq -e '.skill_ran_at' "$STATE_FILE" >/dev/null
test "$(ls "$STATE_DIR" | grep -cE '\.json\.tmp\.' || true)" = "0"   # 残骸 tmp なし
```

### V-11: memory escalation の実機書き込み (R-8.1)

> V-6 と一緒に発動するが、観点は分離する: V-6 は close mode 全体フロー、V-11 は memory file の中身検証。

**事前条件:**
- V-6 と同じ実行直後の状態を流用 (memory file 生成済)

**実行:**
- Claude が `Bash` で memory file を読み、`memory_escalate.render_reference_memory` の Python 出力と diff

**判定基準:**
```bash
# Python から render し、ファイルと完全一致するか diff
uv run python -c "
from issueops.marker_parser import Decision
from issueops.memory_escalate import render_reference_memory
d = Decision(slug='${SLUG}', what='${WHAT}', why='${WHY}', alternatives='${ALT}', consequences='${CONS}')
print(render_reference_memory(d), end='')
" > /tmp/v11-expected.md
diff -u /tmp/v11-expected.md "$MEMORY_DIR/reference_$SLUG.md"
# diff exit code = 0 で PASS
```

### V-12: gh auth 失敗時の hint 表示 (R-9.2)

**事前条件:**
- 認証を意図的に壊す: `export GITHUB_TOKEN=invalid_token_for_v12` (`gh auth logout` は対話的なため使わない)
- AskUserQuestion fixture: `verification-fixtures/v12-abort-on-failure.json` (失敗時「中断」)

**実行:**
- Claude が `/claude-issueops:session-closer --capture` を skill 経由で起動 (出力を `/tmp/v12-skill.log` にリダイレクト)

**期待される副作用:**
- skill が `gh_failure_kind: auth` と認識し、`gh auth status を実行してください` のヒント文字列を出力
- state file には `skill_ran_at` のみ更新、Decision 投稿は 0 件

**判定基準:**
```bash
grep -q 'gh_failure_kind: auth' /tmp/v12-skill.log
grep -q 'gh auth status' /tmp/v12-skill.log
jq -e '.skill_ran_at' "$STATE_FILE" >/dev/null
```

**事後処理:** `unset GITHUB_TOKEN`

### V-13: gh 失敗時の 3 択分岐 (R-9.3, R-9.4, R-9.5, R-9.6)

> V-2 が「保存」の分岐を担当しているため、V-13 は「破棄」「中断」の 2 分岐を担当して棲み分ける。

**事前条件 (2 ケース):**
- 共通: `export GITHUB_TOKEN=invalid_token_for_v13` で認証を壊す
- (a) 「破棄」 fixture: `verification-fixtures/v13-discard.json` → pending file が生成されない
- (b) 「中断」 fixture: `verification-fixtures/v13-abort.json` → pending file が生成されず、後続候補処理が止まる
- 各ケースで `$SID` を別値にして state file を分離する

**実行:**
- Claude が (a) と (b) でそれぞれ `/claude-issueops:session-closer --capture` を実行

**判定基準:**
```bash
# (a) 破棄
test ! -f "$STATE_DIR/$SID_A.pending-decisions.json"
test "$(jq '.captured_slugs // [] | length' "$STATE_DIR/$SID_A.json")" = "0"
# (b) 中断
test ! -f "$STATE_DIR/$SID_B.pending-decisions.json"
# 中断時 last_processed_offset が更新されていない (null または 0)
jq -e '(.last_processed_offset // 0) == 0' "$STATE_DIR/$SID_B.json" >/dev/null
```

**事後処理:** `unset GITHUB_TOKEN`

### V-14: SKILL.md frontmatter validation (R-10.1)

> 元案の「Claude Code セッション内でトリガー語から skill が suggest されること」は Claude が内部状態として観測できないため、frontmatter の構造検証 (T-91 と重複しない実機検証) で代替する。

**事前条件:**
- `skills/session-closer/SKILL.md` が plugin install 後の場所に配置されている

**実行:**
- Claude が `Bash` で frontmatter を抽出し YAML として parse、必須フィールドの存在を確認

**判定基準:**
```bash
# frontmatter parse + 必須キー検証
uv run python -c "
import yaml, sys
from pathlib import Path
text = Path('skills/session-closer/SKILL.md').read_text()
fm = yaml.safe_load(text.split('---')[1])
assert fm['name'] == 'session-closer'
assert 'description' in fm and 'capture' in fm['description'] and 'close' in fm['description']
assert 'triggers' in fm and len(fm['triggers']) >= 3
print('OK')
"
# stdout に OK が出れば PASS
```

### V-15: モード切替の skill 実機検証 (R-10.2, R-10.3)

**事前条件:**
- 検証用 issue 新規作成、AskUserQuestion fixture: `verification-fixtures/v15-approve-one-cross-issue.json`
- 同じ transcript fixture を 2 回の実行で使用 (1 回目で offset が前進、2 回目は別 transcript fixture に差し替えるか state.last_processed_offset を 0 にリセット)

**実行 (2 ラウンド):**
- (a) Claude が `/claude-issueops:session-closer --capture` を実行 (capture モード単独)
- (b) Claude が `/claude-issueops:session-closer` を実行 (close モード)

**期待される副作用:**
- (a) 終了後: Decision コメント追加あり、summary コメント **なし**、memory file **なし**
- (b) 終了後: 追加の Decision コメントあり、summary コメント追加あり、cross-issue 候補があれば memory file 生成

**判定基準:**
```bash
# (a) capture モード直後
test "$(gh issue view "$ISSUE" --json comments | jq '[.comments[] | select(.body | contains("session-closer:summary:"))] | length')" = "0"
test ! -f "$MEMORY_DIR/reference_$SLUG.md"
# (b) close モード直後
test "$(gh issue view "$ISSUE" --json comments | jq '[.comments[] | select(.body | contains("session-closer:summary:"))] | length')" = "1"
test -f "$MEMORY_DIR/reference_$SLUG.md"
```

---

## 既存テストへの refactor 影響棚卸し (Codex 改善推奨 #3)

state_save / session_end が `state_writer` 経由になることで、既存テストへの影響範囲を明確化する。修正方針は「**API シグネチャは維持**して中身だけ書き換える」のため、既存テストの assert はそのまま green を保つ前提だが、setup 部 (mock の patch 先) に変更が入る可能性がある。

| 既存ファイル | 既存テスト数 | 影響内容 | 必要対応 |
|-------------|:-----------:|----------|----------|
| `tests/test_state_save.py` | 10 | 内部実装が `state_writer.merge_update_state` 経由になる。出力 JSON 構造は不変 | 現状の assert はそのまま green 維持。+T-132 で互換性を明示 |
| `tests/test_session_end.py` | 12 | 同上 (`run_session_end` の state 書き込みが `state_writer` 経由になる) | 現状の assert はそのまま green 維持。+T-133 で互換性を明示 |
| `tests/test_precompact.py` | 11 | PreCompact の `save_pending_restore` 経由経路が変わる | 既存テストの patch 先 (`state_save.save_pending_restore` 等) は API 維持のため変更不要 |
| `tests/test_user_prompt_submit.py` | 18 | UserPromptSubmit の状態読み取りは現状維持 (書き込みは新規パス) | 影響なし |
| `tests/test_branch_resolver.py` | 20 | 影響なし (branch_resolver 自体は不変) | 影響なし |
| `tests/test_marker_parser.py` | 10 | 影響なし | 影響なし |
| `tests/test_memory_escalate.py` | 8 | 影響なし | 影響なし |

**risk register**: refactor で何かを破壊した場合、`uv run pytest` の既存 89 件のうち最も先に落ちるのは `test_state_save.py` または `test_session_end.py`。spec-implementer は state_writer 導入後に必ず `uv run pytest tests/test_state_save.py tests/test_session_end.py` を最初に走らせること。

---

## DI スタブ仕様 (conftest.py 共通 fixture, Codex 改善推奨 #4)

L2 テストで多用される callable スタブの **共通仕様** を `tests/conftest.py` に集約する。各 Test ID で個別実装するとリグレッションが起きやすいため、以下の fixture を必ず使う:

```python
# tests/conftest.py (新規追加分)
@pytest.fixture
def gh_post_fn_factory():
    """戻り値を制御できる gh_post_fn スタブ。
    Usage:
        fn = gh_post_fn_factory(results=[
            PostResult(ok=True, comment_url="https://...#c1", failure=None),
            PostResult(ok=False, comment_url=None, failure=GhFailure(AUTH, "401", 1, "gh auth status を実行してください")),
        ])
        # 呼び出し回数で順に返す
    """

@pytest.fixture
def gh_view_comments_fn_factory():
    """既存コメントを返す gh_view_comments スタブ。Tier 2 dedup テスト用"""

@pytest.fixture
def gh_list_in_progress_fn_factory():
    """resolve-issue Tier 1 用。返す issue 番号リストを制御"""

@pytest.fixture
def freeze_now():
    """skill_ran_at / saved_at を固定するための datetime fixture"""

@pytest.fixture
def project_dir(tmp_path):
    """一時 project_dir。session-state/ サブディレクトリを自動作成"""
```

各 L2 テスト記述において、上記 fixture を使うと一行で書けることを暗黙の前提とする。

---

## Test File Structure

```
tests/
├── test_session_closer.py             # T-21, T-31, T-32, T-101〜T-136 (orchestrator + helper, L1+L2)
├── test_transcript_reader.py          # T-01〜T-03 (L1)
├── test_decision_extractor.py         # T-11〜T-14 (L1)
├── test_dedup_checker.py              # T-41〜T-43 (L1)
├── test_issue_resolver.py             # T-51〜T-55 (L1)
├── test_state_writer.py               # T-61〜T-67 (L1)
├── test_gh_adapters.py                # T-71〜T-72 (L1, classify_gh_failure)
├── test_pending_decisions.py          # T-81〜T-82 (L1) + 補助
├── test_skill_md.py                   # T-91 (L1, frontmatter parse)
├── test_path_utils.py                 # T-92〜T-94 (L1, state_file_path 切り出し)
├── test_verification_fixture.py       # T-95〜T-98 (L1, AskUserQuestion bypass)
├── test_state_save.py *existing*      # T-132 (refactor 互換)
├── test_session_end.py *existing*     # T-133 (refactor 互換)
├── test_precompact.py *existing*      # 既存 11 件、refactor 後も green
├── test_user_prompt_submit.py *existing*
├── test_marker_parser.py *existing*
├── test_branch_resolver.py *existing*
├── test_memory_escalate.py *existing*
└── conftest.py                        # 共通 fixtures (project_dir, session_id, freeze_time)

VERIFICATION.md                        # V-1〜V-15 を Claude が順次実行する Bash レシピ集
```

---

## Coverage Target

| Level | Target | Rationale |
|-------|--------|-----------|
| L1 (Unit) | 100% | pure module の関数を網羅。実装が DI で書かれているため達成可能 |
| L2 (Integration) | 95%+ | run_capture / run_close の主要分岐をすべてカバー。LLM 呼び出しと AskUserQuestion を callable 注入で抽象化しているため、Python レイヤだけで全フローを駆動できる |
| L3 (Verification) | V-1 〜 V-15 すべて Claude が実行し PASS | Python レイヤを越える範囲 (subprocess 経由 bin adapter / 実 gh / 実 memory dir / skill オーケストレーション全体 / 並行実行 race) は Claude が Claude Code セッション内で skill 起動 + Bash で副作用検証する。AskUserQuestion はフィクスチャ注入で再現する |

---

## Key Testing Decisions

> 「なぜこの戦略にしたか」を明示する。レビュアと未来の自分のため。スコープ外の判断も書く。

1. **AskUserQuestion を callable で抽象化、SKILL.md 経由で渡す前提**: SKILL.md (Claude Code セッション) から AskUserQuestion を実行し、その結果を `UserDecision[]` という静的データ構造として bin に渡す。これにより L2 のテストは MCP / Claude Code セッションを起動せずに `UserDecision[]` を直接スタブとして注入できる。L3 でだけ実機 UI を確認する。

2. **bin adapter (`bin/session_closer.py`) は L2 で直接テストしない**: 既存 hook 群と同じく bin は thin な subprocess wrapper layer に留め、ロジックをすべて pure module に押し込む。これにより L2 のテストは src のみを対象に書け、bin の単体テストは Verification (L3) でカバーする。

3. **既存テスト 89 件を破壊しない refactor 制約**: state_save / session_end が `state_writer` 経由に書き換わるが、API シグネチャは維持し、既存 `tests/test_state_save.py` (10 件)、`tests/test_session_end.py` (12 件) はそのまま green であること (`uv run pytest` で全件パス) を成功条件とする。T-132/T-133 で互換性を明示テスト化。

4. **subcommand 分離テスト (T-136) の重要性**: Codex 再レビューで発覚した「post-and-update が ユーザー対話前に state を書く責務矛盾」を、`post-decisions` と `commit-state` を別呼び出しとして分離した設計で解決した。この分離が確実に機能する (post-decisions だけ呼ばれて落ちても state が前回値を保持する) ことを T-136 で明示テスト化する。

5. **L3 を最小限に絞る**: V-1〜V-15 のうち L2 で代替可能なものは L2 に寄せ、L3 は「Python レイヤから到達できない事象」(skill 自動発見、AskUserQuestion 実機経由、subprocess 実起動、複数プロセス race) に限定。Claude が L3 を一括実行できるよう、すべての判定基準を Bash + jq + grep で自動評価可能な形で記述する。

6. **race condition は L3 で Claude が並行起動して観測**: `state_writer` の atomic write は L1 (T-67) で tmp 名の一意性を検証するが、複数プロセスが本当に衝突しないかは実機で並行起動しないと分からない。Claude が `&` で 2 プロセス並行起動 + `wait` で同期し、両方の書き込みが反映されている (互いの更新を破壊していない) ことを jq で確認する手順を V-10 系で記述する。

7. **AskUserQuestion フィクスチャ注入**: skill の対話部分は実機で人間応答を要求するが、L3 の Claude 自動実行のため `verification-fixtures/<v-id>.json` を Phase 4 (Tasks) で実装スコープに含める。skill 起動時 `CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE` 環境変数があれば fixture からユーザー応答を読み、`AskUserQuestion` を bypass する。本番フローと verification フローを 1 つのコードパスで賄う設計。
   - **誤活性化ガード (重要)**: 通常運用で誤って fixture が活性化しないよう、本番ビルドでは以下の二重防御を実装する: (a) 環境変数の値が `verification-fixtures/` ディレクトリ配下のパスでない場合は無視 (パス検証)、(b) `CLAUDE_ISSUEOPS_VERIFICATION_MODE=1` という二つ目の環境変数が同時に設定されているときのみ fixture 読み込みを許可。一方の変数だけでは bypass せず、stderr に明示的な警告を残す。これらの仕様は Phase 4 タスク化する。

8. **scope_hint と final_scope の独立性検証 (T-13, T-116, V-7)**: LLM 推定とユーザー確定の責務分離が要件 R-3.5 の核心。3 段階 (型変換 / 統合 / 実機) で重複検証することで「scope_hint をうっかり拾って escalation 発火する」リグレッションを排除。

9. **Test ID 体系**: L1 は機能領域ごとに 10 番台 (transcript=01-10, extractor=11-19, etc.)、L2 は run_capture/close 周りで 100 番台、L3 は V-1〜V-15。spec-implementer がテストファイルを生成するときに ID で範囲が判別できる。

---

## Success Criteria

実装完了の判定基準:

- [ ] **L1 全テストパス**: 37 件 (新規) — `uv run pytest tests/test_transcript_reader.py tests/test_decision_extractor.py tests/test_dedup_checker.py tests/test_issue_resolver.py tests/test_state_writer.py tests/test_gh_adapters.py tests/test_pending_decisions.py tests/test_skill_md.py tests/test_path_utils.py tests/test_verification_fixture.py --collect-only -q` で 37 件、すべて green
- [ ] **L2 全テストパス**: 36 件 (新規) — `uv run pytest tests/test_session_closer.py --collect-only -q` で 36 件、すべて green
- [ ] **既存 89 件 green**: state_save / session_end の refactor 後も既存テストが破壊されていない (T-132, T-133 が green)
- [ ] **総計 162 件 green**: `uv run pytest --collect-only -q | tail -1` で 162 件、`uv run pytest` 全件パス
- [ ] **L3 全 verification 完了**: V-1 〜 V-15 が判定基準を満たす (Claude が VERIFICATION.md のレシピを順次実行し、各判定基準コマンドが exit 0)
- [ ] **Coverage Target 達成**: L1=100%、L2=95%+、L3=チェックリスト全完了
- [ ] **Requirements トレース完備**: R-1 〜 R-10 の全 43 AC がカバレッジマトリクス上で `-` 単独の行を持たない、または備考で理由が説明されている
- [ ] **subcommand 分離が機能**: T-136 が green (`commit_state_fn` を raise させた状態で state file が前回値を保持)
- [ ] **件数集計の自動検証**: `pytest --collect-only -q` の出力件数と本書の表記件数が一致している (記載ゆれなし、Codex 軽微 #3)
