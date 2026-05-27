# ARCHIVED: session-closer spec docs (phase:v0.1)

> **このディレクトリは phase:v0.1 (v0.1.0 / v0.1.1) までの spec doc 群。Meta [#69](https://github.com/etoyama/claude-issueops/issues/69) で開発プロセスを刷新したため、v0.1.1 以降は archive 扱いで凍結する。**

## なぜ archive か

phase:v0.1 は spec-workflow の 4 ファイル制 (requirements / design / tasks / test-design) で開発した。重い phase の最初の release には十分機能したが:

- 4 ファイル制は **漸進開発** と整合性が低い (Story 完了ごとに design が更新されない)
- requirements / tasks / test-design は **release 完了後に陳腐化** する (実装が真実源になる)
- 「決定→記述→実装」の流れが「実装→決定→記述」になり、Decision の鮮度が落ちる

phase:v0.2 以降は **Epic Living Design Doc** (`docs/design/epic-NN-<topic>.md`) に移行する。Architecture / Module structure / Subcommand contract を Living セクションとして漸進更新し、Story 完了ごとに timeline を append-only で追加する。

詳細: [CLAUDE.md §6](../../../../CLAUDE.md#6-design-doc-運用-epic-living-design-doc) / [docs/PRD.md §7](../../../PRD.md#7-architecture--contracts-data-sources-相当)。

## ファイル一覧 (snapshot at v0.1.1)

本ディレクトリは `.spec-workflow/specs/session-closer/` のうち archive 価値のある 4 主要 spec + 本 ARCHIVED.md を凍結 snapshot として置いたもの (`.gitignore` で元のディレクトリは個人 artifacts として ignored、こちらは tracked)。

| ファイル | 用途 (v0.1 当時) | 現状 |
|---|---|---|
| `requirements.md` | R-1〜R-10 の機能要求 | freeze、実装が真実源 |
| `design.md` | アーキテクチャ + Skill ↔ bin Contract 表 | freeze、subcommand contract の真実源は SKILL.md / bin / tests に分散 (CLAUDE.md §5 の 3 層構造を参照) |
| `tasks.md` | Task 1〜13 の実装ステップ | freeze、phase:v0.1 release で完了 |
| `test-design.md` | L1 / L2 / L3 のテスト戦略 | freeze、tests/ ディレクトリが真実源 |
| `Implementation Logs/` (除外) | 実装中の Decision ログ、MCP 内部 artifact | この tracked snapshot には含めず、元 (`.spec-workflow/specs/session-closer/Implementation Logs/`) のみに残置 |

## 変更が必要になった場合

このディレクトリのファイルを **直接編集しない**。代わりに:

1. 対応する Epic の Living Design Doc を `docs/design/epic-NN-<topic>.md` に新規作成 (まだ無ければ)
2. 新 Doc 内で legacy 記述を上書き or 明示的に refute
3. legacy spec の該当箇所には「[Updated by epic-NN](../../design/epic-NN-...md)」と注釈を追加 (本ファイル ARCHIVED.md か当該 spec ファイル冒頭)

`docs/design/` ディレクトリは [Meta #69](https://github.com/etoyama/claude-issueops/issues/69) のサブタスクで新設予定 (v0.2 着手時、target-flag Epic が初運用)。

## 参考: legacy spec を読むタイミング

- **historical context が欲しい時**: phase:v0.1 の設計判断を遡及的に確認 (例: subcommand 分離理由 → design.md の "Codex 再レビュー対応" 項)
- **L3 acceptance criteria 確認**: test-design.md L1/L2/L3 区分は v0.1 acceptance verification の根拠
- **R-番号の参照**: 既存コード/コメントの "R-1" "R-9.4" 等の参照解決
