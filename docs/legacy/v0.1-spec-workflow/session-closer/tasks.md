# Tasks Document — session-closer skill

> 本タスクリストは Phase 1 (Requirements) / Phase 2 (Design) / Phase 3 (Test Design) すべての承認済 spec から逆算して、TDD 原則 (テスト先行) で並べたもの。タスク順序は依存関係に従い、上から順に実装する。各タスク完了時に `uv run pytest` で当該タスク範囲のテストが green になっていることを確認する。

## 実装順序の方針

1. **共通 fixture を先に整備** (タスク 1) — 後続全テストで使用
2. **新規 pure module は test-first** で 1 モジュールずつ Red→Green→Refactor (タスク 2〜8)
3. **既存 state_save / session_end の refactor** (タスク 9〜10) — 既存 89 件 green を維持
4. **orchestrator 実装** (タスク 11〜12) — pure module を組み合わせる
5. **bin adapter + SKILL.md + fixture 注入機構** (タスク 13〜16)
6. **Verification fixtures + VERIFICATION.md 作成** (タスク 17〜18)
7. **動作検証 + 結合確認** (タスク 19〜20)

## Tasks

- [x] 1. 共通 test fixture を `tests/conftest.py` に追加
  - File: tests/conftest.py (新規 or 既存に追記)
  - Test Design「DI スタブ仕様」セクションに記載した共通 fixture を実装: `project_dir(tmp_path)`、`freeze_now`、`gh_post_fn_factory`、`gh_view_comments_fn_factory`、`gh_list_in_progress_fn_factory`
  - Purpose: 後続の L1 / L2 テストで使う共通スタブを 1 箇所に集約し、テスト間で乖離を防ぐ
  - _Leverage: 既存 tests/test_*.py の pytest 慣習_
  - _Requirements: NFR-Code Architecture (DI), Test Design 全体_
  - _Prompt: Role: pytest fixture 設計に詳しい Python QA エンジニア | Task: Test Design 文書の「DI スタブ仕様」セクションに従って tests/conftest.py に共通 fixture を追加する。各 fixture は呼び出し側で戻り値や呼び出し履歴を制御できる factory 関数として実装する | Restrictions: 既存 tests/conftest.py がある場合は破壊しない。fixture の docstring に「どの Test ID で使うか」を明記する。実装は I/O を持たず callable 注入のみ | Success: 5 つの fixture (project_dir / freeze_now / gh_post_fn_factory / gh_view_comments_fn_factory / gh_list_in_progress_fn_factory) が定義され、`uv run pytest --collect-only` で既存テストを破壊せず収集できる_

- [x] 2.1 `path_utils` モジュールを切り出して循環依存を解消
  - File: src/issueops/path_utils.py (新規)、tests/test_path_utils.py (新規)、src/issueops/state_save.py (修正、最小)
  - 既存 `state_save.state_file_path` と `_validate_session_id` を新規 `path_utils.py` に移植 (state_save 側は path_utils から re-export して API を維持)。tests/test_path_utils.py に新規テスト 3 件 (T-92, T-93, T-94: 正常パス / unsafe session_id / 空 session_id)
  - Purpose: state_writer が state_save を import し、state_save が state_writer を import する循環依存を未然に防ぐ。両者が共通 path_utils に依存する形に正規化
  - _Leverage: src/issueops/state_save.py 既存実装_
  - _Requirements: R-7.2 (パス検証), Design「Code Reuse Analysis」_
  - _Prompt: Role: Python パッケージ設計と循環依存解消に詳しい開発者 | Task: state_file_path / _validate_session_id を path_utils.py に移植する。state_save.py からは `from issueops.path_utils import state_file_path` で re-export して既存テスト 10 件の API を破壊しない。tests/test_path_utils.py に T-92/T-93/T-94 (正常 / unsafe / empty) を追加 | Restrictions: state_save.py の public API シグネチャは変更しない。既存 tests/test_state_save.py が修正なしで green を保つ | Success: tests/test_path_utils.py の 3 件 (T-92〜T-94) green、tests/test_state_save.py の既存 10 件も green を維持_

