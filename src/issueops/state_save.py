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

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Re-export path helpers from ``path_utils`` so existing imports such as
# ``from issueops.state_save import state_file_path`` keep working while
# the new ``state_writer`` module shares the same low-level primitives
# (avoids an import cycle: state_save -> state_writer -> state_save).
from issueops.path_utils import _validate_session_id, state_file_path
from issueops.state_writer import merge_update_state

__all__ = [
    "STATE_SCHEMA_VERSION",
    "IssueSnapshot",
    "_validate_session_id",
    "state_file_path",
    "build_pending_restore",
    "save_pending_restore",
]

STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IssueSnapshot:
    """Minimal projection of a GitHub issue, captured at PreCompact time."""

    number: int
    title: str
    body_excerpt: str
    decision_slugs: tuple[str, ...]
    parent_epic: int | None


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
    # Validate session_id eagerly even when snapshot is None so callers
    # always see a clear error for unsafe IDs (existing test contract).
    state_file_path(project_dir, session_id)
    if snapshot is None:
        return None

    return merge_update_state(
        project_dir=project_dir,
        session_id=session_id,
        patch={"pending_restore": build_pending_restore(snapshot, now=now)},
        now=now,
    )
