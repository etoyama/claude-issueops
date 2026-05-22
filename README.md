# claude-issueops

Persist session context and decisions across Claude Code sessions via GitHub Issues.

English | [日本語](./README.ja.md)

> Status: [v0.1.0 released](https://github.com/etoyama/claude-issueops/releases/tag/v0.1.0) — continuity hooks, the `session-closer` skill (capture / close), the decision marker protocol, and cross-issue memory escalation are all on `master`. L3 acceptance verified ([Epic #7](https://github.com/etoyama/claude-issueops/issues/7) closed). A marketplace listing will follow. Origin: this is the OSS extraction of [insight-blueprint#132](https://github.com/etoyama/insight-blueprint/issues/132).

## What & Why

Claude Code sessions lose context at five predictable boundaries: session start, mid-session drift, automatic compaction, session end, and the next session start. Existing memory mechanisms cover personal preferences but not "what was I doing on this issue, and why did we decide X instead of Y?". `claude-issueops` makes the GitHub Issue itself the persistent memory layer: hooks read recent comments at session start and after compaction, and a skill captures decisions back to the issue when the session ends. Cross-issue knowledge escalates to Claude's standard memory as `reference` entries.

## Install

### Via Claude Code marketplace (recommended)

```text
/plugin marketplace add etoyama/claude-issueops
/plugin install claude-issueops@claude-issueops
```

(`etoyama/claude-issueops` is shorthand for the GitHub repo; the SSH form `git@github.com:etoyama/claude-issueops.git` also works.)

Skills are namespaced under the plugin name, so commands appear as `/claude-issueops:<skill>` once installed.

### Locally for development

```bash
git clone https://github.com/etoyama/claude-issueops.git
claude --plugin-dir ./claude-issueops
```

`--plugin-dir` loads the plugin without going through the marketplace machinery; useful when iterating on the plugin itself.

A community marketplace listing (Anthropic's `claude-plugins-community`) is tracked in [#60](https://github.com/etoyama/claude-issueops/issues/60).

## Quickstart

1. Work on a branch named after an issue, e.g. `feat/132-session-continuity`.
2. Open Claude Code. The first prompt of the session triggers a briefing: a one-line list of in-progress issues, plus the current issue's body excerpt and any prior decisions.
3. Make decisions. When you reach a meaningful conclusion, run `/claude-issueops:session-closer --capture` to extract and post the decision as a comment on the current issue.
4. End the session with `/claude-issueops:session-closer` (no flag). The skill captures any remaining decisions, posts a session summary, and escalates cross-issue learnings to Claude's standard memory.

If you forget to invoke the skill, a `SessionEnd` hook posts a minimal summary as a fallback.

## Decision marker protocol

Decisions are recorded as issue comments using a frozen format. The format is part of the protocol and must not be customized; downstream tooling depends on the exact shape.

```markdown
## Decision: <kebab-case-slug>

**What:** <one sentence describing what was decided>
**Why:** <reasoning, constraints, or motivation>
**Alternatives considered:**
- <option> -> <reason for rejection>
**Consequences:** <what this gains, what this gives up, what may break later>
```

Extraction uses two combined regexes: `^## Decision: (?<slug>[a-z0-9-]+)\s*$` for the heading, immediately followed by `^\*\*What:\*\*` to reject false positives (quoted text, code blocks).

A slug is `kebab-case`, unique within the issue. Re-using a slug requires deleting or rewriting the prior comment by hand; the capture flow refuses to overwrite.

## Configuration

Defaults ship with the plugin. Override per-project in `.claude/settings.json`:

```jsonc
{
  "issueops": {
    "stateDir": "${CLAUDE_PROJECT_DIR}/session-state",
    "branch": {
      "issuePattern": "(?:feat|fix|chore|refactor)/(\\d+)-",
      "fallback": "latest-in-progress"
    },
    "projects": {
      "enabled": false
    },
    "memory": {
      "escalate": true,
      "type": "reference"
    }
  }
}
```

| Key | Default | Purpose |
|---|---|---|
| `stateDir` | `${CLAUDE_PROJECT_DIR}/session-state` | Per-session state file location. Must be gitignored. `CLAUDE_PROJECT_DIR` is the env var Claude Code exposes to plugin scripts. |
| `branch.issuePattern` | `(?:feat\|fix\|chore\|refactor)/(\d+)-` | Regex with one capture group for the issue number. |
| `branch.fallback` | `latest-in-progress` | What to do when the pattern does not match. Alternative: `none`. |
| `projects.enabled` | `false` | Opt in to GitHub Projects v2 integration (added in v0.2). |
| `memory.escalate` | `true` | Whether `cross-issue` decisions are written to Claude's standard memory. |
| `memory.type` | `reference` | Memory type to use when escalating. |

## Hooks behavior

Three hooks ship in v0.1:

| Event | Role | Injects context? |
|---|---|---|
| `UserPromptSubmit` | Session briefing on the first prompt; restore current-issue context after a compaction. | Yes (`additionalContext`) |
| `PreCompact` | Save the current-issue snapshot to the state file so restore works. | No (save only) |
| `SessionEnd` | Fallback summary post when the user did not invoke `/claude-issueops:session-closer`. | No (post only) |

`SessionStart` is intentionally not used: `UserPromptSubmit` runs at the same effective moment for context purposes and is the only hook that supports `additionalContext` injection both at session start and after compaction. A single hook keeps the lifecycle debuggable.

**On `additionalContext` durability** ([Claude Code docs](https://code.claude.com/docs/en/hooks#add-context-for-claude)): the injected briefing is saved to the session transcript and **replayed verbatim** when the session is resumed via `--continue` or `--resume`. The briefing is therefore frozen at the moment it was first injected — the in-progress issues list and prior-decisions excerpt seen on resume are the snapshot from the original session start, not a fresh fetch. This is by design (the briefing is meant to anchor the session, not to track live state); when you want fresh data, end the session and start a new one. The hook's exact position relative to `CLAUDE.md` in the assembled prompt is implementation-detail and not part of the contract — we treat it as "alongside the user prompt" per the documented wording.

## Skills

### `session-closer`

The skill that closes the loop: captures decisions back to the issue and (optionally) escalates cross-issue learnings to Claude's standard memory. Two modes:

| Mode | Invocation | What it does |
|---|---|---|
| `capture` | `/claude-issueops:session-closer --capture` | Reads the recent transcript, asks for confirmation on each detected Decision via `AskUserQuestion`, posts approved Decisions as issue comments with the frozen marker, updates `state.captured_slugs`. Run mid-session whenever you reach a meaningful conclusion. |
| `close` (default) | `/claude-issueops:session-closer` | Runs `capture` plus an idempotent session-summary comment, plus memory escalation for `final_scope = cross-issue` Decisions. Run at session end. |

Key guarantees:

- **Two-tier dedup**: a slug already in `state.captured_slugs` (Tier 1) or already present as a `decision:<slug>` marker on the issue (Tier 2) is skipped. Re-running the skill is safe.
- **Subcommand separation**: posts are committed to GitHub *before* state is written. If state-write fails after a successful post, the next run sees the post via Tier 2 dedup and skips it. The state file always reflects what was actually posted.
- **gh failure → 3-choice fallback**: on `gh` error the skill asks save / discard / abort. "save" persists the unposted Decisions to `<sid>.pending-decisions.json` so they can be retried in a later session.
- **AmbiguousResolution**: when the branch + `status:in-progress` label combination yields multiple candidate issues, the skill prompts to pick one before posting.

If the user forgets to invoke the skill, the `SessionEnd` fallback hook posts a minimal summary (no decision extraction — that requires interactive confirmation).

State file shape (`<stateDir>/<session_id>.json`):

```json
{
  "session_id": "abc123",
  "issue_number": 132,
  "briefing_done": true,
  "pending_restore": null,
  "compact_count": 0,
  "last_processed_offset": 1234,
  "captured_slugs": ["two-layer-architecture"]
}
```

## Roadmap

| Release | Scope |
|---|---|
| v0.1 | Session continuity hooks, decision capture skill, marker protocol, branch-to-issue regex, memory escalation. |
| v0.2 | GitHub Projects v2 integration as an opt-in feature (default off). Adds an "in progress" tier to the briefing. |
| v0.3 | Issue rule engine. Reads `.claude-issueops/issue-rules.yaml` and intercepts `gh issue create` / `gh issue edit` to suggest fixes for missing labels, parent links, or template fields. Violations surface via `additionalContext` and `permissionDecision: ask` rather than hard blocks. |

The marker protocol is frozen across releases. Settings keys may evolve in v0.x but will follow semver from v1.0.

## Project layout

```
claude-issueops/
├── .claude-plugin/
│   └── plugin.json           # plugin manifest
├── skills/
│   └── session-closer/
│       └── SKILL.md          # capture + close orchestration
├── bin/                      # subprocess entrypoints invoked by hooks and skill
│   ├── userpromptsubmit_hook.py
│   ├── precompact_hook.py
│   ├── sessionend_hook.py
│   └── session_closer.py     # 8-subcommand JSON dispatcher for the skill
├── src/issueops/             # pure Python modules (no I/O, callable-injection)
│   ├── path_utils.py         # session-id validation + atomic-write primitives
│   ├── state_writer.py       # single window for state-file writes
│   ├── pending_decisions.py  # gh-failure "save" branch
│   ├── transcript_reader.py
│   ├── decision_extractor.py
│   ├── dedup_checker.py
│   ├── issue_resolver.py     # branch + status:in-progress → issue number
│   ├── gh_adapters.py        # subprocess wrappers + failure classification
│   ├── verification_fixture.py  # AskUserQuestion bypass for L3 verification
│   ├── session_closer.py     # orchestrator (run_capture / run_close)
│   ├── marker_parser.py
│   ├── memory_escalate.py
│   └── branch_resolver.py
├── tests/                    # pytest suite (217 unit tests, ~82% coverage)
│   └── fixtures/transcripts/ # L3 verification transcripts (LLM-extractable)
├── verification-fixtures/    # JSON fixtures for L3 verification recipes
├── VERIFICATION.md           # V-1〜V-15 Bash verification recipes
├── scripts/
│   ├── l3-acceptance.sh             # bash-only V-X driver (V-3, V-9 Run 1, V-10 Run 2, V-14)
│   └── cleanup-l3-verification-issues.sh  # sweep [V- prefix open issues
├── README.md
├── CONTRIBUTING.md
├── CHANGELOG.md
├── CODE_OF_CONDUCT.md
└── LICENSE
```

Implementation lands incrementally. See the [v0.1 Epic](https://github.com/etoyama/claude-issueops/issues/7) for the open work.

## License

[MIT](./LICENSE) (c) 2026 etoyama.
