# Claude Code 作業ガイドライン (claude-issueops)

## 1. プロジェクト概要

claude-issueops は Claude Code セッションで下された **Decision を GitHub Issue コメントに persistent memory として書き戻す** plugin。session-closer skill が中心で、capture/close 2 モードで Decision marker を投稿し、cross-issue scope は project memory に昇格させる。

- 詳細仕様: [docs/PRD.md](./docs/PRD.md)
- 現フェーズ: **v0.1 released** ([release notes](https://github.com/etoyama/claude-issueops/releases/tag/v0.1.0))、v0.2 / v0.3 計画中

## 2. 通底原理: 「ツールを使うツールを、そのツールで作る」

claude-issueops 自体が「Issue を使った decision capture」ツールである以上、**自分の開発プロセスがこのツールに乗らなければ説得力に欠ける**。dogfooding は通底原理として全運用に貫く。

派生原則:

- **Decision を残す**: 主要 Decision は実装中に `session-closer --capture` で投稿する。後追いではなく文脈が新鮮なうちに
- **小さく動かして足す**: spec を先に完全に書き切らない。Walking Skeleton で小さく動かし、Story 完了ごとに Living Design Doc を更新する
- **subcommand contract drift を防ぐ**: SKILL.md / design.md / bin / pure modules の I/O 契約は単一の真実源を持つ ([#56 の教訓](https://github.com/etoyama/claude-issueops/pull/57))
- **YAGNI**: 3 回出てから括る。半完成の抽象は書かない
- **TDD ハードルール**: pure module 層は Red→Green→Refactor を例外なく回す。テスト未実装の green コードはマージしない

## 3. Issue 階層と運用 (claude-issueops の dogfooding)

階層モデル (Task は Issue 化しない):

```
Milestone (phase:vX.Y)
└── [Meta | Epic]  ← Milestone 直下に並列
    └── Story      ← Epic の Sub-issue
```

| 層 | 責務 | type ラベル |
|---|---|---|
| Milestone | フェーズ単位の集約、release 期限管理 | — (`phase:vX.Y` ラベルで識別) |
| Meta | フェーズ全体の Decision 集約、横断論点、運用ルール変更 | `type:meta` |
| Epic | 機能追加 / リリース単位、複数 Story を束ねる | `type:epic` |
| Story | PR 1 本相当、半日〜3 日、AC 5 件以下 | `type:story` |

### Milestone マップ (現状)

| Milestone (phase ラベル) | 期間 | 完了基準 |
|---|---|---|
| `phase:setup` | 2026-04 | plugin scaffold、初期 SKILL.md、bin adapter |
| `phase:v0.1` | 2026-04〜05 | core continuity + capture (released) |
| `phase:v0.2` | 未定 | GitHub Projects v2 opt-in 統合 (Epic #16) |
| `phase:v0.3` | 未定 | Issue rule engine M3' (Epic #17) |

### Story 作成前セルフチェック (全部 Yes でなければ Epic 疑い)

- [ ] AC は 5 件以下か？
- [ ] 完了見込みは 半日〜3 日 か？
- [ ] PR 数は 1 で済むか？
- [ ] 「〜の作業」と一言で説明できるか？
- [ ] 完了時の成果物は 1 つの独立価値か？

### Decision marker protocol (固定、改変禁止)

session-closer が **parse_decisions / filter-dedup / capture** で前提とする marker フォーマット。手動 `gh issue comment` で投稿するときも同じ形式を守ること (Tier 2 dedup が機能する条件)。

```markdown
<!-- claude-issueops:session-closer:decision:<kebab-case-slug> -->
## Decision: <kebab-case-slug>

**What:** <一文>
**Why:** <理由・制約・動機>
**Alternatives considered:**
- <選択肢> -> <却下理由>
**Consequences:** <得るもの、失うもの、将来の影響>
```

### claude-issueops 自身を使った運用 (dogfooding)

- ブランチ命名: `(feat|fix|chore|refactor|docs)/<issue番号>-<slug>`
- `status:in-progress` は同時に 1 Issue のみ (session-closer Tier 1 fallback が機能する条件)
- 1 issue = 1 commit、 master への merge は no-ff (CONTRIBUTING.md 参照)
- セッション中の主要 Decision: `/claude-issueops:session-closer --capture`
- セッション終了: `/claude-issueops:session-closer` (引数なし、close モード)

## 4. ラベル体系

| カテゴリ | ラベル | 用途 |
|---|---|---|
| type (階層) | `type:meta`, `type:epic`, `type:story` | §3 の階層モデルに対応する Issue 種別 |
| type (補助) | `type:feature`, `type:bug`, `type:chore`, `type:docs` | Story / 単発 Issue の性質を補足 (階層 type と併用可、例: `type:story` + `type:bug`) |
| phase | `phase:setup`, `phase:v0.1`, `phase:v0.2`, `phase:v0.3` | リリースフェーズ識別 |
| area | `area:hooks`, `area:protocol`, `area:skill`, `area:plugin-meta`, `area:settings`, `area:process` | 領域識別 |
| status | `status:in-progress` | 状態 (Tier 1 fallback で必須、同時 1 件のみ) |

新フェーズ / 新領域追加時は本セクションに追記する。

## 5. コーディング原則

- **Python 3.11+** (PEP 604 union、modern type hints)
- **uv** で依存管理 (`uv sync`, `uv run pytest`)
- **pytest** が唯一のテストランナー (pyproject.toml `[tool.pytest.ini_options]` 参照、`testpaths=["tests"]`, `pythonpath=["src"]`)
- **YAGNI**: 3 回出てから括る、半完成の抽象禁止
- **TDD (ハードルール、pure module 層)**: Red→Green→Refactor。テストを実行して Red 確認、Green 確認、Refactor 維持確認を必ず通す
- **subprocess 集約**: shell-out はすべて `src/issueops/gh_adapters.py` 経由、`shell=True` 禁止
- **型 hint は public API に必須**、内部は任意

### 3 層構造 (subcommand contract drift 防止)

claude-issueops は副作用境界を 3 層に分ける。境界を越える変更時は **3 層全部を同期する**:

| 層 | 場所 | 責務 |
|---|---|---|
| **Skill 層** | `skills/<skill>/SKILL.md` | LLM 推論、AskUserQuestion での対話、subcommand のオーケストレーション |
| **bin adapter 層** | `bin/*.py` | stdin/stdout JSON でやりとりする唯一の Python entrypoint、ロジックを持たず dispatch のみ |
| **pure module 層** | `src/issueops/*.py` | 副作用を持たない関数群、依存注入 (DI) でテスト可能 |

**重要**: `AskUserQuestion` は **必ず SKILL.md (Claude Code セッション内) から呼ぶ**。Python 側からは呼ばない。bin adapter は subprocess 起動も持たない (gh_adapters 経由のみ)。

subcommand 契約 (input/output JSON schema) は `.spec-workflow/specs/<skill>/design.md` の "Skill ↔ bin Contract" 表が真実源。SKILL.md / design.md / bin / tests のいずれかを変える時は **全部同期** すること ([#56 教訓](https://github.com/etoyama/claude-issueops/pull/57)、[#64 教訓](https://github.com/etoyama/claude-issueops/pull/68))。

### ディレクトリ構成 (大枠)

```
skills/<skill>/SKILL.md         # Skill 層
bin/<skill>.py                  # bin adapter 層 (subcommand dispatch)
bin/<event>_hook.py             # Claude Code hook entrypoint (precompact / sessionend / userpromptsubmit)
src/issueops/*.py               # pure module 層
tests/*.py                      # pytest (L1 helpers + L2 e2e、conftest.py で DI factory)
hooks/hooks.json                # plugin hook 登録
.spec-workflow/specs/<skill>/   # legacy: phase:v0.1 までの spec doc 群 (archive 扱い)
.claude-plugin/                 # plugin marketplace meta

# 未作成 (Meta #69 のサブタスクで追加予定):
docs/PRD.md                     # プロダクト中央仕様
docs/design/epic-NN-*.md        # Epic Living Design Doc
.github/PULL_REQUEST_TEMPLATE.md # PR template (Self review セクション)
```

## 6. Design Doc 運用 (Epic Living Design Doc)

> **運用開始予定**: `docs/design/` ディレクトリは未作成。`phase:v0.2` の着手時 (target-flag Epic が初運用予定、[Meta #69](https://github.com/etoyama/claude-issueops/issues/69)) に新設し、雛形を確立する。

各 Epic に 1 ファイル: `docs/design/epic-NN-<topic>.md`

更新タイミング:

- **主要 Decision 確定時** (実装着手前): Living セクションを draft 更新、user / Codex review
- **Story 完了時**: draft → finalize、Story timeline に節を append

セクション:

1. Status (Living) — `in-progress` / `done`
2. Architecture overview (Living) — Mermaid `graph TD`
3. Module structure (Living) — パッケージ・公開 API
4. Subcommand contract (Living) — Skill ↔ bin の I/O 契約 (claude-issueops 固有、3 層整合の要)
5. Data flow (Living) — Mermaid `flowchart LR`
6. Story timeline (Append-only) — Story 完了ごとに節追加

簡潔さの原則:

- 各セクション 1 ページ以内
- 図は原則 Mermaid (GitHub レンダリング + PR diff で差分が見える)
- 「実装と乖離した冗長な説明」を書かない、コードを正とする

### `.spec-workflow/specs/` (legacy) の扱い

`phase:v0.1` までは `.spec-workflow/specs/session-closer/` の 4 ファイル (requirements / design / tasks / test-design) で開発した。`phase:v0.2` 以降は docs/design/epic-NN-*.md に移行する。**legacy spec は archive 扱いで凍結**、変更が必要な場合は対応する Epic Design Doc を新規作成して上書きする。

## 7. claude-issueops 自身の使い方 (dogfooding)

開発中の Claude Code セッションで本 plugin を使う:

| タイミング | コマンド | 効果 |
|---|---|---|
| 主要 Decision を決めた瞬間 | `/claude-issueops:session-closer --capture` | Decision marker を target Issue (Tier 1/2 で確定) に投稿、state file に slug 記録 |
| セッション終了時 | `/claude-issueops:session-closer` | capture フロー + summary 投稿 + cross-issue scope の memory 昇格 |
| (将来) Meta Issue に投稿 | `/claude-issueops:session-closer --target meta` | Phase 全体の cross-cutting Decision を Meta Issue に投稿 (#65 + #66 で導入予定) |

### Decision の粒度 (#67 の運用ガイダンス案)

| パターン | タイミング | 認知コスト |
|---|---|---|
| **都度 capture** (推奨) | Story 完了時に 1-3 Decision を `--capture` | 中 (transcript が新鮮、抽出精度高) |
| **バッチ capture** | セッション末に `--capture` 1 回 + close 1 回 | 低 (対話回数最小) |
| 手動 `gh issue comment` | 上記が重い場合の最終手段 | marker さえ守れば Tier 2 dedup は機能、ただし Tier 1 state file が漏れる |

## 8. 言語ルール

- 応答は **日本語**
- code、関数名、変数名、ラベル名、subcommand 名は **英語**
- docs/ は **日本語** (code 断片と確立技術用語のみ英語)
- Issue 本文・PR 説明は日英混在可
- コメントは「WHY が非自明な時のみ」(WHAT は名前で示す)

## 9. コードレビュー (軽量)

### タイミングと観点

| タイミング | 範囲 | 観点 | 上限 |
|---|---|---|---|
| **Story PR 提出時** | `git diff master..<branch> -- 'src/**' 'bin/**' 'tests/**' 'skills/**'` | 5 観点 | 12 items |
| **Epic 完了時** | Epic 全体の master 差分 | 5 観点 + doc/README 同期 | 15 items |

**5 観点** (priority 順):

1. ロジック誤り / off-by-one bugs
2. edge case がテスト未網羅 (partial failure、idempotency、空入力、特殊文字)
3. **subcommand contract drift** (SKILL.md / design.md / bin / tests の I/O 契約が全部同期しているか — #56 / #64 の教訓)
4. **3 層境界違反** (AskUserQuestion を Python から呼んでいないか、bin にロジックが漏れていないか、`shell=True` が使われていないか)
5. YAGNI 違反 / 過剰設計 (現フェーズ範囲を超えた抽象、未使用の DI スロット等)

**Epic 完了時の追加観点**:

6. doc / README 同期 (Epic Design Doc、CLAUDE.md、PRD、SKILL.md が実装と整合しているか)

### 実行手段

優先順位: subagent (即時、軽量) → codex CLI 直叩き → team-review (重め、必要時のみ)。

```bash
git diff master..<branch> -- 'src/**' 'bin/**' 'tests/**' 'skills/**' > /tmp/pr-<num>.diff
```

`orchestra:general-purpose` subagent への prompt (固定フォーマット):

```
Review the Python/Markdown diff at /tmp/pr-<num>.diff for a claude-issueops Story.
Output ONLY actionable bullet points. No praise. No restating. Max ~12 items.

Focus areas (priority order):
1. Logic errors or off-by-one bugs
2. Edge cases NOT covered by tests (partial failure, idempotency, empty input, special chars)
3. Subcommand contract drift: SKILL.md / design.md / bin / tests の I/O 契約が同期しているか
4. 3-layer boundary violation: AskUserQuestion を Python から呼んでいないか、bin にロジックが漏れていないか
5. YAGNI: over-engineering for the current phase

For each item: file:line + one-sentence fix suggestion.
```

Epic 完了時は 6 観点目 (doc/README 同期) を追加し `Max ~15 items` に上げる。

### 結果の取り扱い

指摘の優先度を **high / medium / low** で評価し、**high + medium** だけを当該 PR 内で fix。**low は defer** して次 Story / Epic で再評価。結果は **PR description の Self review セクション** に貼る (PR template は [Meta #69](https://github.com/etoyama/claude-issueops/issues/69) のサブタスクで `.github/PULL_REQUEST_TEMPLATE.md` を新設予定。それまでは PR description に直接記載)。

「全件 fix しないと merge できない」運用にしない (軽量レビューの精神に反する)。
