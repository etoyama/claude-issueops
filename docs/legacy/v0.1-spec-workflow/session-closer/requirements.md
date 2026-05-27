# Requirements Document — session-closer skill

## Introduction

`/claude-issueops:session-closer` は claude-issueops プラグインのユーザー対話 skill であり、Claude Code の会話セッション中に下された決定 (Decision) を、GitHub Issue のコメント欄に persistent memory として書き戻す責務を担う。本 skill は **capture / close** の 2 モードで動作し、Decision marker protocol (`<!-- claude-issueops:decision:<slug> -->` と What / Why / Alternatives considered / Consequences の 4 フィールド) に従って投稿する。さらに `close` モードでは、セッション要約コメントの追記と、cross-issue scope の Decision を Claude のグローバル memory ディレクトリへ昇格 (escalate) する処理を含む。

本 skill は v0.1 リリースで Epic #7 を auto-close する最後の機能要素であり、PreCompact / UserPromptSubmit / SessionEnd の 3 hook と協調して「セッションをまたいだ context 喪失」と「決定の記録漏れ」を一貫して防ぐ。

## Alignment with Product Vision

claude-issueops の中核命題は「GitHub Issue のコメント欄を LLM の persistent memory layer として使う」こと。session-closer はその **書き戻し側のメインエントリ** として、抽出と確認を skill UI (`AskUserQuestion` による対話 + skill 内 prompt による抽出) に乗せる。Issue #8、Epic #7、insight-blueprint #132 の design decision `h4-skill-with-fallback-and-modes` のスコープに従う。

## Issue #8 AC ↔ Requirement トレーサビリティ表

| Issue #8 AC | 対応 Requirement | 主な AC# | 検証観点 |
|-------------|------------------|----------|----------|
| AC3: `--capture` で decision 抽出・確認・投稿 | R-1 (capture mode) | R-1.1, R-1.3, R-1.4 | trigger・抽出・確認・投稿・部分成功時の state |
| AC4: close mode で decision + summary + memory escalate | R-2 (close mode) + R-8 (memory) | R-2.1〜R-2.5, R-8.1〜R-8.4 | フロー全体・summary idempotency・cross-issue 昇格 |
| AC6: 二段階 dedup で二重投稿なし | R-5 (duplicate prevention) | R-5.1, R-5.2, R-5.3 | Tier 1 + Tier 2 + degradation |

> v0.1 では Issue #8 で番号付けされていない要件 (extraction / confirmation / issue resolve / state file / graceful / registration) も R-3, R-4, R-6, R-7, R-9, R-10 として補完してある。Test Design phase ではこの表を起点に、すべての AC が test level に割り当てられているかを検証する。

## Requirements

### Requirement 1: capture モードによる Decision 投稿

**User Story:** アクティブなセッション中の開発者として、`--capture` をセッションを終了せずに呼び出すことで、ここまでに下した決定を marker protocol 準拠の Issue コメントとして記録したい。

#### Acceptance Criteria

1. WHEN ユーザーが `/claude-issueops:session-closer --capture` を呼び出した THEN skill SHALL `state.last_processed_offset` 以降の transcript 領域から Decision 候補を抽出し、`AskUserQuestion (multiSelect)` でユーザーに提示し、承認された候補を Decision marker protocol で Issue コメント投稿し、同じセッションに制御を戻す。
2. IF 未処理領域に新しい候補が 1 件もない THEN skill SHALL 「新しい決定は検出されませんでした」と明示して終了し、何も投稿しない。
3. WHEN capture モードでコメント投稿が走った THEN skill SHALL **個別の候補単位で** 成功・失敗を判定し、投稿成功した slug のみを `state.captured_slugs` に追記する。部分失敗 (一部成功・一部失敗) を許容し、失敗 slug は次回 invocation の Tier 2 dedup で再検出されるため二重投稿は発生しない。
4. WHEN 全候補の処理 (投稿 / ユーザー却下 / `gh` 失敗時の選択) が完了した THEN skill SHALL `state.last_processed_offset` を当該 invocation で読み込んだ transcript 位置に更新する。途中中断時は更新しない (Reliability NFR の atomic write 規約に従う)。
5. WHEN capture モードが終了した (投稿件数に関係なく) THEN skill SHALL `state.skill_ran_at` を ISO-8601 UTC のタイムスタンプとして書き込み、SessionEnd hook が当該セッションの fallback 投稿を skip できるようにする。