- [x] 2.2 `state_writer` の Red→Green (T-61〜T-67)
  - File: tests/test_state_writer.py (新規)、src/issueops/state_writer.py (新規)
  - 先に tests/test_state_writer.py で T-61〜T-67 (7 件) の test_* 関数を Red 状態で書く。次に src/issueops/state_writer.py を実装して Green にする
  - 実装内容: `merge_update_state(*, project_dir, session_id, patch, now=None) -> Path` (atomic write with `os.replace` + `pid+monotonic_ns+uuid4` tmp 名) と `quarantine_corrupt(target, *, now=None) -> Path` (マイクロ秒 ISO8601 suffix)
  - Purpose: 全 state file 書き込みの単一窓口を提供。NFR Reliability の atomic 規約を満たす
  - _Leverage: src/issueops/path_utils.py (state_file_path), Design「State File Atomic Write Pattern」_
  - _Requirements: R-7.1〜R-7.4, R-1.4, R-1.5, NFR-Reliability_
  - _Prompt: Role: Python ファイル I/O と原子性に詳しいバックエンド開発者 | Task: tests/test_state_writer.py で T-61〜T-67 を Red で書き、src/issueops/state_writer.py を実装する。merge_update_state は (1) 既存ファイル読み込み (不正 JSON は quarantine_corrupt で退避)、(2) patch を merge、(3) 同一 dir に `<file>.tmp.<pid>.<monotonic_ns>.<uuid8>` で書き込み、(4) os.replace で原子的に target 化、の流れ | Restrictions: os.rename ではなく os.replace を使う (Windows/POSIX 両対応)。`state_save` を直接 import せず必ず `path_utils.state_file_path` を経由する (循環依存防止)。list 値は merge せず置換 | Success: tests/test_state_writer.py の 7 件すべて green、`uv run pytest tests/test_state_writer.py -v` で 7 件 PASS、既存 89 件にも影響なし_

- [x] 3. `transcript_reader` の Red→Green (T-01〜T-03)
  - File: tests/test_transcript_reader.py (新規)、src/issueops/transcript_reader.py (新規)
  - tests で T-01 / T-02 / T-03 を Red、実装で Green
  - 実装: `read_transcript_since(transcript_path, *, offset=0) -> TranscriptSlice`、`@dataclass(frozen=True) TranscriptSlice(content, end_offset)`
  - Purpose: transcript の差分読み取りを提供。FileNotFoundError は上位伝播
  - _Leverage: 標準ライブラリのみ_
  - _Requirements: R-3.1, R-3.4_
  - _Prompt: Role: Python I/O と JSONL 処理に詳しい開発者 | Task: tests/test_transcript_reader.py で T-01〜T-03 を Red で書き、src/issueops/transcript_reader.py を実装する。バイトオフセット指定で transcript の続きを読み、読み終えた位置を end_offset として返す。FileNotFoundError は catch せず上位に伝播 | Restrictions: ファイル全体を一度にメモリへ読み込まない (大規模 transcript 対応のためストリーム可能な形)。end_offset の計算誤差を生まないよう byte-level で扱う | Success: 3 件 green、`uv run pytest tests/test_transcript_reader.py -v` で 3 件 PASS_

- [x] 4. `decision_extractor` の Red→Green (T-11〜T-14)
  - File: tests/test_decision_extractor.py (新規)、src/issueops/decision_extractor.py (新規)
  - tests で T-11〜T-14 (4 件) を Red、実装で Green
  - 実装: `Candidate / UserDecision / PostedDecision` の frozen dataclass、`parse_candidates_json(text) -> list[Candidate]`、`candidate_to_decision(candidate) -> Decision` (既存 marker_parser.Decision に変換)
  - Purpose: LLM 出力の parse + 検証 + Candidate→Decision 変換
  - _Leverage: src/issueops/marker_parser.py (Decision)_
  - _Requirements: R-3.2, R-3.3, R-3.5_
  - _Prompt: Role: Python dataclass と JSON 検証に詳しい開発者 | Task: tests/test_decision_extractor.py で T-11〜T-14 を Red で書き、src/issueops/decision_extractor.py に Candidate / UserDecision / PostedDecision dataclass と parse_candidates_json / candidate_to_decision を実装する。slug は kebab-case (`^[a-z0-9-]+$`) で検証、必須フィールドが空な候補は破棄 | Restrictions: marker_parser.Decision を直接 import して再利用、自前で Decision 型を作らない。Literal["issue", "cross-issue"] を厳密に守り任意の値を許容しない | Success: 4 件 green、`uv run pytest tests/test_decision_extractor.py -v` で 4 件 PASS_

