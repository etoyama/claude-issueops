"""Tests for the decision marker parser.

The marker protocol is frozen and documented in README.md and CONTRIBUTING.md.
These tests pin the parsing behavior so downstream tooling can rely on it.
"""

from issueops.marker_parser import Decision, parse_decisions


SINGLE_VALID = """\
Some preamble that is not a decision.

## Decision: scope-confirmation

**What:** v0.1 scope locked to five features (continuity hooks, capture skill, marker protocol, branch regex, memory escalate).
**Why:** adoption coverage is the top-priority quality attribute, so Issues-only users must get value without GitHub Projects v2.
**Alternatives considered:**
- All-in v0.1 (R-A) -> first-release time slips, rule schema freezes too early, rejected.
- Split rule engine into a separate plugin (R-C) -> forces marketplace design up front, rejected.
**Consequences:** Shortest first-release path; v0.2 and v0.3 land additively without breaking changes; Projects users wait until v0.2 for the in-progress tier.

Trailing text below the decision.
"""


def test_parses_single_valid_decision():
    decisions = parse_decisions(SINGLE_VALID)
    assert len(decisions) == 1
    d = decisions[0]
    assert isinstance(d, Decision)
    assert d.slug == "scope-confirmation"
    assert "five features" in d.what
    assert "adoption coverage" in d.why
    assert "All-in v0.1" in d.alternatives
    assert "first-release path" in d.consequences


def test_ignores_marker_inside_fenced_code_block():
    body = """\
Some prose explaining the format.

```markdown
## Decision: example-slug

**What:** This is just documentation, not a real decision.
**Why:** It is inside a code block.
**Alternatives considered:**
- N/A
**Consequences:** none
```

End of prose.
"""
    decisions = parse_decisions(body)
    assert decisions == []


def test_ignores_marker_inside_blockquote():
    body = """\
Quoting an older comment:

> ## Decision: outdated-slug
>
> **What:** something we already retracted.
> **Why:** historical context.
> **Alternatives considered:**
> - N/A
> **Consequences:** N/A

Replying with the new direction.
"""
    decisions = parse_decisions(body)
    assert decisions == []


def test_rejects_non_kebab_case_slug():
    body = """\
## Decision: NotKebabCase

**What:** This should not be matched because slug is CamelCase.
**Why:** Frozen protocol mandates kebab-case slugs.
**Alternatives considered:**
- snake_case -> rejected.
**Consequences:** Tooling can rely on a single slug shape.
"""
    decisions = parse_decisions(body)
    assert decisions == []


def test_rejects_decision_missing_required_field():
    # Missing **Consequences:** entirely.
    body = """\
## Decision: missing-consequences

**What:** A decision with only three fields.
**Why:** Authors sometimes forget the consequences section.
**Alternatives considered:**
- Accept partial decisions -> rejected, parser must enforce all four fields.
"""
    decisions = parse_decisions(body)
    assert decisions == []


def test_parses_two_decisions_in_one_comment():
    body = """\
## Decision: first-slug

**What:** Decision A.
**Why:** Reason A.
**Alternatives considered:**
- Option A1 -> rejected.
**Consequences:** Outcome A.

Some prose between the two decisions.

## Decision: second-slug

**What:** Decision B.
**Why:** Reason B.
**Alternatives considered:**
- Option B1 -> rejected.
**Consequences:** Outcome B.
"""
    decisions = parse_decisions(body)
    assert [d.slug for d in decisions] == ["first-slug", "second-slug"]
    assert decisions[0].what == "Decision A."
    assert decisions[1].consequences == "Outcome B."


def test_handles_decision_at_end_of_text_without_trailing_newline():
    body = (
        "## Decision: trailing-slug\n"
        "\n"
        "**What:** End of file decision.\n"
        "**Why:** No trailing newline after the last field.\n"
        "**Alternatives considered:**\n"
        "- None considered.\n"
        "**Consequences:** Parser must terminate cleanly at EOF."
    )
    decisions = parse_decisions(body)
    assert len(decisions) == 1
    assert decisions[0].slug == "trailing-slug"
    assert decisions[0].consequences.endswith("at EOF.")


def test_alternatives_field_can_span_multiple_bullet_lines():
    body = """\
## Decision: many-alternatives

**What:** Choose option C.
**Why:** Quality attribute X dominates.
**Alternatives considered:**
- Option A -> rejected, costs too much in attribute Y.
- Option B -> rejected, blocks future migration.
- Option D -> not viable, fails compliance.
**Consequences:** Migration is straightforward; team must learn new tool.
"""
    decisions = parse_decisions(body)
    assert len(decisions) == 1
    alts = decisions[0].alternatives
    assert "Option A" in alts
    assert "Option B" in alts
    assert "Option D" in alts


def test_consequences_field_can_span_multiple_lines():
    body = """\
## Decision: detailed-consequences

**What:** Adopt approach Z.
**Why:** Reduces lock-in.
**Alternatives considered:**
- Approach Y -> rejected, increases coupling.
**Consequences:**
- (+) Approach Z keeps modules swappable.
- (+) Test suites stay isolated.
- (-) Initial migration takes two weeks.
"""
    decisions = parse_decisions(body)
    assert len(decisions) == 1
    cons = decisions[0].consequences
    assert "swappable" in cons
    assert "two weeks" in cons


def test_rejects_wrong_heading_level():
    # h1 and h3 must be rejected; only h2 (## Decision:) is the marker.
    body_h1 = """\
# Decision: wrong-level

**What:** This is h1, not h2.
**Why:** Protocol freezes h2.
**Alternatives considered:**
- N/A
**Consequences:** N/A
"""
    body_h3 = """\
### Decision: also-wrong

**What:** This is h3, not h2.
**Why:** Protocol freezes h2.
**Alternatives considered:**
- N/A
**Consequences:** N/A
"""
    assert parse_decisions(body_h1) == []
    assert parse_decisions(body_h3) == []
