"""SessionEnd hook: fallback summary when the skill did not run.

When the session-closer skill (``#8``) is invoked during a session it
must write ``skill_ran_at`` into the per-session state file. This hook
checks that flag: present means the skill handled the close in full,
so we stay silent (AC5b — no duplicate posts). Absent means we post a
minimal fallback summary so the issue still records the session.

The summary contains a stable ``session-end-fallback`` marker token so
that downstream tooling (and humans) can distinguish a fallback comment
from a full skill-driven close.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from issueops.state_save import state_file_path

PostCommentFn = Callable[[int, str], None]

FALLBACK_MARKER = "session-end-fallback"


def should_post_summary(state: dict | None) -> bool:
    """True iff a fallback summary should be posted for this session.

    - ``None`` or missing state → False (nothing to summarize).
    - ``skill_ran_at`` present → False (skill already closed the session).
    - ``pending_restore`` present and skill flag absent → True.
    - Otherwise → False.
    """
    if not state:
        return False
    if state.get("skill_ran_at"):
        return False
    return bool(state.get("pending_restore"))


def render_summary(state: dict, *, ended_at: datetime) -> str:
    """Render the fallback comment body in Markdown."""
    pr = state.get("pending_restore") or {}
    issue_number = pr.get("issue_number")
    title = pr.get("title", "")
    saved_at = pr.get("saved_at", "")
    return (
        f"<!-- claude-issueops:{FALLBACK_MARKER} -->\n"
        "## Session ended (fallback summary)\n"
        "\n"
        f"- Ended at: `{ended_at.isoformat()}`\n"
        f"- Last known current issue: #{issue_number} — {title}\n"
        f"- Last snapshot saved at: `{saved_at}`\n"
        "\n"
        "_Posted by `claude-issueops` SessionEnd fallback because the "
        "session-closer skill did not run in this session. Decision "
        "extraction was skipped (requires interactive confirmation)._\n"
    )


def _read_state(state_path: Path) -> dict | None:
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def run_session_end(
    *,
    project_dir: Path,
    session_id: str,
    post_comment_fn: PostCommentFn,
    ended_at: datetime | None = None,
) -> int | None:
    """Read state, decide whether to post, and post if so.

    Returns the issue number we posted to, or ``None`` when we skipped
    (state missing, skill already ran, no pending_restore, or post
    failure). Errors from ``post_comment_fn`` are swallowed — SessionEnd
    is best-effort.
    """
    state = _read_state(state_file_path(project_dir, session_id))
    if not should_post_summary(state):
        return None
    assert state is not None  # narrowed by should_post_summary

    issue_number = state["pending_restore"]["issue_number"]
    body = render_summary(state, ended_at=ended_at or datetime.now(timezone.utc))
    try:
        post_comment_fn(issue_number, body)
    except Exception:
        return None
    return issue_number
