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

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Union

from issueops.branch_resolver import (
    DEFAULT_BRANCH_PATTERN,
    extract_issue_number,
)

__all__ = [
    "AmbiguousResolution",
    "IssueResolutionError",
    "TargetResolutionError",
    "TargetSpec",
    "parse_target_spec",
    "resolve_meta_target",
    "resolve_target_issue",
    "DEFAULT_BRANCH_PATTERN",
]

TargetKind = Literal["meta", "issue", "story", "epic"]


class IssueResolutionError(Exception):
    """Raised when neither Tier 1 nor Tier 2 yields any candidate.

    Maps to design state-table row 1 (Tier 1 = 0 件, branch_resolver = None).
    """


class TargetResolutionError(Exception):
    """Raised when ``--target`` cannot be resolved to a concrete issue.

    Currently fires when ``--target meta`` finds zero Meta Issues
    (F1 strict-error design decision — see Epic 01 Living Design Doc).
    The bin handler converts this to the ``target-resolution`` error kind.
    """


@dataclass(frozen=True)
class TargetSpec:
    """User-supplied ``--target`` argument, normalised.

    ``kind`` covers the full future-reserved set even though
    :func:`parse_target_spec` currently only accepts ``meta`` and ``issue``;
    keeping the Literal wide means adding ``story`` / ``epic`` support later
    is a parser-only change, not a Value Object signature break.

    The ``(kind, value)`` invariant is enforced in ``__post_init__`` so
    construction outside :func:`parse_target_spec` (e.g. direct calls
    from tests or future bin code) cannot land in an inconsistent state.
    """

    kind: TargetKind
    value: int | None  # ``meta`` has no value; ``issue:N`` carries N.

    def __post_init__(self) -> None:
        if self.kind == "meta":
            if self.value is not None:
                raise ValueError("meta target must not carry a value")
        else:
            if self.value is None or self.value <= 0:
                raise ValueError(
                    f"{self.kind!r} target requires a positive int value"
                )


# Positive-integer match is enforced by the regex itself (`[1-9][0-9]*`)
# so callers do not need to defend against `kind:0` separately.
_TARGET_KV_RE = re.compile(r"^(issue|story|epic):([1-9][0-9]*)$")


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


def parse_target_spec(raw: str) -> TargetSpec:
    """Parse ``--target`` raw string into a :class:`TargetSpec`.

    Accepted forms:

    - ``"meta"`` → ``TargetSpec(kind="meta", value=None)``
    - ``"issue:<positive int>"`` → ``TargetSpec(kind="issue", value=N)``

    ``story:N`` / ``epic:N`` are syntactically recognised but reserved for
    a future Epic — they raise :class:`ValueError` here. SKILL.md should
    validate user input up-front; the bin layer falls back to converting
    any ``ValueError`` from this function into the ``invalid-target-spec``
    error kind.
    """
    if raw == "meta":
        return TargetSpec(kind="meta", value=None)

    match = _TARGET_KV_RE.match(raw)
    if match is None:
        raise ValueError(
            f"invalid target spec: {raw!r} (expected 'meta' or 'issue:<int>')"
        )

    kind, value_str = match.group(1), match.group(2)
    if kind != "issue":
        # ``story`` / ``epic`` are reserved for a future Epic. The dataclass
        # Literal accepts them; the parser does not — see TargetSpec docstring.
        raise ValueError(
            f"target kind {kind!r} is reserved for a future release; "
            "use 'issue:N' to address it explicitly"
        )
    return TargetSpec(kind="issue", value=int(value_str))


def resolve_meta_target(
    *,
    list_meta_fn: Callable[[], list[int]],
) -> Union[int, AmbiguousResolution]:
    """Resolve ``--target meta`` to a single Meta Issue number.

    Parameters
    ----------
    list_meta_fn:
        Zero-arg callable returning open Meta Issue numbers in the current
        Milestone. Injected so this module stays subprocess-free.

    Returns
    -------
    int
        When exactly one Meta Issue is found.
    AmbiguousResolution
        When multiple Meta Issues are open; the caller (SKILL.md) asks the
        user to disambiguate via AskUserQuestion.

    Raises
    ------
    TargetResolutionError
        When the list is empty. ``--target meta`` is an explicit user
        intent, so silently falling back is hostile — we surface the error
        and let the SKILL.md layer present guidance (F1 strict-error).
    """
    candidates = list(list_meta_fn())
    if not candidates:
        raise TargetResolutionError(
            "no open Meta issues found in the current Milestone"
        )
    if len(candidates) == 1:
        return candidates[0]
    return AmbiguousResolution(candidates=candidates)
