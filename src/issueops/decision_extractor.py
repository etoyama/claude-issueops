"""Parse LLM-extracted Decision candidates and convert to ``Decision``.

The session-closer skill's LLM step emits a JSON list of ``Candidate``
objects. This module is the *strict* parser between that untrusted JSON
and the typed Python world: anything that doesn't match the contract
(slug shape, scope literals, missing fields) is silently dropped so the
orchestrator never has to filter again.

The frozen ``marker_parser.Decision`` is reused as the canonical
on-issue type — this module only adds the extra fields the orchestrator
needs (``scope_hint``, ``final_scope``, ``comment_url``).

Test Design refs: T-11〜T-14, with extra sanity check on the frozen
dataclasses.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, get_args

from issueops.marker_parser import DECISION_MARKER_PREFIX, Decision

# Reuse marker_parser's slug shape exactly. Keeping the regex local (not
# importing the private compiled regex from marker_parser) so this file
# stays a stand-alone parser.
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")

ScopeLiteral = Literal["issue", "cross-issue"]
_ALLOWED_SCOPES: frozenset[str] = frozenset(get_args(ScopeLiteral))

_REQUIRED_STR_FIELDS: tuple[str, ...] = (
    "slug",
    "what",
    "why",
    "alternatives",
    "consequences",
)


@dataclass(frozen=True)
class Candidate:
    """LLM-proposed decision *before* user confirmation.

    ``scope_hint`` is the LLM's guess at scope; the user's final answer
    is captured separately in ``UserDecision.final_scope`` (R-3.5).
    """

    slug: str
    what: str
    why: str
    alternatives: str
    consequences: str
    scope_hint: ScopeLiteral


@dataclass(frozen=True)
class UserDecision:
    """Candidate + the user's confirmed scope, ready for posting."""

    candidate: Candidate
    final_scope: ScopeLiteral


@dataclass(frozen=True)
class PostedDecision:
    """Result of an attempted gh comment post.

    ``comment_url`` is ``None`` when the post failed and the decision was
    saved to the local pending file instead.
    """

    user_decision: UserDecision
    comment_url: str | None


def parse_candidates_json(text: str) -> list[Candidate]:
    """Decode and validate LLM-emitted JSON into ``list[Candidate]``.

    Drops candidates that fail any of:
        - missing required key
        - non-string required field
        - empty required field after strip
        - slug not matching ``^[a-z0-9-]+$``
        - ``scope_hint`` not one of the ``Literal`` values

    Raises:
        ValueError: When ``text`` is not valid JSON or not a JSON array.
            Orchestrator catches this and aborts (R-3.2 error path).
    """
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"candidates JSON decode failed: {exc}") from exc

    if not isinstance(raw, list):
        raise ValueError(
            f"candidates JSON must be a list, got {type(raw).__name__}"
        )

    out: list[Candidate] = []
    for item in raw:
        candidate = _coerce_candidate(item)
        if candidate is not None:
            out.append(candidate)
    return out


def candidate_to_decision(candidate: Candidate) -> Decision:
    """Convert ``Candidate`` to the frozen ``marker_parser.Decision``.

    Decision is the canonical on-issue marker type and intentionally
    *does not* carry scope (the marker text format itself has no scope
    field). Scope is decided per-decision separately and influences only
    memory escalation downstream.
    """
    return Decision(
        slug=candidate.slug,
        what=candidate.what,
        why=candidate.why,
        alternatives=candidate.alternatives,
        consequences=candidate.consequences,
    )


def render_decision_body(user_decision: UserDecision) -> str:
    """Render the gh-issue-comment body for a UserDecision.

    Single source of truth for the marker text — both the orchestrator
    (``session_closer._post_decisions``) and the bin adapter use this
    so a copy-edit in one place cannot drift the marker the next dedup
    pass will see (#30, M-1).
    """
    decision = candidate_to_decision(user_decision.candidate)
    return (
        f"{DECISION_MARKER_PREFIX}{decision.slug} -->\n"
        f"## Decision: {decision.slug}\n"
        "\n"
        f"**What:** {decision.what}\n"
        "\n"
        f"**Why:** {decision.why}\n"
        "\n"
        f"**Alternatives considered:** {decision.alternatives}\n"
        "\n"
        f"**Consequences:** {decision.consequences}\n"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _coerce_candidate(item: Any) -> Candidate | None:
    """Validate one dict and return a ``Candidate`` or ``None`` to drop."""
    if not isinstance(item, dict):
        return None

    # All required string fields must exist, be strings, and be non-empty
    # after stripping. Empty alternatives/consequences are common LLM
    # failure modes and would silently corrupt downstream Decision text.
    values: dict[str, str] = {}
    for key in _REQUIRED_STR_FIELDS:
        val = item.get(key)
        if not isinstance(val, str):
            return None
        if not val.strip():
            return None
        values[key] = val

    if not _SLUG_RE.match(values["slug"]):
        return None

    scope_hint = item.get("scope_hint")
    if scope_hint not in _ALLOWED_SCOPES:
        return None

    return Candidate(
        slug=values["slug"],
        what=values["what"],
        why=values["why"],
        alternatives=values["alternatives"],
        consequences=values["consequences"],
        scope_hint=scope_hint,  # type: ignore[arg-type]
    )
