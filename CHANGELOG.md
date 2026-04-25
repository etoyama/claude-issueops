# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) starting from v1.0. v0.x releases may include breaking changes to settings keys; the decision marker protocol itself is frozen across all releases.

## [Unreleased]

### Added

- `.claude-plugin/plugin.json` manifest so Claude Code recognizes the directory as a plugin.
- `session-state/` ignored in `.gitignore`; per-session state files are derived data and must not be tracked.
- README initial version covering the protocol, configuration, hooks behavior, and roadmap.
- `CONTRIBUTING.md`, `CHANGELOG.md`, `CODE_OF_CONDUCT.md` for OSS readiness.

### Notes

- v0.1 features (continuity hooks, decision capture skill, marker parser, branch regex, memory escalation) are tracked under [Epic #7](https://github.com/etoyama/claude-issueops/issues/7) and are not yet implemented.

[Unreleased]: https://github.com/etoyama/claude-issueops/compare/HEAD...HEAD