> Issue #8 AC3 に対応。

### Requirement 2: close モードによる Decision + Summary + Memory escalation

**User Story:** セッションを終わらせる開発者として、capture と要約投稿と memory 昇格を 1 コマンドで完了させ、セッションをまたいでも重要な情報が失われないようにしたい。

#### Acceptance Criteria

1. WHEN ユーザーが `/claude-issueops:session-closer` をモード指定なし (= 既定 `close` モード) で呼び出した THEN skill SHALL Requirement 1 で定義した capture フロー全体を実行する。
2. WHEN close モード内で capture フローが完了した AND 当該セッションで 1 件以上の Decision が投稿されている (capture / close 両方の累計) THEN skill SHALL マーカー `<!-- claude-issueops:session-closer:summary:<session_id> -->` を持ち、投稿された slug 一覧を含むセッション要約コメントを Issue に投稿する。session_id を marker に埋め込むことで、複数回の close 呼び出しでも要約多重投稿を防ぐ。
3. WHEN 要約投稿前に Issue 既存コメントを scan した結果 `<!-- claude-issueops:session-closer:summary:<session_id> -->` (同一 session_id) が既に存在する THEN skill SHALL 要約再投稿をスキップする (idempotency)。
4. WHEN 投稿された Decision のうち `scope == "cross-issue"` のものがある THEN skill SHALL Requirement 8 の手順に従って memory 昇格を実行する。本 Requirement では「close モードでは memory 昇格フェーズを起動する」ことのみを規定し、書き込み手順の詳細は R-8 が所有する。
5. IF 当該セッションで Decision が 0 件 AND `cross-issue` 候補も 0 件 THEN skill SHALL 要約コメント投稿と memory 昇格をスキップする (`state.skill_ran_at` の更新は行う)。

> Issue #8 AC4 に対応。

### Requirement 3: transcript からの Decision 候補抽出

**User Story:** skill 自身として、セッションの transcript を読み、構造化された Decision 候補を生成することで、ユーザーには曖昧な要約ではなく具体的かつ整形済みの提案を提示したい。

#### Acceptance Criteria

1. WHEN skill が処理を開始した THEN skill SHALL `${CLAUDE_PROJECT_DIR}/.claude/projects/<sid>/transcript.jsonl` を読み込み、`state.last_processed_offset` (既定 0) からのバイト範囲のみを抽出対象とする。
2. WHEN 抽出を実行した THEN skill SHALL 各候補を `{slug: kebab-case, what, why, alternatives, consequences, scope: "issue" | "cross-issue"}` の構造で生成する。`scope` は LLM による初期推定であり、最終決定はユーザーに委ねる (AC5 を参照)。
3. WHEN 候補の slug が kebab-case でない OR `what / why / alternatives / consequences` のいずれかが空である THEN skill SHALL その候補をユーザー提示前に破棄する。
4. IF transcript ファイルが存在しない OR 読み取り不能 THEN skill SHALL モードに応じた復旧示唆を含む明確なエラーで終了し、state ファイルを変更しない。
5. WHEN 候補をユーザー提示する THEN skill SHALL Requirement 4 の `AskUserQuestion` の中で `scope` を `[issue, cross-issue]` の二択としてユーザーに選択させ、LLM 推定を初期値として表示する。最終的な scope はユーザー選択が決定権を持つ。

### Requirement 4: ユーザー確認ゲート

**User Story:** Decision 記録を求められた開発者として、不正確・重複した内容が Issue に書き込まれないよう、投稿前に候補を multi-select で確認したい。

#### Acceptance Criteria

1. WHEN 抽出と dedup の後に候補が 1 件以上残っている THEN skill SHALL `AskUserQuestion` を `multiSelect: true` で提示し、ユーザーが各候補を独立に承認 / 却下できるようにする。
2. WHEN ユーザーが候補を却下した (選択を外した) THEN skill SHALL その候補を投稿せず、`state.captured_slugs` にも追加しない。
3. WHEN ユーザーがすべての候補を却下した (1 件も選択しなかった) THEN skill SHALL 「ユーザーが全候補を却下しました」と通知して終了する。コメント投稿なし、`skill_ran_at` 以外の state 変更なし。

