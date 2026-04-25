"""L1 unit tests for ``dedup_checker``.

Covers Test Design IDs:

- T-41: ``test_filter_local_excludes_captured`` (R-5.1)
- T-42: ``test_filter_remote_excludes_marker_parsed`` (R-5.2)
- T-43: ``test_filter_local_empty_captured_passthrough`` (R-5 boundary)

Pure functions only — gh I/O lives in the orchestrator. Both filters
return new lists; original input is never mutated.
"""

from __future__ import annotations

from typing import Literal

import pytest


def _make_candidate(slug: str, scope: Literal["issue", "cross-issue"] = "issue"):
    from issueops.decision_extractor import Candidate

    return Candidate(
        slug=slug,
        what=f"what-{slug}",
        why=f"why-{slug}",
        alternatives=f"alt-{slug}",
        consequences=f"cons-{slug}",
        scope_hint=scope,
    )


def _make_decision(slug: str):
    from issueops.marker_parser import Decision

    return Decision(
        slug=slug,
        what=f"what-{slug}",
        why=f"why-{slug}",
        alternatives=f"alt-{slug}",
        consequences=f"cons-{slug}",
    )


def test_filter_local_excludes_captured() -> None:
    """T-41: candidates whose slug appears in ``captured_slugs`` are dropped."""
    from issueops.dedup_checker import filter_local

    candidates = [
        _make_candidate("alpha"),
        _make_candidate("beta"),
        _make_candidate("gamma"),
        _make_candidate("delta"),
        _make_candidate("epsilon"),
    ]

    out = filter_local(candidates, captured_slugs=["beta", "delta"])

    assert [c.slug for c in out] == ["alpha", "gamma", "epsilon"]
    # original list unchanged
    assert [c.slug for c in candidates] == [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
    ]


def test_filter_remote_excludes_marker_parsed() -> None:
    """T-42: candidates whose slug equals an existing Decision's slug are dropped.

    Simulates Tier-2 path: ``existing_decisions`` arrives from
    ``marker_parser.parse_decisions`` over ``gh issue view`` output.
    """
    from issueops.dedup_checker import filter_remote

    candidates = [
        _make_candidate("alpha"),
        _make_candidate("beta"),
        _make_candidate("gamma"),
    ]
    existing = [_make_decision("beta"), _make_decision("zeta")]

    out = filter_remote(candidates, existing_decisions=existing)

    assert [c.slug for c in out] == ["alpha", "gamma"]


def test_filter_local_empty_captured_passthrough() -> None:
    """T-43: empty ``captured_slugs`` returns input unchanged.

    Boundary contract — orchestrator passes ``state.captured_slugs or []``
    on first run and we must not drop anything.
    """
    from issueops.dedup_checker import filter_local

    candidates = [_make_candidate("alpha"), _make_candidate("beta")]

    out = filter_local(candidates, captured_slugs=[])

    assert [c.slug for c in out] == ["alpha", "beta"]


def test_filter_remote_empty_existing_passthrough() -> None:
    """Boundary mirror of T-43 for the Tier-2 path: empty existing decisions
    means nothing is excluded."""
    from issueops.dedup_checker import filter_remote

    candidates = [_make_candidate("alpha"), _make_candidate("beta")]

    out = filter_remote(candidates, existing_decisions=[])

    assert [c.slug for c in out] == ["alpha", "beta"]


def test_filter_local_empty_candidates_returns_empty() -> None:
    """Boundary: empty candidates trivially returns empty (no surprises)."""
    from issueops.dedup_checker import filter_local

    assert filter_local([], captured_slugs=["alpha"]) == []


def test_filter_remote_empty_candidates_returns_empty() -> None:
    """Boundary: same for filter_remote."""
    from issueops.dedup_checker import filter_remote

    assert filter_remote([], existing_decisions=[_make_decision("alpha")]) == []
