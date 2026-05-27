# claude-issueops PRD

## 1. Why

Claude Code セッションは 5 つの境界で context を失う:

1. セッション開始 (前セッションの結論を覚えていない)
2. セッション中盤のドリフト (序盤の合意を忘れる)
3. 自動 compaction (要約に意図と理由が落ちる)
4. セッション終了 (decisions が transcript と共に消える)
5. 次セッション開始 (1 に戻る)

既存の memory 機構 (Claude memory) は個人の preferences は扱うが、「この issue で何をやっていて、なぜ X ではなく Y を選んだか」のような **意思決定ログ** には向かない。`claude-issueops` は **GitHub Issue 自体を persistent memory 層** として使うことでこの問題を解く。hooks が session 開始 / compaction 後に最新コメントを読み、skill が session 終了時に decisions を書き戻す。cross-issue な学びは Claude memory に `reference` として昇格させる。

## 2. Target

GitHub を中心に開発する Claude Code 利用者に対し、**Issue を persistent memory として** 使う仕組みを Plugin として提供する。具体的には:

- **Decision capture**: session 中の主要 Decision を構造化 marker 付きで Issue コメントに書き戻し
- **Session continuity**: hooks による session 開始 briefing + post-compact restore
- **Cross-issue escalation**: 横断的な学びを Claude memory に reference として昇格
- **Dogfooding**: claude-issueops 自身の開発も同じ仕組みで回す ([CLAUDE.md](../CLAUDE.md) §2)

## 3. Success Metrics

主指標と補助指標を分けて追跡する。boatrace-insight プロジェクトを **第一 dogfood ユーザー** として、利用パターンから定量化する。