- [x] 5. `dedup_checker` の Red→Green (T-41〜T-43)
  - File: tests/test_dedup_checker.py (新規)、src/issueops/dedup_checker.py (新規)
  - tests で T-41 / T-42 / T-43 を Red、実装で Green
  - 実装: `filter_local(candidates, *, captured_slugs)` と `filter_remote(candidates, *, existing_decisions)`
  - Purpose: Tier 1 + Tier 2 dedup の純粋判定ロジック (gh 取得自体は orchestrator 側)
  - _Leverage: marker_parser.Decision (型として)_
  - _Requirements: R-5.1, R-5.2, R-5.3 のうち純粋判定部分_
  - _Prompt: Role: 関数型志向の Python 開発者 | Task: tests/test_dedup_checker.py で T-41〜T-43 を Red で書き、filter_local と filter_remote を実装する。両関数は副作用を持たず candidates を slug ベースで除外して返すだけ | Restrictions: gh 呼び出しはこのモジュールに含めない。empty captured_slugs / existing_decisions のときは入力をそのまま返す境界ケースを必ずテストする | Success: 3 件 green_

- [x] 6. `issue_resolver` の Red→Green (T-51〜T-55)
  - File: tests/test_issue_resolver.py (新規)、src/issueops/issue_resolver.py (新規)
  - tests で T-51〜T-55 (5 件) を Red、実装で Green
  - 実装: `resolve_target_issue(*, branch, list_in_progress_fn, branch_pattern=DEFAULT_BRANCH_PATTERN) -> int | AmbiguousResolution`、`AmbiguousResolution(candidates: list[int])` dataclass、`IssueResolutionError`
  - Design「Issue resolution 状態遷移表」7 ケースを完全に網羅するテストを書く
  - Purpose: Issue 解決の Tier 1 / Tier 2 / 交差確定 / ambiguous 判定
  - _Leverage: src/issueops/branch_resolver.py (extract_issue_number, resolve_current_issue, DEFAULT_BRANCH_PATTERN)_
  - _Requirements: R-6.1〜R-6.5_
  - _Prompt: Role: 状態遷移と分岐ロジックに強い Python 開発者 | Task: tests/test_issue_resolver.py で T-51〜T-55 を Red で書き、resolve_target_issue を Design 文書の状態遷移表 7 ケース通りに実装する。AmbiguousResolution は SKILL.md がユーザー選択フローに渡す signal 用 frozen dataclass | Restrictions: branch_resolver.resolve_current_issue を必ず再利用、自前でブランチ正規表現を書き直さない。例外は IssueResolutionError のみ raise、None は返さない (SKILL.md 側で扱いやすくするため) | Success: 5 件 green、状態遷移表 7 ケースの分岐がすべてテストでカバーされる_

- [x] 7. `gh_adapters` の Red→Green (T-71〜T-72)
  - File: tests/test_gh_adapters.py (新規)、src/issueops/gh_adapters.py (新規)
  - tests で T-71 / T-72 を Red、実装で Green
  - 実装: `GhFailureKind` (StrEnum)、`GhFailure(Exception)` dataclass、`PostResult` dataclass、`classify_gh_failure(stderr, exit_code) -> GhFailure`、subprocess wrapper (`gh_view_comments`、`gh_post_comment`、`gh_list_in_progress`、`git_branch`)
  - Purpose: subprocess + 失敗分類の単一所有者
  - _Leverage: bin/precompact_hook.py のサブプロセス wrapper パターン_
  - _Requirements: R-9.1, R-9.2_
  - _Prompt: Role: subprocess とエラー分類に詳しい Python 開発者 | Task: tests/test_gh_adapters.py で T-71/T-72 (classify_gh_failure 4 種分岐 + auth hint) を Red で書き、gh_adapters.py を実装する。subprocess wrapper 4 個も併せて実装するが、これらは pure ではないので unit test では mock 化のみ (純粋テストは classify_gh_failure に集中) | Restrictions: subprocess.run の引数は必ず argv 配列、shell=True 禁止 (NFR Security)。stderr の文字列マッチングは大文字小文字非依存にする (例: `re.IGNORECASE` で `Authentication failed` も拾う) | Success: 2 件 green。subprocess wrapper はインターフェース面のみ確認_

