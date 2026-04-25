"""Orchestration for the PreCompact hook.

The bin entrypoint (``bin/precompact_hook.py``) is a thin adapter that
wires real git/gh subprocesses into :func:`run_precompact`. All
testable logic lives here behind injected callables, matching the
pattern used in :mod:`issueops.branch_resolver`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from issueops.branch_resolver import resolve_current_issue
from issueops.marker_parser import parse_decisions
from issueops.state_save import IssueSnapshot, save_pending_restore

BODY_EXCERPT_MAX_CHARS = 500

_PARENT_EPIC_RE = re.compile(r"^Parent:\s*#(\d+)\s*$", re.MULTILINE)


GhFetchFn = Callable[[int], dict]
LatestInProgressFn = Callable[[], "int | None"]


def _extract_parent_epic(body: str) -> int | None:
    m = _PARENT_EPIC_RE.search(body or "")
    return int(m.group(1)) if m else None


def _decision_slugs(body: str, comments: list[dict]) -> tuple[str, ...]:
    parts = [body or ""] + [c.get("body", "") for c in comments]
    slugs: list[str] = []
    for part in parts:
        for d in parse_decisions(part):
            slugs.append(d.slug)
    return tuple(slugs)


def snapshot_current_issue(
    *,
    branch: str,
    gh_fetch_fn: GhFetchFn,
    latest_in_progress_fn: LatestInProgressFn,
) -> IssueSnapshot | None:
    """Resolve the current issue from ``branch`` and project it to a snapshot.

    Returns ``None`` when no issue can be resolved (branch does not match
    the convention and the in-progress fallback also returns ``None``),
    so the caller can short-circuit without writing a state file.
    """
    number = resolve_current_issue(
        branch,
        fallback="latest-in-progress",
        fallback_fn=latest_in_progress_fn,
    )
    if number is None:
        return None

    payload = gh_fetch_fn(number)
    body = payload.get("body") or ""
    comments = payload.get("comments") or []

    return IssueSnapshot(
        number=number,
        title=payload.get("title") or "",
        body_excerpt=body[:BODY_EXCERPT_MAX_CHARS],
        decision_slugs=_decision_slugs(body, comments),
        parent_epic=_extract_parent_epic(body),
    )


def run_precompact(
    *,
    project_dir: Path,
    session_id: str,
    branch: str,
    gh_fetch_fn: GhFetchFn,
    latest_in_progress_fn: LatestInProgressFn,
    now: datetime | None = None,
) -> Path | None:
    """End-to-end PreCompact entrypoint, callable from the bin shell.

    Failures from ``gh_fetch_fn`` or ``latest_in_progress_fn`` are
    swallowed: PreCompact must never block compaction. The function
    returns ``None`` in that case and the state file is left as-is.
    """
    try:
        snapshot = snapshot_current_issue(
            branch=branch,
            gh_fetch_fn=gh_fetch_fn,
            latest_in_progress_fn=latest_in_progress_fn,
        )
    except Exception:
        return None

    return save_pending_restore(
        project_dir=project_dir,
        session_id=session_id,
        snapshot=snapshot,
        now=now,
    )
