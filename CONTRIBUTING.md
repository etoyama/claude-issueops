# Contributing to claude-issueops

Thanks for considering a contribution. This project follows a few opinionated conventions to keep the protocol stable and the project navigable. Please read them before opening a PR.

## Filing issues

Every issue must carry three labels:

- `type:*` — `epic`, `feature`, `chore`, `docs`, or `bug`.
- `phase:*` — `setup`, `v0.1`, `v0.2`, or `v0.3`.
- `area:*` — `hooks`, `skill`, `protocol`, `settings`, or `plugin-meta`. May be omitted for cross-cutting work.

Children of an Epic must include `Parent: #<n>` on the first line of the body. The `v0.3` issue rule engine will enforce this; until then, please add it by hand.

Acceptance criteria belong inside the issue body as a Markdown checklist (`- [ ]`). Keep them testable.

## Branch and commit conventions

- Branch name: `<type>/<issue-number>-<short-slug>` where `<type>` is one of `feat`, `fix`, `chore`, `refactor`. Example: `feat/132-session-continuity`.
- Commit message: a short imperative subject, followed by a body that explains the *why*. Reference the issue with `(#N)` in the subject and `Refs #N` in the body when appropriate.
- One issue per commit is the default. Squash later commits onto a single PR-level merge if the work is small.

## The decision marker protocol is frozen

Comments that record decisions must use this exact format. Do not rename fields, reorder them, or omit any of the four. Tooling depends on the regex matching both the heading and the immediately following `**What:**` line.

```markdown
## Decision: <kebab-case-slug>

**What:** <one sentence describing what was decided>
**Why:** <reasoning, constraints, motivation>
**Alternatives considered:**
- <option> -> <reason for rejection>
**Consequences:** <what this gains, what this gives up, what may break later>
```

Slugs are `kebab-case` and unique within the issue. Re-using a slug means deleting or rewriting the prior comment by hand.

If you find a case where the protocol does not fit, file an issue with `type:feature` and `area:protocol`. Do not adjust the format unilaterally; downstream parsers will break.

## Pull requests

1. Open a draft PR early. Reference the issue in the description.
2. Update the affected `Acceptance Criteria` checkboxes in the parent issue body as part of the PR.
3. If the PR introduces a behavior change visible to users, add an entry to `CHANGELOG.md` under `[Unreleased]`.
4. CI must be green. If you have to skip a hook, explain why in the PR description.
5. Mark the PR ready for review. Reviews focus on the protocol, the public surface, and tests.

## Local development

```bash
git clone https://github.com/etoyama/claude-issueops.git
cd claude-issueops
claude --plugin-dir ./
```

The `--plugin-dir` flag loads the plugin without installing it. Use `/reload-plugins` after changes.

## Code of conduct

By participating you agree to abide by [the Contributor Covenant v2.1](./CODE_OF_CONDUCT.md). Report concerns to the contact address listed there.