- [x] 8. `pending_decisions` の Red→Green (T-81〜T-82)
  - File: tests/test_pending_decisions.py (新規)、src/issueops/pending_decisions.py (新規)
  - tests で T-81 / T-82 を Red、実装で Green
  - 実装: `pending_path(project_dir, session_id) -> Path`、`append_pending_decisions(*, project_dir, session_id, issue_number, decisions, now=None) -> Path` (state_writer の atomic パターンを再利用)
  - Purpose: gh 失敗時の「保存」分岐の永続化
  - _Leverage: state_writer (atomic write 規約参照)、path_utils.state_file_path / _validate_session_id (path 検証)_
  - _Requirements: R-9.4_
  - _Prompt: Role: Python ファイル I/O と JSON schema 設計の開発者 | Task: tests/test_pending_decisions.py で T-81/T-82 を Red で書き、pending_decisions.py を実装する。append_pending_decisions は既存ファイルを読み entries 配列に追記する形 (置換ではない)。schema_version=1 を必須フィールドに含め、不一致は ValueError | Restrictions: state_writer.merge_update_state を直接呼ぶのではなく、同じ atomic write パターン (tmp 命名規則 `<file>.tmp.<pid>.<monotonic_ns>.<uuid8>` + os.replace) を本モジュール内で **state_writer と完全に同一の手順で** 実装する (pending file は schema が異なるので merge ロジックは借りないが、atomic write の挙動は揃える)。pending_path は path_utils.\_validate_session_id を再利用 | Success: 2 件 green_

- [x] 9. `state_save` を `state_writer` 経由に refactor
  - File: src/issueops/state_save.py (修正)
  - `save_pending_restore` 内部の JSON 書き込みを `state_writer.merge_update_state(patch={"pending_restore": ...})` に置き換える
  - Purpose: state file 書き込みパスの統一 (NFR Reliability の atomic 規約を全 hook に適用)
  - _Leverage: src/issueops/state_writer.py (タスク 2 で新規作成)_
  - _Requirements: NFR-Reliability, Design「Code Reuse Analysis → state_save の atomic write 化」_
  - _Prompt: Role: Python リファクタリングに詳しい開発者 | Task: src/issueops/state_save.py の save_pending_restore を state_writer.merge_update_state 経由に書き換える。public API シグネチャは絶対に維持する (既存 tests/test_state_save.py 10 件が修正なしで green を保つこと) | Restrictions: API シグネチャを変更しない、戻り値の型・None ケースの扱いも変更しない。state_save.state_file_path の path 検証ロジックは残す | Success: `uv run pytest tests/test_state_save.py -v` で既存 10 件すべて green を維持_

- [x] 10. `session_end` を `state_writer` 経由に refactor
  - File: src/issueops/session_end.py (修正)
  - `run_session_end` 内部の state file 書き込み (例: `last_summary_at` の更新) を `state_writer.merge_update_state` に置き換える
  - Purpose: 同上
  - _Leverage: src/issueops/state_writer.py_
  - _Requirements: NFR-Reliability, Test Design「既存テストへの refactor 影響棚卸し」_
  - _Prompt: Role: Python リファクタリングに詳しい開発者 | Task: src/issueops/session_end.py の run_session_end を state_writer 経由に書き換える。public API は維持。tests/test_session_end.py 12 件が修正なしで green を保つこと | Restrictions: API シグネチャ・戻り値・引数を変更しない | Success: `uv run pytest tests/test_session_end.py -v` で既存 12 件すべて green、`uv run pytest tests/test_state_save.py tests/test_session_end.py tests/test_precompact.py` で 33 件すべて green_

