"""L1 unit tests for ``decision_extractor``.

Covers Test Design IDs:

- T-11: ``test_parse_candidates_valid`` (R-3.2)
- T-12: ``test_parse_candidates_invalid_dropped`` (R-3.3)
- T-13: ``test_candidate_to_decision_strips_scope`` (R-3.5 type integrity)
- T-14: ``test_parse_candidates_json_decode_error`` (R-3.2 parse error path)

The module turns LLM-extracted JSON into the ``Candidate`` dataclass and
provides the ``Candidate → Decision`` converter so the rest of the
pipeline (memory escalation, comment body rendering) can keep using the
frozen ``marker_parser.Decision`` type.
"""

from __future__ import annotations

import json

import pytest


def _good_candidate(slug: str = "lazy-load-feature", scope: str = "issue") -> dict:
    return {
        "slug": slug,
        "what": "Adopt lazy loading for the feature module",
        "why": "Initial bundle size dropped 30%",
        "alternatives": "Eager load (rejected: too slow)",
        "consequences": "Slight delay on first interaction; cached afterwards",
        "scope_hint": scope,
    }


def test_parse_candidates_valid() -> None:
    """T-11: well-formed JSON list yields a ``list[Candidate]`` mirroring inputs."""
    from issueops.decision_extractor import Candidate, parse_candidates_json

    payload = json.dumps(
        [
            _good_candidate("lazy-load-feature", "issue"),
            _good_candidate("centralize-config", "cross-issue"),
        ]
    )

    candidates = parse_candidates_json(payload)

    assert len(candidates) == 2
    assert all(isinstance(c, Candidate) for c in candidates)
    assert candidates[0].slug == "lazy-load-feature"
    assert candidates[0].scope_hint == "issue"
    assert candidates[1].slug == "centralize-config"
    assert candidates[1].scope_hint == "cross-issue"
    # frozen dataclass — must reject mutation
    with pytest.raises(Exception):
        candidates[0].slug = "mutated"  # type: ignore[misc]


def test_parse_candidates_invalid_dropped() -> None:
    """T-12: invalid candidates are silently dropped; valid ones survive.

    Drops are: (1) non-kebab-case slug, (2) empty required field, (3)
    bad ``scope_hint`` literal, (4) missing key. 4 inputs → 1 valid.
    """
    from issueops.decision_extractor import parse_candidates_json

    bad_slug = _good_candidate("UpperCase_Bad", "issue")
    empty_field = _good_candidate("ok-slug", "issue")
    empty_field["why"] = ""
    bad_scope = _good_candidate("ok-slug-2", "team")
    missing_key = _good_candidate("ok-slug-3", "issue")
    del missing_key["consequences"]
    valid = _good_candidate("good-one", "cross-issue")

    payload = json.dumps([bad_slug, empty_field, bad_scope, missing_key, valid])

    candidates = parse_candidates_json(payload)

    assert [c.slug for c in candidates] == ["good-one"]
    assert candidates[0].scope_hint == "cross-issue"


def test_candidate_to_decision_strips_scope() -> None:
    """T-13: ``candidate_to_decision`` returns a ``marker_parser.Decision``
    that has the canonical 5 fields (no scope leakage)."""
    from issueops.decision_extractor import Candidate, candidate_to_decision
    from issueops.marker_parser import Decision

    candidate = Candidate(
        slug="lazy-load-feature",
        what="Adopt lazy loading",
        why="Bundle size",
        alternatives="Eager load",
        consequences="Slight first-load delay",
        scope_hint="cross-issue",
    )

    decision = candidate_to_decision(candidate)

    assert isinstance(decision, Decision)
    assert decision.slug == candidate.slug
    assert decision.what == candidate.what
    assert decision.why == candidate.why
    assert decision.alternatives == candidate.alternatives
    assert decision.consequences == candidate.consequences
    # Decision must NOT carry scope (frozen marker spec)
    assert not hasattr(decision, "scope_hint")
    assert not hasattr(decision, "final_scope")


def test_parse_candidates_json_decode_error() -> None:
    """T-14: malformed JSON propagates ``ValueError`` to the orchestrator
    so it can ``abort`` cleanly without state mutation (R-3.2 error path).
    """
    from issueops.decision_extractor import parse_candidates_json

    with pytest.raises(ValueError):
        parse_candidates_json("{not valid json")


def test_user_decision_dataclass_exists() -> None:
    """Sanity: ``UserDecision`` and ``PostedDecision`` are frozen dataclasses
    with the fields the orchestrator and SKILL.md will populate. Not a
    Test Design ID but cheap to assert here so downstream tests have a
    stable contract."""
    from issueops.decision_extractor import (
        Candidate,
        PostedDecision,
        UserDecision,
    )

    candidate = Candidate(
        slug="x",
        what="w",
        why="y",
        alternatives="a",
        consequences="c",
        scope_hint="issue",
    )
    user_decision = UserDecision(candidate=candidate, final_scope="cross-issue")
    posted = PostedDecision(user_decision=user_decision, comment_url=None)

    # frozen-ness
    with pytest.raises(Exception):
        user_decision.final_scope = "issue"  # type: ignore[misc]
    with pytest.raises(Exception):
        posted.comment_url = "x"  # type: ignore[misc]

    assert user_decision.candidate is candidate
    assert posted.user_decision is user_decision
