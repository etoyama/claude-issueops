"""Pure slug-based deduplication filters.

The session-closer skill defends against duplicate Decision posts in
two tiers (R-5):

- **Tier 1 (local)**: drop candidates whose slug appears in the
  per-session ``state.captured_slugs`` list. Cheap, no I/O.
- **Tier 2 (remote)**: drop candidates whose slug appears among the
  ``Decision[]`` already parsed out of the issue's existing comments.
  The orchestrator owns the ``gh issue view`` call and the
  ``marker_parser.parse_decisions`` step; this module only does the
  set-difference so it stays trivially testable.

Both functions are pure — they never mutate inputs and never touch I/O.
Empty inputs short-circuit to the input list unchanged (R-5 boundary).
"""

from __future__ import annotations

from issueops.decision_extractor import Candidate
from issueops.marker_parser import Decision


def filter_local(
    candidates: list[Candidate], *, captured_slugs: list[str]
) -> list[Candidate]:
    """Return candidates whose slug is **not** in ``captured_slugs`` (Tier 1).

    When ``captured_slugs`` is empty the input list is returned as-is —
    the orchestrator passes ``state.captured_slugs or []`` so this is
    the common first-run path.
    """
    if not captured_slugs:
        return list(candidates)

    seen: set[str] = set(captured_slugs)
    return [c for c in candidates if c.slug not in seen]


def filter_remote(
    candidates: list[Candidate], *, existing_decisions: list[Decision]
) -> list[Candidate]:
    """Return candidates whose slug is **not** in ``existing_decisions`` (Tier 2).

    ``existing_decisions`` is the output of
    ``marker_parser.parse_decisions(gh_issue_view_output)``; only the
    ``slug`` field matters for dedup. Empty input is a passthrough.
    """
    if not existing_decisions:
        return list(candidates)

    seen: set[str] = {d.slug for d in existing_decisions}
    return [c for c in candidates if c.slug not in seen]