- [x] 11. refactor 互換テスト T-132 / T-133 を追加
  - File: tests/test_state_save.py (追記) または新規 tests/test_state_save_compat.py、tests/test_session_end.py (追記)
  - T-132: `state_save.save_pending_restore` の書き込み JSON 構造が refactor 前と同等であることを snapshot で検証
  - T-133: `session_end.run_session_end` の書き込みフィールドが refactor 前と同等
  - Purpose: refactor 後も I/O 同等性を明示テスト化
  - _Leverage: state_writer (atomic write の確認)_
  - _Requirements: NFR-Reliability, Test Design T-132/T-133_
  - _Prompt: Role: 互換性テストに強い Python QA | Task: T-132/T-133 を実装し、書き込まれる JSON 構造が refactor 前と同じであることを assert する。snapshot 比較は jq などを呼ばず、Python 内で json.loads + dict 比較で行う。atomic write の証拠として `<file>.tmp.*` 残骸が無いことも確認 | Restrictions: 既存テスト 22 件と矛盾しない assertion を書く。snapshot 値はテスト内に埋め込み、外部 fixture ファイルにしない | Success: T-132/T-133 が green、`uv run pytest tests/test_state_save.py tests/test_session_end.py -v` で既存 + 新規がすべて PASS_

- [x] 12. `session_closer` orchestrator + 残 L1 (T-21, T-31, T-32) の Red→Green (T-101〜T-136 + T-21, T-31, T-32)
  - File: tests/test_session_closer.py (新規)、src/issueops/session_closer.py (新規)
  - tests で T-101〜T-136 (L2: 36 件) と、orchestrator 内 helper の単体テストとして T-21 (`test_run_capture_partial_success_state` 単体ロジック)、T-31 (`test_summary_marker_idempotent` helper)、T-32 (`test_summary_marker_format` helper) を Red、実装で Green
  - 実装: `CaptureRequest / CloseRequest` dataclass、`run_capture(req) -> CaptureResult`、`run_close(req) -> CloseResult`、内部 helper として `build_summary_marker(session_id) -> str` (T-32) と `is_summary_already_posted(comments, session_id) -> bool` (T-31)
  - State Writes Table の 10 シナリオを完全に再現する分岐、subcommand 分離 (post-decisions / commit-state) 設計に整合
  - **T-136 の特記事項**: `commit_state_fn` (state_writer.merge_update_state を wrap した callable) を意図的に `RuntimeError` で raise させる setup を組み、(1) `gh_post_comment` が呼ばれている (post-decisions が走った)、(2) state file が事前 snapshot と完全一致 (前回値を保持)、(3) `<sid>.json.tmp.*` 残骸ファイルが残っていない、の 3 点を assert する。subcommand 分離が機能していることを担保する核心テスト
  - Purpose: 全フローを callable 注入で end-to-end テスト可能にする
  - _Leverage: タスク 2.1/2.2〜8 の全 pure module、memory_escalate (write_memory_file/update_memory_index/render_reference_memory)_
  - _Requirements: R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9, NFR_
  - _Prompt: Role: orchestration と DI に強い Python アーキテクト | Task: tests/test_session_closer.py で T-21, T-31, T-32, T-101〜T-136 (合計 39 件) を Red で書き、src/issueops/session_closer.py に run_capture / run_close + summary helper を実装する。テストは callable 注入ですべて駆動、subprocess を使わない。State Writes Table の 10 シナリオを各テストでカバー。T-136 は上記特記事項通り | Restrictions: AskUserQuestion 関連の callable は注入しない (orchestrator は UserDecision[] のみを扱う、対話は責務外)。memory escalation は final_scope=cross-issue のみ発動、issue scope は memory dir に絶対に触らない | Success: 39 件すべて green、`uv run pytest tests/test_session_closer.py -v` で 39 件 PASS、State Writes Table の 10 シナリオが各テストで明示的に検証されている_

- [x] 13. `bin/session_closer.py` adapter を実装
  - File: bin/session_closer.py (新規、実行可能 +x)
  - Skill ↔ bin Contract (stdin/stdout JSON, schema_version: 1) に従って 8 種の subcommand を dispatch
  - Purpose: SKILL.md が呼ぶ唯一の Python entrypoint
  - _Leverage: bin/precompact_hook.py のパターン、gh_adapters のサブプロセス wrapper、session_closer の run_capture/run_close_
  - _Requirements: Design「Skill ↔ bin Contract」_
  - _Prompt: Role: CLI/IPC 設計に強い Python 開発者 | Task: bin/session_closer.py を実装する。stdin で JSON を読み、subcommand に応じて適切な pure function (read-transcript / resolve-issue / filter-dedup / post-decisions / commit-state / summary / escalate / save-pending) を呼び、結果を stdout JSON で返す。schema_version 不一致は `{ok: false, error: {kind: "internal"}}` で即返却 | Restrictions: ロジックを bin に書かない (試験対象が増える)、すべて pure module 経由。例外をキャッチして必ず `{ok: false, error: {...}}` JSON を出して exit 0 (skill が JSON を parse する前提) | Success: stdin に各 subcommand の JSON を流すと正しい stdout JSON を返す。手動で `echo '{...}' | uv run python bin/session_closer.py` で動作確認_