### Requirement 5: 二段階 Duplicate prevention

**User Story:** 同一セッション内で `--capture` を複数回叩く可能性のある開発者として、同じ Decision が二重投稿されないよう保証してほしい。Issue コメント欄をクリーンに保ちたい。

#### Acceptance Criteria

1. WHEN 抽出が走った THEN skill SHALL `state.captured_slugs` に既出の slug を持つ候補を除外する (Tier 1: ローカル state)。
2. WHEN Tier 1 通過後の候補が残っている THEN skill SHALL 対象 Issue の既存コメントを `gh issue view --json comments` で取得し、`marker_parser.parse_decisions` でパースし、Issue 上に既出の slug を持つ候補を除外する (Tier 2: サーバー側の真実)。
3. IF Tier 2 の `gh` 呼び出しが失敗した THEN skill SHALL Tier 1 のみで継続し (警告通知付き)、abort しない。これは Requirement 9 の graceful degradation の一部。

> Issue #8 AC6 に対応。

### Requirement 6: ターゲット Issue の解決

**User Story:** skill として、決定を投稿する対象 Issue を確実に決定し、誤った Issue や保護ブランチ (master / main 等) 直下のコミットに投稿しないようにしたい。

#### Acceptance Criteria

1. WHEN 解決処理が始まった THEN skill SHALL `gh issue list --label status:in-progress --state open` で `status:in-progress` ラベル付きの open Issue を探し、ちょうど 1 件見つかった場合はそれを優先する (Tier 1)。
2. WHEN Tier 1 が 2 件以上を返した THEN skill SHALL Tier 2 にフォールバックする前に `branch_resolver.extract_issue_number` で現在ブランチから Issue 番号を抽出し、Tier 1 結果と一致する 1 件があればそれを採用する (Tier 1 + branch hint による交差確定)。
3. WHEN Tier 1 が 0 件 OR Tier 1 + branch hint で一意確定できない THEN skill SHALL `branch_resolver.resolve_current_issue` を現在の git ブランチ名に対して実行し、ブランチ名パターンで解決する (Tier 2)。
4. IF 両 tier 経由でも 2 件以上の候補が残る (極めて稀) THEN skill SHALL `AskUserQuestion (single select)` でユーザーに対象 Issue を選ばせる。ユーザーが選ばずキャンセルした場合は abort。
5. IF Tier 1 / Tier 2 / ユーザー選択のどれでも Issue 番号を確定できない THEN skill SHALL 明確なエラーメッセージで終了し、何も投稿しない。state ファイルは `skill_ran_at` のみ更新する。

### Requirement 7: state ファイルとの統合 (frozen schema)

**User Story:** hook + skill スタックの保守者として、本 skill が PreCompact / UserPromptSubmit / SessionEnd hook と共有する session-state ファイルで、互いのフィールドを破壊しないようにしてほしい。

#### Acceptance Criteria

