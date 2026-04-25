"""Resolve the target GitHub issue for the session-closer skill.

This module implements the 7-row state-transition table from
``.spec-workflow/specs/session-closer/design.md`` § Component 5.
Resolution combines two independent signals:

- **Tier 1**: the list of currently in-progress issues (label-driven,
  injected as ``list_in_progress_fn`` so this module stays subprocess-free).
- **Tier 2**: the issue number parsed from the current branch via
  :func:`issueops.branch_resolver.resolve_current_issue` (which we reuse
  rather than re-implementing the regex — single source of truth).

Outcome:
- An ``int`` when the issue is uniquely determined.
- An :class:`AmbiguousResolution` carrying the Tier 1 candidates when the
  caller (SKILL.md) needs to ask the user to disambiguate.
- :class:`IssueResolutionError` raised when neither tier yields a
  candidate at all.

Returning ``None`` is intentionally **not** part of the contract — the
SKILL.md side handles ``int`` vs. ``AmbiguousResolution`` and treats the
exception as an abort signal. See design.md § Component 5 for the
rationale.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Union

from issueops.branch_resolver import (
    DEFAULT_BRANCH_PATTERN,
    extract_issue_number,
)

__all__ = [
    "AmbiguousResolution",
    "IssueResolutionError",
    "resolve_target_issue",
    "DEFAULT_BRANCH_PATTERN",
]


class IssueResolutionError(Exception):
    """Raised when neither Tier 1 nor Tier 2 yields any candidate.

    Maps to design state-table row 1 (Tier 1 = 0 件, branch_resolver = None).
    """


@dataclass(frozen=True)
class AmbiguousResolution:
    """Multiple candidate issue numbers; SKILL.md must ask the user.

    ``candidates`` is the Tier 1 list because that is the user-meaningful
    set ("which in-progress issue is this for?"). The branch hint, when
    present but unhelpful, is intentionally not bubbled up here — it has
    already been considered during resolution.
    """

    candidates: tuple[int, ...]

    def __init__(self, candidates: list[int] | tuple[int, ...]) -> None:
        # Accept list or tuple input but freeze to tuple internally so the
        # dataclass remains immutable.
        object.__setattr__(self, "candidates", tuple(candidates))


def resolve_target_issue(
    *,
    branch: str,
    list_in_progress_fn: Callable[[], list[int]],
    branch_pattern: str = DEFAULT_BRANCH_PATTERN,
) -> Union[int, AmbiguousResolution]:
    """Resolve the target issue per the design state-transition table.

    Parameters
    ----------
    branch:
        Current git branch name. Empty string is treated as "no branch hint".
    list_in_progress_fn:
        Zero-arg callable returning the in-progress issue numbers (Tier 1).
        Injected so this module performs no I/O and is trivially testable.
    branch_pattern:
        Regex used by :func:`extract_issue_number`. Defaults to the
        plugin-wide :data:`DEFAULT_BRANCH_PATTERN` so callers do not need
        to know it.

    Returns
    -------
    int
        When the issue is uniquely determined.
    AmbiguousResolution
        When multiple candidates remain after combining Tier 1 and the
        branch hint, with no Tier 2 fallback to break the tie.

    Raises
    ------
    IssueResolutionError
        When neither tier produces any candidate.
    """
    tier1 = list(list_in_progress_fn() or [])
    branch_hint = extract_issue_number(branch, branch_pattern)

    # ---- Row 3: Tier 1 == 1 件 → 確定 ---------------------------------
    if len(tier1) == 1:
        return tier1[0]

    # ---- Rows 1 / 2: Tier 1 == 0 件 -----------------------------------
    if len(tier1) == 0:
        if branch_hint is not None:
            return branch_hint  # Row 2: Tier 2 fallback
        raise IssueResolutionError(
            "no in-progress issues and branch did not match the issue pattern"
        )

    # ---- Tier 1 ≥ 2 件 ------------------------------------------------
    # Row 4: branch hint intersects Tier 1 in exactly one place.
    if branch_hint is not None and branch_hint in tier1:
        return branch_hint

    # Row 5: branch hint exists but is not in Tier 1 → take Tier 2.
    if branch_hint is not None:
        return branch_hint

    # Rows 6 / 7: no usable Tier 2 → defer to user.
    return AmbiguousResolution(candidates=tier1)