- [x] 14. fixture 注入機構 (`AskUserQuestion` bypass) の Red→Green (T-95〜T-98)
  - File: src/issueops/verification_fixture.py (新規)、tests/test_verification_fixture.py (新規)、bin/session_closer.py 内で利用
  - tests で T-95 (両方揃う = bypass 発動)、T-96 (path 不正 = 無視 + stderr 警告)、T-97 (MODE 不正 = 無視 + stderr 警告)、T-98 (両方なし = 無視、警告なし) を Red、実装で Green
  - 環境変数 `CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE` (fixture path) と `CLAUDE_ISSUEOPS_VERIFICATION_MODE=1` (二重ガード) が **両方** 揃ったときのみ fixture 読み込みを許可
  - Purpose: L3 verification を Claude が一気通貫で実行可能にする
  - _Leverage: なし (新規)_
  - _Requirements: Test Design「Key Testing Decisions #7」_
  - _Prompt: Role: テストハーネス設計に強い Python 開発者 | Task: verification_fixture.py に `load_fixture_or_none() -> dict | None` を実装する。`CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE` の値が `verification-fixtures/` 配下の path でなければ無視、かつ `CLAUDE_ISSUEOPS_VERIFICATION_MODE` が `1` でなければ無視。両方揃った場合のみ JSON を読んで返す。tests/test_verification_fixture.py で T-95〜T-98 を実装 | Restrictions: 片方だけ設定された場合は stderr に「fixture mode 不完全」の警告を出す。fixture JSON parse 失敗は ValueError 上位伝播 | Success: tests/test_verification_fixture.py の 4 件 (T-95〜T-98) すべて green_

- [x] 15. `skills/session-closer/SKILL.md` を作成
  - File: skills/session-closer/SKILL.md (新規)、tests/test_skill_md.py (新規) — T-91
  - frontmatter (name, description, triggers) と orchestration 本文 (LLM 抽出 prompt + bin subcommand 呼び出し手順) を書く
  - Purpose: skill としての登録と発見性 (R-10)
  - _Leverage: claude code skill 仕様 (既存 spec-writer / spec-reviewer 等の SKILL.md がリファレンス)_
  - _Requirements: R-10.1, R-10.2, R-10.3_
  - _Prompt: Role: Claude Code skill 設計に詳しいプロンプトエンジニア | Task: skills/session-closer/SKILL.md を作成する。frontmatter には name=session-closer, description (capture/close 両モードを 2 行で), triggers (5 個以上) を含める。本文には Design 文書の「SKILL.md オーケストレーションのステップ」10 ステップを LLM プロンプトとして展開し、bin subcommand への JSON 構造例を併記する。tests/test_skill_md.py で T-91 (frontmatter parse + 必須キー検証) を実装 | Restrictions: bin に渡す JSON は必ず schema_version: 1 を含める。AskUserQuestion を skill 内で実行する箇所を明示する (Python は AskUserQuestion を呼ばない) | Success: tests/test_skill_md.py の 1 件 (T-91) が green。assert は (a) frontmatter が yaml として parse 可能、(b) `name == "session-closer"`、(c) `description` に "capture" と "close" が含まれる、(d) `triggers` が 5 個以上、(e) 本文に「`schema_version`: 1」と「`AskUserQuestion`」の文字列が出現、の 5 点。実機 trigger 動作は L3 V-14 でカバーするため本タスクの Success には含めない_

