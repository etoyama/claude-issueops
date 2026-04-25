# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) starting from v1.0. v0.x releases may include breaking changes to settings keys; the decision marker protocol itself is frozen across all releases.

## [Unreleased]

### Added

- `.claude-plugin/plugin.json` manifest so Claude Code recognizes the directory as a plugin.
- `session-state/` ignored in `.gitignore`; per-session state files are derived data and must not be tracked.
- README initial version covering the protocol, configuration, hooks behavior, and roadmap.
- `CONTRIBUTING.md`, `CHANGELOG.md`, `CODE_OF_CONDUCT.md` for OSS readiness.
- **Marker parser + branch resolver + memory escalation** primitives (Issues #4, #5, #6) — pure modules with full unit-test coverage.
- **Continuity hooks**: `UserPromptSubmit` (briefing + post-compact restore, #9), `PreCompact` (snapshot save, #10), `SessionEnd` (fallback summary, #11). The hooks share a per-session state file via `state_writer.merge_update_state`, which provides atomic write + advisory `flock` + fsync of the tmp file and parent directory.
- **`session-closer` skill** (#8) — capture + close modes with two-tier dedup, AskUserQuestion-driven approval, gh-failure 3-choice fallback (save / discard / abort), AmbiguousResolution handling, idempotent summary posting, and memory escalation for `cross-issue` decisions. The skill is split across `skills/session-closer/SKILL.md` (LLM orchestration), `bin/session_closer.py` (8-subcommand JSON dispatcher), and pure modules under `src/issueops/`. End-to-end behaviour is verified by `VERIFICATION.md` recipes V-1〜V-15.
- **Test design template** (#26) integrated via `.spec-workflow/user-templates/`.

### Notes

- v0.1 is feature-complete; remaining work is L3 acceptance verification and the marketplace listing. Track under [Epic #7](https://github.com/etoyama/claude-issueops/issues/7).

[Unreleased]: https://github.com/etoyama/claude-issueops/compare/HEAD...HEAD
