<!--
PR template for claude-issueops.
- Story PR: 5 観点 12 items 上限 (CLAUDE.md §9 / docs/PRD.md §7)
- Epic 完了 PR: 6 観点 15 items 上限
- Refs / Closes は本文または Linked issues UI のいずれかで OK
- 詳細は CLAUDE.md §9 を参照
-->

## Summary

<!-- 1-3 bullet points で「何を」「なぜ」変えたか -->

-

## Self review

<!--
軽量コードレビュー (CLAUDE.md §9) の結果をここに貼る。
実行手段の優先順位: subagent (orchestra:general-purpose) → codex CLI 直叩き → team-review

5 観点 (Story PR):
1. ロジック誤り / off-by-one bugs
2. edge case がテスト未網羅 (partial failure / idempotency / 空入力 / 特殊文字)
3. subcommand contract drift (SKILL.md / design.md / bin / tests が同期しているか)
4. 3 層境界違反 (AskUserQuestion を Python から呼んでいないか、bin にロジックが漏れていないか、shell=True が使われていないか)
5. YAGNI 違反 / 過剰設計

Epic 完了時の追加観点:
6. doc / README 同期 (Epic Design Doc / CLAUDE.md / PRD / SKILL.md が実装と整合しているか)

指摘の取り扱い: high + medium を本 PR で fix、low は Meta Issue or 次 Story に defer。
本セクションが空でも OK (CHANGELOG-only / typo-fix のような自明な変更時)。
-->

### Reflected in this PR (high + medium)

| 優先度 | 指摘 | 対応 |
|---|---|---|
| - | - | - |

### Deferred to follow-up (low)

<!-- 該当する Meta Issue にコメントとして記録した上で、ここにリンクを貼る -->

- N/A

## Test plan

<!-- 検証チェックリスト。pytest、bin 直叩き、bash スクリプト、目視確認 等 -->

- [ ]
- [ ]

## 関連

<!-- Refs / Closes (例: Refs #69 / Closes #65 #66)。dependency があれば直前の PR も -->

- Refs #