- [x] 16.1 `verification-fixtures/` v1〜v5 を作成
  - File: verification-fixtures/v1-approve-all.json 〜 v5-reject-all.json (5 個)
  - V-1 (capture happy path)、V-2 (gh 失敗 → 保存)、V-3 (SessionEnd skip)、V-4 (close mode)、V-5 (全却下) 用の AskUserQuestion 応答 fixture
  - Purpose: V-1〜V-5 を Claude が自動実行可能にする
  - _Leverage: Test Design V-1〜V-5 の事前条件記述、verification_fixture.py のスキーマ_
  - _Requirements: V-1〜V-5_
  - _Prompt: Role: テストフィクスチャ設計に詳しい QA エンジニア | Task: 5 個の JSON fixture を作成。verification_fixture.py が読み取る schema (例: `{"schema_version":1, "responses":[{"question_id":"...","selections":[...]}]}`) に従う | Restrictions: 各 fixture の内容と V-X 判定基準が乖離していないこと | Success: 5 個が valid JSON_

- [x] 16.2 `verification-fixtures/` v6〜v10 を作成
  - File: verification-fixtures/v6-cross-issue.json 〜 v10-state-corrupt.json (5 個)
  - V-6 (cross-issue 昇格)、V-7 (scope 上書き)、V-8 (Tier 2 dedup)、V-9 (AmbiguousResolution)、V-10 (state 破損 + race) 用 fixture
  - Purpose: V-6〜V-10 用 fixture
  - _Leverage: 16a と同じ_
  - _Requirements: V-6〜V-10_
  - _Prompt: Role: 同上 | Task: 5 個の JSON fixture を作成 | Restrictions: 同上 | Success: 5 個が valid JSON_

- [x] 16.3 `verification-fixtures/` v11〜v15 を作成
  - File: verification-fixtures/v11-memory-content.json 〜 v15-mode-switch.json (5 個)
  - V-11 (memory 内容)、V-12 (gh auth hint)、V-13 (3 択 discard/abort)、V-14 (skill frontmatter 実機)、V-15 (モード切替) 用 fixture
  - Purpose: V-11〜V-15 用 fixture
  - _Leverage: 16a と同じ_
  - _Requirements: V-11〜V-15_
  - _Prompt: Role: 同上 | Task: 5 個の JSON fixture を作成 | Restrictions: 同上 | Success: 5 個が valid JSON_

- [x] 17. `VERIFICATION.md` を作成 (V-1〜V-15 を Claude が順次実行する Bash レシピ)
  - File: VERIFICATION.md (新規、リポジトリルート)
  - 各 V-X の (1) 事前条件 (2) 実行 (3) 判定基準コマンド を Claude がそのまま `Bash` ツールで実行できる単一スクリプト集として書き出す
  - Purpose: L3 verification の実行手順を一箇所に集約
  - _Leverage: Test Design V-1〜V-15_
  - _Requirements: Test Design L3 全体_
  - _Prompt: Role: 自動化スクリプトに強い DevOps エンジニア | Task: VERIFICATION.md にマークダウンとして V-1〜V-15 を順番に書く。各 V-X は (a) `### V-N: 名前` 見出し、(b) `## Setup` の bash code block、(c) `## Run` の bash code block (skill 起動 or bin 直接呼び出し)、(d) `## Assert` の bash code block (test/jq/grep/diff で機械評価)、(e) `## Cleanup` (環境変数 unset 等) の構成 | Restrictions: 各 code block は単独実行で完結する形にする (前後の bash 変数は環境変数として export しておく前提を明記)。判定基準は exit 0 で PASS、非 0 で FAIL を一貫させる | Success: Claude が VERIFICATION.md の各セクションを Bash ツールでコピペ実行できる、全 15 件の Assert が exit 0 を返せば overall PASS_

