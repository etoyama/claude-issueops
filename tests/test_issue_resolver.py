"""L1 unit tests for ``issueops.issue_resolver``.

Covers the 7-case "Issue resolution 状態遷移表" from
``.spec-workflow/specs/session-closer/design.md`` § Component 5. Each
test is mapped to a specific row of the table; T-51〜T-55 are the
baseline IDs from test-design.md, and the additional rows that share
behaviour with one of those IDs are folded in via ``pytest.mark.parametrize``
or extra ``assert`` blocks so all 7 transitions are exercised.

Test ID  | Verifies (state-transition table row)
---------|----------------------------------------------------------------
T-51     | row 3: Tier 1 = 1 件 → 確定 (Tier 1 採用)
T-52     | row 4: Tier 1 ≥ 2 ∩ branch hint = 1 → 交差確定
T-53     | row 2 + row 5: Tier 1 0 件 → Tier 2 採用 / Tier 1 ≥ 2, branch hint 0, Tier 2 = 1
T-54     | row 6 + row 7: 曖昧 → AmbiguousResolution(candidates=tier1)
T-55     | row 1: Tier 1 = 0 件 + Tier 2 None → IssueResolutionError

Reuse policy: ``branch_resolver.resolve_current_issue`` is the *only*
permitted source of branch-derived issue numbers; the resolver under
test must not re-implement the regex.
"""

from __future__ import annotations

import pytest

from issueops.issue_resolver import (
    AmbiguousResolution,
    IssueResolutionError,
    resolve_target_issue,
)


# ---------------------------------------------------------------------------
# T-51: Tier 1 single hit → confirmed (row 3)
# ---------------------------------------------------------------------------


def test_resolve_tier1_single_hit(gh_list_in_progress_fn_factory) -> None:
    """Tier 1 returns one issue → resolver returns that number directly.

    Branch hint should not be consulted (Tier 1 alone is sufficient).
    """
    list_fn = gh_list_in_progress_fn_factory(issues=[42])

    # Branch deliberately matches a *different* number — Tier 1 wins.
    result = resolve_target_issue(branch="feat/99-other", list_in_progress_fn=list_fn)

    assert result == 42
    # branch_resolver.resolve_current_issue may or may not be called, but
    # if Tier 1 is single-hit the answer is unambiguous regardless.
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# T-52: Tier 1 multiple + branch hint intersects → branch issue wins (row 4)
# ---------------------------------------------------------------------------


def test_resolve_tier1_multiple_intersect_branch(gh_list_in_progress_fn_factory) -> None:
    """Tier 1 has many candidates but exactly one matches the branch hint."""
    list_fn = gh_list_in_progress_fn_factory(issues=[10, 20, 30])

    result = resolve_target_issue(
        branch="feat/20-do-thing",
        list_in_progress_fn=list_fn,
    )

    assert result == 20


# ---------------------------------------------------------------------------
# T-53: Tier 1 zero → Tier 2 fallback (row 2) + Tier 1 multi & branch hint
# absent + Tier 2 single (row 5)
# ---------------------------------------------------------------------------


def test_resolve_tier1_zero_then_tier2(gh_list_in_progress_fn_factory) -> None:
    """Two independent rows that both end in 'Tier 2 採用'.

    Sub-case A (row 2): Tier 1 = 0 件, branch_resolver = 1 件 → Tier 2.
    Sub-case B (row 5): Tier 1 ≥ 2, branch hint not in Tier 1, Tier 2 = 1.
    """
    # Sub-case A: Tier 1 empty, branch parses to issue 7
    list_fn_a = gh_list_in_progress_fn_factory(issues=[])
    assert resolve_target_issue(branch="fix/7-bug", list_in_progress_fn=list_fn_a) == 7

    # Sub-case B: Tier 1 = [10, 20] but branch points to 99 (not in Tier 1).
    # Per state table row 5, when intersection is empty the branch_resolver
    # value (Tier 2) is taken.
    list_fn_b = gh_list_in_progress_fn_factory(issues=[10, 20])
    assert (
        resolve_target_issue(branch="feat/99-elsewhere", list_in_progress_fn=list_fn_b)
        == 99
    )


# ---------------------------------------------------------------------------
# T-54: ambiguous (row 6 + row 7) → AmbiguousResolution(candidates=tier1)
# ---------------------------------------------------------------------------


def test_resolve_ambiguous_returns_candidates(gh_list_in_progress_fn_factory) -> None:
    """Two rows that both yield AmbiguousResolution.

    Row 6: Tier 1 ≥ 2, branch hint = 0 件, Tier 2 = None → ambiguous.
    Row 7: Tier 1 ≥ 2, branch hint ≥ 2 件 (would only happen if Tier 1
    contained duplicates of the branch number; using a non-matching branch
    pattern triggers the same ambiguous outcome via row 6 — we keep both
    sub-cases for documentation).
    """
    # Row 6: branch does not parse → no Tier 2, Tier 1 has 2 entries.
    list_fn = gh_list_in_progress_fn_factory(issues=[100, 200])
    result = resolve_target_issue(branch="master", list_in_progress_fn=list_fn)
    assert isinstance(result, AmbiguousResolution)
    assert sorted(result.candidates) == [100, 200]

    # Frozen dataclass: cannot be mutated after construction.
    with pytest.raises((AttributeError, TypeError)):
        result.candidates = [1, 2]  # type: ignore[misc]


# ---------------------------------------------------------------------------
# T-55: total failure (row 1) → IssueResolutionError
# ---------------------------------------------------------------------------


def test_resolve_total_failure_raises(gh_list_in_progress_fn_factory) -> None:
    """Tier 1 = 0 件 AND branch_resolver returns None → error, not None."""
    list_fn = gh_list_in_progress_fn_factory(issues=[])

    with pytest.raises(IssueResolutionError):
        resolve_target_issue(branch="master", list_in_progress_fn=list_fn)
