"""Persist a per-session snapshot for restore-after-compact.

The PreCompact hook event cannot inject ``additionalContext``; instead
it writes a snapshot of the current GitHub issue context here, and the
next ``UserPromptSubmit`` reads it back (D-2 pattern, see project memory
``project_hook_constraints``).

This module is pure: callers inject the snapshot and the project dir.
The bin entrypoint is responsible for resolving the current issue and
fetching its data via ``gh``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IssueSnapshot:
    """Minimal projection of a GitHub issue, captured at PreCompact time."""

    number: int
    title: str
    body_excerpt: str
    decision_slugs: tuple[str, ...]
    parent_epic: int | None


def _validate_session_id(session_id: str) -> None:
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        raise ValueError(f"unsafe session_id: {session_id!r}")
    if not session_id:
        raise ValueError("session_id must not be empty")


def state_file_path(project_dir: Path, session_id: str) -> Path:
    """Return the canonical state-file path for ``session_id``.

    Refuses session IDs that contain path separators or ``..`` so a
    malicious or malformed value cannot escape ``session-state/``.
    """
    _validate_session_id(session_id)
    return project_dir / "session-state" / f"{session_id}.json"


def build_pending_restore(
    snapshot: IssueSnapshot,
    *,
    now: datetime | None = None,
) -> dict:
    """Render an :class:`IssueSnapshot` as the ``pending_restore`` payload."""
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "issue_number": snapshot.number,
        "title": snapshot.title,
        "body_excerpt": snapshot.body_excerpt,
        "decision_slugs": list(snapshot.decision_slugs),
        "parent_epic": snapshot.parent_epic,
        "saved_at": timestamp,
    }


def save_pending_restore(
    *,
    project_dir: Path,
    session_id: str,
    snapshot: IssueSnapshot | None,
    now: datetime | None = None,
) -> Path | None:
    """Merge a ``pending_restore`` field into the per-session state file.

    Existing sibling fields (e.g., ``briefing_done`` written by the
    UserPromptSubmit hook, ``last_summary_at`` written by SessionEnd)
    are preserved. A repeated PreCompact within the same session
    overwrites the previous ``pending_restore`` so the next
    UserPromptSubmit restores the latest context.

    When ``snapshot`` is ``None`` (no current issue resolvable, e.g.,
    on master), this is a no-op and returns ``None`` — we deliberately
    avoid creating an empty state file so SessionEnd's "skill ran in
    this session?" check is not muddied.
    """
    target = state_file_path(project_dir, session_id)
    if snapshot is None:
        return None

    target.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text())
        except json.JSONDecodeError:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}

    existing["session_id"] = session_id
    existing["pending_restore"] = build_pending_restore(snapshot, now=now)
    target.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
    return target