1. WHEN skill が `${CLAUDE_PROJECT_DIR}/session-state/<session_id>.json` に書き込む THEN skill SHALL 既存 JSON をロードし、自分のフィールド (`skill_ran_at`、`last_processed_offset`、`captured_slugs`) のみを merge し、他 hook が所有するフィールド (`briefing_done`、`pending_restore`、`last_summary_at` 等) を削除・変更しないこと。
2. WHEN state ファイルパスを構築する THEN skill SHALL `state_save.state_file_path` を再利用し、path traversal 検証 (`/`、`\`、`..` を拒否) を既存 hook と同じ実装で行うこと。
3. IF skill 実行時点で state ファイルが存在しない THEN skill SHALL 自分のフィールドのみで新規作成する (他 hook 用フィールドのデフォルト値は埋めない)。
4. IF state ファイルが不正な JSON である THEN skill SHALL 当該ファイルを `<session_id>.json.corrupt-<ISO8601>` にリネームして退避し、警告通知をユーザーに表示し、新しい state ファイルを自分のフィールドのみで作成する。他 hook が書いたかもしれない情報を上書きで失わないため。

### Requirement 8: Memory escalation (cross-issue scope のみ)

**User Story:** 複数 Issue にまたがるアーキテクチャ決定を記録した開発者として、その Decision を Claude のグローバル memory に昇格させ、元の Issue がクローズされても残るようにしたい。

#### Acceptance Criteria

1. WHEN 投稿された Decision が `scope == "cross-issue"` を持つ THEN skill SHALL `memory_escalate.write_memory_file` で reference 型 memory ファイルを Claude の標準 memory ディレクトリ (`~/.claude/projects/<encoded-project>/memory/`) に書き込む。
2. WHEN 昇格が memory ファイルを書き込んだ THEN skill SHALL 同じ Decision に対して `memory_escalate.update_memory_index` を一度だけ呼び、`MEMORY.md` の index 行を slug 単位で idempotent に追記する。
3. WHEN 投稿された Decision が `scope == "issue"` を持つ THEN skill SHALL memory ディレクトリには触らない (Issue コメントが唯一の sink)。
4. IF memory ファイル書き込みが失敗した (ディスクエラー、権限) THEN skill SHALL 警告を出すが、すでに投稿済みの Issue コメントはロールバックせず継続する。

### Requirement 9: `gh` 失敗時の Graceful degradation

**User Story:** ネットワーク不安定や `gh` 認証エラーの状況にいる開発者として、Decision を失わずに後でリトライできるよう、ローカル退避の選択肢を提示してほしい。

#### Acceptance Criteria

1. WHEN `gh` コマンドが失敗した (exit code != 0) THEN skill SHALL stderr / exit code を判定し、失敗理由を `network` / `auth` / `rate-limit` / `unknown` の 4 種に分類すること。判定ルールは: `auth` (stderr に `authentication`, `auth status`, `401`)、`rate-limit` (`rate limit`, `429`)、`network` (`Could not resolve host`, `connection refused`, `timeout`)、それ以外は `unknown`。
2. WHEN 分類結果が `auth` THEN skill SHALL ユーザーに `gh auth status を実行してください` というヒントを表示する。
3. IF 投稿ステップ (`gh issue comment`) が失敗した THEN skill SHALL `AskUserQuestion` で `[ローカルに保存して後で再投稿, 破棄, 中断]` の選択肢を提示する。
4. WHEN ユーザーが「ローカルに保存」を選んだ THEN skill SHALL 未投稿の決定 payload を `${CLAUDE_PROJECT_DIR}/session-state/<session_id>.pending-decisions.json` (state 本体とは別ファイル) に追記し (既存ファイルを尊重して append/merge)、当該 slug を `captured_slugs` に追加しない。
5. WHEN ユーザーが「破棄」を選んだ THEN skill SHALL 当該未投稿候補を記録せずに drop する。
6. WHEN ユーザーが「中断」を選んだ THEN skill SHALL 即座に終了し、それ以降の候補処理はしない (ただし `skill_ran_at` は書き込む)。

> v0.1 では 3 択構成を採用する。理由: 開発の主用途はネットワーク不調・auth 切れによる一時失敗であり、「保存→次回再投稿」が決定の喪失を防ぐ実用的価値を持つ。`pending-decisions.json` の自動再投稿フローは v0.2 へ延期 (Scope 参照)。

### Requirement 10: Skill 登録と発見性

**User Story:** claude-issueops プラグインを install するユーザーとして、`/claude-issueops:session-closer` が適切な description とトリガーキーワードで自動発見され、追加設定なしで呼び出せるようにしたい。

#### Acceptance Criteria

1. WHEN プラグインが install された THEN skill SHALL `skills/session-closer/SKILL.md` 配下に登録され、frontmatter で両モードの説明とトリガーキーワード (`session closer`、`capture decisions`、`close session`、`session 終了`、`decisions まとめて` 等) を列挙すること。
2. WHEN `--capture` なしで呼び出された THEN skill SHALL 既定の `close` モードで動作する。
3. WHEN `--capture` 付きで呼び出された THEN skill SHALL capture モードで動作し、要約投稿と memory 昇格フェーズをスキップする。

## Non-Functional Requirements

### Code Architecture and Modularity
- **Single Responsibility Principle**: skill ランタイムの関心事 (transcript reader, candidate extractor, dedup checker, state writer, memory escalator, gh adapter) は、それぞれ `src/issueops/` 配下の独立した純粋モジュールとして実装する。SKILL.md はオーケストレーションのみで、ビジネスロジックを埋め込まない。
- **重複より再利用**: skill は `marker_parser.parse_decisions`、`branch_resolver.resolve_current_issue`、`memory_escalate.{write_memory_file, update_memory_index, render_reference_memory}`、`state_save.state_file_path` を必ず再利用する。コピー実装の禁止。
- **bin adapter pattern**: subprocess ラッパー (`gh issue view`、`gh issue comment`、`gh issue list`、`git branch --show-current`) は薄い adapter モジュール (`src/issueops/gh_adapters.py`) に隔離し、オーケストレーション関数に注入する。これによりピュアモジュールはテスト可能に保つ。
- **明確なインターフェース**: オーケストレーションのエントリポイントは、transcript reader、gh I/O、ユーザープロンプト (`AskUserQuestion`) を依存性注入で受け取る。これにより全フローを subprocess や MCP 依存なしでユニットテスト可能にする。

### Performance
- transcript scan は `last_processed_offset` 以降をストリーム読みする。invocation のたびに先頭から読み直さない。
- 典型的なセッション (transcript ≤ 5 MB、候補 ≤ 20 件) は **transcript 読み込み開始から `AskUserQuestion` 提示直前まで** を、SSD ローカル I/O 想定で 5 秒以内に完了すること。`gh` ネットワーク往復および LLM 抽出推論時間は本指標から除外する (それぞれ Reliability / 抽出 prompt 設計が責務を持つ)。
- `gh` 読み取りは最大 2 回 (in-progress 一覧取得、対象 Issue の既存コメント取得)、書き込みは最大 N + 1 回 (N コメント + 要約 1 件) に抑える。

### Security
- ランタイムコンテキストから渡される `session_id` は必ず `state_save.state_file_path` の検証を通す。文字列連結で state ファイルパスを組み立てない。
- ログ方針: INFO レベルでは slug と件数のみ出力し、Decision 本文は出さない (機密性のあるプロダクト判断を含む可能性があるため)。DEBUG ログでも本文は **先頭 80 文字に切り詰める**。本文の完全出力は `CLAUDE_ISSUEOPS_DEBUG_FULL_BODY=1` 環境変数を明示設定した場合に限る (CI ・共有開発環境での過剰露出を防ぐため)。
- transcript や候補内容に由来する文字列をシェル経由で実行しない (slug や Decision 本文の `gh` 引数への shell interpolation 禁止、必ず argv 配列で渡す)。

### Reliability
- skill は連続呼び出しに対して安全であること。2 回目の呼び出しは Tier 1 + Tier 2 dedup で「すべて投稿済み」と判定し clean に終了すること。
- skill は SIGINT による中断に対して安全であること。state ファイル書き込みは明確なコミットポイント (capture フロー終端、要約投稿後) でのみ実行する。フロー途中の中断は state を変更せずに残す。
- **state ファイルの atomic write**: 書き込みは `<file>.tmp` への完全書き込み → `os.rename(<file>.tmp, <file>)` の二段階で行う。これにより SIGINT・電源断・並行実行 (PreCompact 等の hook と同タイミング) で state ファイルが破損または半端な状態になることを防ぐ。これは PreCompact / SessionEnd hook の既存実装にも揃える前提。
- `gh` 失敗時の分類と通知ルールは Requirement 9 が SHALL 形式で所有する。本 NFR では「分類が必須である」点のみ規定し、検証は R-9 で行う。

### Usability
- `AskUserQuestion` の選択肢は、各候補について slug + `what` の 1 行サマリ (≤ 80 文字) を併記する。ユーザーが本文展開せずに即決できるようにする。
- エラーメッセージは 1 画面で収まる長さにする。長い診断情報は `${CLAUDE_PROJECT_DIR}/session-state/<session_id>.skill.log` に書き出す。
- skill 終了時に標準出力へ 1 行サマリを出す: `Posted N decisions, escalated M to memory, skipped K duplicates.`

## Scope

### In scope (v0.1)
- capture モード (`--capture` フラグ)。
- close モード (既定)。
- 二段階 duplicate prevention (state offset + Issue marker scan)。
- `cross-issue` scope の Decision に対する memory escalation。
- close モードでのセッション要約コメント (session_id 込みの marker による idempotency 含む)。
- state ファイル統合 (merge update、atomic write、他 hook フィールドを破壊しない)。
- `gh` 失敗時の graceful degradation (3 択 + 失敗種別 4 分類)。
- `SKILL.md` による skill 登録。
- **`scope` 判定方式**: LLM が候補抽出時に初期推定し、`AskUserQuestion` でユーザーが最終決定する。閾値による自動判定や AI 単独判断は v0.1 では採用しない。

### Out of scope (deferred)
- GitHub Projects v2 のフィールド更新 (v0.2 へ延期)。
- `pending-decisions.json` の自動再投稿フロー (v0.2 へ延期、v0.1 では「保存」のみ提供しユーザーが手動で再 invoke する)。
- Issue ルールエンジン / capture 時の自動ラベル付け (v0.3 へ延期)。
- Decision からの自動 Issue 作成 (計画なし)。
- 1 回の invocation で複数 Issue にまたがる capture (skill は常に 1 invocation = 1 Issue を対象とする)。
- LLM のみによる `scope` 自動判定 (将来的に検討余地あり、v0.1 ではユーザー確認必須)。

## Glossary

| 用語 | 定義 |
|------|------|
| 候補 (Candidate) | 抽出器が transcript から生成した、ユーザー承認前の Decision 案。`AskUserQuestion` で承認されたものだけが「Decision」として投稿される。本文書では承認前を「候補」、承認後を「Decision」と呼び分ける。 |
| Decision | ユーザー承認後の、4 フィールド (What / Why / Alternatives considered / Consequences) と kebab-case slug を持つ構造化レコード。Issue コメントとして投稿される。 |
| Decision marker | Issue コメント内で Decision 本文を anchor する HTML コメント `<!-- claude-issueops:decision:<slug> -->`。 |
| capture モード | `--capture` 付きの呼び出し。新しい Decision を抽出して投稿し、セッションへ制御を戻す。 |
| close モード | 既定の呼び出し。capture フロー → 要約投稿 → cross-issue 昇格までを順に行う。 |
| state ファイル | `${CLAUDE_PROJECT_DIR}/session-state/<session_id>.json`。PreCompact / UserPromptSubmit / SessionEnd / session-closer で共有。 |
| `skill_ran_at` | 本 skill が書き込む ISO-8601 UTC タイムスタンプ。SessionEnd hook が fallback 投稿を skip するか判定する際に読む。 |
| `last_processed_offset` | transcript ファイルの中で、すでに Decision 抽出対象として処理済みのバイト位置。 |
| `captured_slugs` | 当該セッションで投稿済みの slug 一覧 (Tier 1 dedup の根拠)。 |
| cross-issue scope | 影響が複数 Issue に跨る Decision。close モードで Claude memory へ昇格される。 |
| issue scope | 単一 Issue 内に閉じる Decision。Issue コメントのみが sink。 |
| Tier 1 / Tier 2 (dedup) | それぞれ「ローカル state offset」「サーバー側 Issue 上の marker scan」。 |
| Tier 1 / Tier 2 (issue resolve) | それぞれ「`status:in-progress` ラベル」「ブランチ名パターン」。 |
| `scope_hint` (Design 用語) | LLM が候補抽出時に推定した scope。最終確定前の値。Design phase で導入された。 |
| `final_scope` (Design 用語) | ユーザーが `AskUserQuestion` で確定した scope。memory escalation の判定はこの値で行う。Design phase で導入された。 |
| `scope` (本 Requirements の用語) | 上記 `scope_hint` と `final_scope` の総称。Requirements 内で `scope` と書かれている箇所は、文脈上「最終確定された scope」(= `final_scope`) を指す。 |