| 種別 | 指標 | 計測方法 | 目標値 (暫定) |
|---|---|---|---|
| 主 | Decision capture 自動化率 | `session-closer` (capture / close) が投稿した Decision marker 数 ÷ 該当 Issue に存在する Decision marker 総数 (手動 `gh issue comment` 投稿分を含む全 marker のうち skill 経由の割合) | ≥ 70% (残りは手動 `gh issue comment` または忘却分) |
| 主 | session-closer fallback 起動率 | SessionEnd hook の fallback summary 投稿数 ÷ 全 session 終了数 | ≤ 20% (利用者が能動的に `/claude-issueops:session-closer` を呼ぶ比率を上げる) |
| 補助 | re-install 頻度 | manifest version bump 間の plugin reinstall 不要日数 | 1 release あたり 30 日以上の連続利用 |
| 補助 | subcommand contract drift 検出件数 | SKILL.md / design.md / bin / tests の不整合 issue 件数 | 0 件/release (#56 / #64 の再発防止) |

ベースライン値は v0.2 着手前に boatrace-insight の利用ログから確定する。

## 4. Out of Scope (v0.x)

v1.0 までは以下を **やらない**:

1. GitHub 以外のプラットフォーム (GitLab Issues / Linear / Jira)
2. WebUI ダッシュボード (Issue / Decision の可視化)
3. リアルタイム集計 (Decision を Slack / メールに即時通知)
4. AI による Decision 価値判定 (重要 Decision の自動抽出を除く、ranking はやらない)
5. Multi-repo Decision 横断 (1 plugin 1 repo)
6. Decision marker の自動 enrichment (related code lines / commits の自動追加)
7. session-closer の自動定期実行 (スケジューラ / CI / cron / Claude Code 内 `/loop` 等での自動トリガー)
8. team モード (個人利用前提、共有 memory は別問題)

v1.0 以降の roadmap で再評価する。

## 5. Roadmap

| Phase (Milestone) | 期間 | アウトプット | 完了基準 |
|---|---|---|---|
| `phase:setup` | 〜2026-04 | plugin scaffold、SKILL.md、bin adapter 雛形 | plugin として claude-code が認識 |
| `phase:v0.1` | 〜2026-05-19 (released) | continuity hooks + session-closer skill + Decision marker protocol + cross-issue escalation | L3 acceptance verification 完了 ([release](https://github.com/etoyama/claude-issueops/releases/tag/v0.1.0)) |
| `phase:v0.1` patch (v0.1.1) | 〜2026-05-28 (released) | #64 summary body fix + CLAUDE.md dev process + v0.1.1 release | [release v0.1.1](https://github.com/etoyama/claude-issueops/releases/tag/v0.1.1) |
| `phase:v0.2` | 未定 | GitHub Projects v2 opt-in 統合 ([Epic #16](https://github.com/etoyama/claude-issueops/issues/16)) | Projects v2 への Decision 自動連携 + 既存 v0.1 ユーザーの opt-in パス |
| `phase:v0.2` pilot (Meta #69) | 着手中 | 開発プロセス刷新 (CLAUDE.md / PRD / Epic Living Design Doc / Story 分解) + target-flag Epic (#65 + #66 統合) | new process が target-flag pilot で機能、CLAUDE.md / PRD / docs/design が揃う |
| `phase:v0.3` | 未定 | Issue rule engine M3' ([Epic #17](https://github.com/etoyama/claude-issueops/issues/17)) | Issue / label 状態遷移の declarative rule、SKILL.md からの参照 |
| **v1.0** | 未定 | semantic versioning 開始、settings keys / Decision marker protocol の互換性凍結 | breaking change が 6 ヶ月以上発生しないことを確認 |

## 6. Decision Points

各 phase 完了時に以下を判断する。

### v0.2 完了時

| 判断 | 条件 | 次フェーズ |
|---|---|---|
| **A. v0.3 進行** | Projects v2 利用率 ≥ 50% (dogfood セッション完了後に `gh project item-list` で手動集計し、Decision を投稿した Issue のうち Projects v2 にも紐付いた割合) | issue rule engine 着手 |
| **B. Projects v2 改善** | 利用率 < 30%、UX 起因 | v0.3 を後送りし v0.2 改善 |
| **C. plugin 分割** | Projects v2 機能が claude-issueops core から独立した方が利用者にとって良いと判明 | 別 plugin に切り出し、core は continuity + Decision capture に絞る |

### v0.3 完了時

| 判断 | 条件 | 意味 |
|---|---|---|
| **A. v1.0 へ** | rule engine の declarative spec が安定、breaking change なし 3 ヶ月以上 | v1.0 release、semantic versioning 開始 |
| **B. rule engine 縮小** | 過剰機能と判明、利用ケースが narrow | rule engine を opt-in にして core を保持 |
| **C. 設計やり直し** | rule engine が SKILL.md / bin / pure modules の 3 層を破壊 | アーキテクチャ再評価 |

### Meta #69 完了時 (v0.2 着手前)

| 判断 | 条件 | 意味 |
|---|---|---|
| **A. 新プロセス継続** | target-flag pilot が予定通り完了、Story 分解運用が機能 | v0.2 から全面採用 |
| **B. プロセス調整** | Living Design Doc の運用負荷が大きい | 軽量化 (例: Architecture overview のみ Living、他は append-only) |
| **C. プロセス撤回** | dogfooding の認知コストが claude-issueops core 改善より大きい | spec-workflow ベースに戻す |

## 7. Architecture & Contracts (Data Sources 相当)

### 3 層構造

claude-issueops は副作用境界を 3 層に分ける。境界を越える変更時は **3 層全部を同期する**:

| 層 | 場所 | 責務 |
|---|---|---|
| **Skill 層** | `skills/<skill>/SKILL.md` | LLM 推論、AskUserQuestion での対話、subcommand のオーケストレーション |
| **bin adapter 層** | `bin/*.py` | stdin/stdout JSON でやりとりする唯一の Python entrypoint、dispatch のみ |
| **pure module 層** | `src/issueops/*.py` | 副作用を持たない関数群、DI でテスト可能 |

詳細: [CLAUDE.md §5](../CLAUDE.md#5-コーディング原則)。subcommand contract drift の防止は v0.x 期間の最重要 NFR (#56 / #64 の教訓)。

### Decision marker protocol (固定、改変禁止)

```markdown
<!-- claude-issueops:session-closer:decision:<kebab-case-slug> -->
## Decision: <kebab-case-slug>

**What:** <一文>
**Why:** <理由・制約・動機>
**Alternatives considered:**
- <選択肢> -> <却下理由>
**Consequences:** <得るもの、失うもの、将来の影響>
```

このフォーマットは **v0.x / v1.0 を通じて凍結**。downstream tooling (Tier 2 dedup の marker scan、memory escalation の parse、外部の Issue 検索) が依存する。**README / README.ja の Decision marker サンプルは人間が読みやすいよう HTML コメント行を省略表示する場合がある**が、Tier 2 dedup の前提として **HTML コメント行は必須** であり、本 PRD §7 のフォーマットが完全な真実源。

### Subcommand contract (JSON in/out, schema_version=1)

- `read-transcript` / `resolve-issue` / `filter-dedup` / `post-decisions` / `commit-state` / `summary` / `escalate` / `save-pending` の 8 subcommand
- 真実源: `.spec-workflow/specs/session-closer/design.md` の "Skill ↔ bin Contract" 表 (v0.1、現行)。`docs/design/epic-NN-*.md` への移行は v0.2 以降の予定 (未作成、[Meta #69](https://github.com/etoyama/claude-issueops/issues/69) のサブタスク)
- `schema_version` 不一致は即 error、バージョン違いの bin と通信し続けない

### Issue 階層

```
Milestone (phase:vX.Y)
└── [Meta | Epic]
    └── Story
```

- Milestone = release phase (`phase:setup` / `phase:v0.1` / `phase:v0.2` / `phase:v0.3`)
- Meta = フェーズ全体の Decision 集約、横断論点 (`type:meta`)
- Epic = 機能追加 / リリース単位 (`type:epic`)
- Story = PR 1 本相当、AC 5 件以下、半日〜3 日 (`type:story`)

詳細: [CLAUDE.md §3](../CLAUDE.md#3-issue-階層と運用-claude-issueops-の-dogfooding)。

### 依存

| 依存 | 用途 | 最低バージョン |
|---|---|---|
| `gh` CLI | GitHub API access (issue / comment / view) | 2.40+ (`--json` フィールド指定が安定するバージョン) |
| Python | bin adapter / pure modules | 3.11+ (PEP 604 union 構文) |
| `uv` | 依存管理 + pytest 実行 | 0.4+ |
| Claude Code | plugin host | latest (skills / hooks API が利用可能なバージョン) |
| `pytest` | テストランナー | 8+ |

外部サービスは GitHub のみ。GitHub Enterprise / self-hosted GitHub への対応は v1.0 以降で再評価。

## 8. References

- [README.md](../README.md) — プロダクト概要、install 手順、Quickstart
- [README.ja.md](../README.ja.md) — 日本語版
- [CLAUDE.md](../CLAUDE.md) — 開発プロセス・コーディング原則・dogfooding ルール
- [CHANGELOG.md](../CHANGELOG.md) — release notes
- [CONTRIBUTING.md](../CONTRIBUTING.md) — 貢献ガイド (no-ff merge 等)
- [skills/session-closer/SKILL.md](../skills/session-closer/SKILL.md) — session-closer skill 仕様
- [.spec-workflow/specs/session-closer/](../.spec-workflow/specs/session-closer/) — v0.1 までの spec doc 群 (legacy、archive 扱い)
- [docs/design/](./design/) — v0.2 以降の Epic Living Design Doc (未作成、Meta #69 で導入予定)
- [Meta #69](https://github.com/etoyama/claude-issueops/issues/69) — 開発プロセス刷新 epic
- 関連 repo:
  - [insight-blueprint#132](https://github.com/etoyama/insight-blueprint/issues/132) — claude-issueops の OSS 抽出元
  - [boatrace-insight](https://github.com/etoyama/boatrace-insight) — 第一 dogfood ユーザー
