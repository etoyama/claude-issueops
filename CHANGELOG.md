# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) starting from v1.0. v0.x releases may include breaking changes to settings keys; the decision marker protocol itself is frozen across all releases.

## [Unreleased]

## [0.1.1] - 2026-05-28

### Fixed

- **session-closer summary body**: the `summary` subcommand now expands `captured_slugs_total` into a `### Captured decisions (N)` section instead of posting only the marker + H2 header. Affected close-mode comments since v0.1.0; without this fix the dogfood UX was a blank summary comment ([#64](https://github.com/etoyama/claude-issueops/issues/64), [#68](https://github.com/etoyama/claude-issueops/pull/68)).
  - New pure helper `build_summary_body(session_id, captured_slugs_total)`; `build_summary_marker` is now a thin idempotency-anchor helper.
  - `CaptureResult` exposes `captured_slugs_total` (existing state + this run, deduplicated, order-preserving) so `run_close` can pass the full list to the body builder without re-reading state.
  - SKILL.md §6 now documents the body composition rule and the optional `body` payload escape hatch.

### Added

- **`CLAUDE.md` 作業ガイドライン** at the repo root ([#69](https://github.com/etoyama/claude-issueops/issues/69), [#70](https://github.com/etoyama/claude-issueops/pull/70)). Ported boatrace-insight's CLAUDE.md structure (Milestone→[Meta|Epic]→Story 3-tier issue hierarchy, Epic Living Design Doc, lightweight 5-perspective code review) and adapted it to claude-issueops (3-layer SKILL.md/bin/pure modules architecture, Decision marker protocol self-reference, subcommand contract drift prevention).
- New issue labels: `type:meta`, `type:story`, `area:process`.

### Documentation

- README.ja.md added (Japanese translation) and status bumped to `v0.1.0 released` ([#58](https://github.com/etoyama/claude-issueops/issues/58), [#59](https://github.com/etoyama/claude-issueops/pull/59)).
- README marketplace install example documented ([#61](https://github.com/etoyama/claude-issueops/issues/61), [#63](https://github.com/etoyama/claude-issueops/pull/63)).
- `.claude-plugin/marketplace.json` (single-plugin marketplace manifest) added so the repo can be added directly via `/plugin marketplace add etoyama/claude-issueops` ([#61](https://github.com/etoyama/claude-issueops/issues/61), [#62](https://github.com/etoyama/claude-issueops/pull/62)).
- SKILL.md subcommand contract aligned with the bin implementation ([#56](https://github.com/etoyama/claude-issueops/issues/56), [#57](https://github.com/etoyama/claude-issueops/pull/57)).

### Notes

- Marketplace cache is keyed by the manifest version string. v0.1.0 users must re-install (`/plugin uninstall claude-issueops` + `/plugin marketplace add etoyama/claude-issueops`) or remove `~/.claude/plugins/cache/claude-issueops` to pick up the v0.1.1 sources; otherwise the cached v0.1.0 sources continue to back the running plugin.

## [0.1.0] - 2026-05-19

### Added

- `.claude-plugin/plugin.json` manifest so Claude Code recognizes the directory as a plugin.
- `session-state/` ignored in `.gitignore`; per-session state files are derived data and must not be tracked.
- README initial version covering the protocol, configuration, hooks behavior, and roadmap.
- `CONTRIBUTING.md`, `CHANGELOG.md`, `CODE_OF_CONDUCT.md` for OSS readiness.
- **Marker parser + branch resolver + memory escalation** primitives (Issues #4, #5, #6) — pure modules with full unit-test coverage.
- **Continuity hooks**: `UserPromptSubmit` (briefing + post-compact restore, #9), `PreCompact` (snapshot save, #10), `SessionEnd` (fallback summary, #11). The hooks share a per-session state file via `state_writer.merge_update_state`, which provides atomic write + advisory `flock` + fsync of the tmp file and parent directory.
- **`session-closer` skill** (#8) — capture + close modes with two-tier dedup, AskUserQuestion-driven approval, gh-failure 3-choice fallback (save / discard / abort), AmbiguousResolution handling, idempotent summary posting, and memory escalation for `cross-issue` decisions. The skill is split across `skills/session-closer/SKILL.md` (LLM orchestration), `bin/session_closer.py` (8-subcommand JSON dispatcher), and pure modules under `src/issueops/`. End-to-end behaviour is verified by `VERIFICATION.md` recipes V-1〜V-15.
- **Test design template** (#26) integrated via `.spec-workflow/user-templates/`.

### Documentation

- CONTRIBUTING.md gains a "Merge strategy" section documenting the no-ff merge convention, why squash/rebase are not used, and the `git revert -m 1 <merge-commit>` revert procedure (#19).

### Notes

- v0.1 is feature-complete; remaining work is L3 acceptance verification and the marketplace listing. Track under [Epic #7](https://github.com/etoyama/claude-issueops/issues/7).

[Unreleased]: https://github.com/etoyama/claude-issueops/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/etoyama/claude-issueops/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/etoyama/claude-issueops/releases/tag/v0.1.0