- [x] 18. requirements.md / design.md / test-design.md の最終整合確認 (機械チェックリスト)
  - File: .spec-workflow/specs/session-closer/*.md (確認のみ、必要なら最小修正)
  - Phase 4 実装中に判明した仕様 mismatch があれば spec 側を修正する
  - Purpose: spec と実装の double source of truth を防ぐ
  - _Leverage: 実装ファイル群と spec の cross-check_
  - _Requirements: spec-writer 最終チェック_
  - _Prompt: Role: 仕様書品質保証担当 | Task: 以下のチェックリストを Bash + grep で機械的に走らせ、すべて exit 0 を確認する: (a) `grep -c 'post-and-update' .spec-workflow/specs/session-closer/*.md` が 0、(b) design.md の subcommand 一覧 (`post-decisions`, `commit-state`, `read-transcript` 等) が bin/session_closer.py に実装されたサブコマンドと一致 (`grep -E "subcommand.*(post-decisions|commit-state|...)" bin/session_closer.py`)、(c) Requirements R-1〜R-10 の AC 番号 (`R-X.Y`) を test-design のカバレッジマトリクスに対して 1 件ずつ存在確認、(d) state file schema (skill_ran_at / last_processed_offset / captured_slugs) が design.md と src/issueops/session_closer.py の両方に登場、(e) `grep -c 'TODO\|FIXME\|XXX' src/issueops/*.py` が 0。すべての assertion が PASS なら整合 OK。修正が必要なら spec 側を最小限修正 | Restrictions: Phase 1〜3 の承認を覆す機能変更は禁止。修正は用語追加・誤字訂正レベルに留める | Success: 上記 (a)〜(e) のチェックがすべて exit 0、spec と実装の AC 対応関係が一致_

- [x] 19. 全 162 件 green を確認 (回帰検証)
  - File: 全 tests/test_*.py (実行確認のみ)
  - `uv run pytest --collect-only -q | tail -1` で 162 件、`uv run pytest -q` で全件 PASS
  - 内訳:
    - 既存 89 件 (test_branch_resolver 20 + test_marker_parser 10 + test_memory_escalate 8 + test_precompact 11 + test_session_end 12 + test_state_save 10 + test_user_prompt_submit 18)
    - 新規 L1 37 件 (T-01〜T-03 + T-11〜T-14 + T-21 + T-31〜T-32 + T-41〜T-43 + T-51〜T-55 + T-61〜T-67 + T-71〜T-72 + T-81〜T-82 + T-91 + T-92〜T-94 (path_utils) + T-95〜T-98 (verification_fixture) = 30 + 3 + 4 = 37)
    - 新規 L2 36 件 (T-101〜T-136 連番、欠番なし、T-132/T-133 互換テスト含む)
    - 合計: 89 + 37 + 36 = **162 件**
  - Purpose: refactor 後も既存 89 件 + 新規分すべて green を保つ
  - _Leverage: 全 test ファイル_
  - _Requirements: Test Design Success Criteria_
  - _Prompt: Role: テスト実行と CI 品質ゲートに詳しい QA | Task: (1) `uv run pytest --collect-only -q | tail -1` で件数を取得 (期待値 163、ただし実装途中で件数微調整があった場合は test-design.md の Success Criteria を最終真実とする)、(2) `uv run pytest -q` で全件 PASS を確認、(3) 失敗があれば該当タスクに戻る、(4) 件数が期待値と乖離したら test-design.md 側の数値を訂正する PR をタスク 18 経由で出す | Restrictions: テストを skip / xfail でごまかさない。落ちたテストは必ず根本原因を直す。pytest dependency に freezegun は追加しない (時刻固定は `freeze_now` fixture でモック注入する設計、pyproject.toml は変更不要) | Success: `uv run pytest -q` の終了行が `<N> passed` で、N が test-design.md Success Criteria の値と一致_

- [ ] 20. L3 verification を Claude が一気通貫実行 (V-1〜V-15)
  - File: VERIFICATION.md の各 V-X を Claude が `Bash` で実行
  - Claude が `CLAUDE_ISSUEOPS_VERIFICATION_MODE=1` を設定し、各 V-X の Setup → Run → Assert → Cleanup を順次実行する
  - Purpose: 全機能要件 R-1〜R-10 の Bash 機械評価可能な検証
  - _Leverage: VERIFICATION.md (タスク 17)、verification-fixtures/ (タスク 16)_
  - _Requirements: R-1〜R-10 の L3 カバー範囲, Test Design Success Criteria_
  - _Prompt: Role: Claude Code セッション内で skill と bin と gh を運用する自動化エンジニア | Task: VERIFICATION.md の V-1 から V-15 までを Claude が `Bash` ツールで順次実行する。各 V-X の Assert ブロックの全コマンドが exit 0 を返せば PASS。途中で失敗したら該当タスクに戻る | Restrictions: 本番 issue を汚さないよう、検証用 issue は事前に作成 (タイトルに [V-N] prefix)、検証後にクローズ。`CLAUDE_ISSUEOPS_VERIFICATION_MODE=1` を必ず付ける、本番運用と完全分離する | Success: 15 件すべて Assert が exit 0、最後に該当 issue を全部 close、verification-fixtures/ の使用ログを stdout に出力_
