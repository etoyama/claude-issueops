"""UserPromptSubmit hook: session briefing + compact restore (D-2).

A single firing covers exactly one of three modes:

- ``briefing`` — first prompt of a session. Inject Tier 1 (in-progress
  issues) + Tier 2 (current issue with decision markers). Mark
  ``briefing_done = True`` so we don't repeat.
- ``restore`` — first prompt after PreCompact. Inject the
  ``pending_restore`` snapshot back as additionalContext, then clear
  the field so subsequent prompts don't re-inject.
- ``none`` — nothing to do; the hook returns no additionalContext to
  keep per-prompt overhead at zero.

Briefing wins when ``briefing_done`` is False, even if PreCompact has
written ``pending_restore`` already (defensive — should not normally
happen in a fresh session).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from issueops.state_save import IssueSnapshot, state_file_path

InjectMode = Literal["briefing", "restore", "none"]

BRIEFING_MARKER = "claude-issueops:briefing"
RESTORE_MARKER = "claude-issueops:restore"


@dataclass(frozen=True)
class InjectDecision:
    """The single mode chosen for this firing."""

    mode: InjectMode


InProgressFn = Callable[[], list[dict]]
CurrentIssueFn = Callable[[], "IssueSnapshot | None"]


def decide_inject(state: dict | None) -> InjectDecision:
    """Pick the mode for this prompt. See module docstring for rules."""
    if not state or not state.get("briefing_done"):
        return InjectDecision(mode="briefing")
    if state.get("pending_restore"):
        return InjectDecision(mode="restore")
    return InjectDecision(mode="none")


def _format_in_progress(items: list[dict]) -> str:
    if not items:
        return "_(no open in-progress issues)_"
    return "\n".join(
        f"- #{it['number']} — {it.get('title', '')}" for it in items
    )


def _format_current_issue(snap: IssueSnapshot | None) -> str:
    if snap is None:
        return "_(no current issue resolvable from branch)_"
    parent = (
        f"\n- Parent epic: #{snap.parent_epic}" if snap.parent_epic else ""
    )
    decisions = (
        "\n- Decisions on file: " + ", ".join(snap.decision_slugs)
        if snap.decision_slugs
        else ""
    )
    return (
        f"**#{snap.number} — {snap.title}**{parent}{decisions}\n\n"
        f"{snap.body_excerpt}"
    )


def render_briefing(
    *,
    in_progress: list[dict],
    current_issue: IssueSnapshot | None,
) -> str:
    """Render Tier 1 + Tier 2 briefing as Markdown."""
    return (
        f"<!-- {BRIEFING_MARKER} -->\n"
        "## Session briefing\n"
        "\n"
        "### Tier 1 — In-progress issues\n"
        f"{_format_in_progress(in_progress)}\n"
        "\n"
        "### Tier 2 — Current issue\n"
        f"{_format_current_issue(current_issue)}\n"
    )


def render_restore(pending_restore: dict) -> str:
    """Render restore-after-compact context as Markdown."""
    number = pending_restore.get("issue_number")
    title = pending_restore.get("title", "")
    excerpt = pending_restore.get("body_excerpt", "")
    saved_at = pending_restore.get("saved_at", "")
    parent = pending_restore.get("parent_epic")
    decisions = pending_restore.get("decision_slugs") or []

    parent_line = f"\n- Parent epic: #{parent}" if parent else ""
    decisions_line = (
        "\n- Decisions on file: " + ", ".join(decisions) if decisions else ""
    )
    return (
        f"<!-- {RESTORE_MARKER} -->\n"
        "## Restored after compact\n"
        "\n"
        f"**#{number} — {title}**{parent_line}{decisions_line}\n"
        f"- Snapshot saved at: `{saved_at}`\n"
        "\n"
        f"{excerpt}\n"
    )


def _read_state(state_path: Path) -> dict | None:
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _write_state(state_path: Path, state: dict, session_id: str) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state["session_id"] = session_id
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _safe_call(fn: Callable, default):
    try:
        return fn()
    except Exception:
        return default


def run_user_prompt_submit(
    *,
    project_dir: Path,
    session_id: str,
    in_progress_fn: InProgressFn,
    current_issue_fn: CurrentIssueFn,
) -> str | None:
    """End-to-end UserPromptSubmit entrypoint.

    Returns the rendered ``additionalContext`` string (or ``None`` if
    the mode is ``none``). Updates the per-session state file as a
    side effect: sets ``briefing_done`` after briefing, clears
    ``pending_restore`` after restore. Other state fields are preserved.
    """
    state_path = state_file_path(project_dir, session_id)
    state = _read_state(state_path) or {}

    decision = decide_inject(state if state else None)

    if decision.mode == "none":
        return None

    if decision.mode == "briefing":
        in_progress = _safe_call(in_progress_fn, [])
        current_issue = _safe_call(current_issue_fn, None)
        body = render_briefing(in_progress=in_progress, current_issue=current_issue)
        state["briefing_done"] = True
        _write_state(state_path, state, session_id)
        return body

    # restore
    pending = state.get("pending_restore") or {}
    body = render_restore(pending)
    state.pop("pending_restore", None)
    _write_state(state_path, state, session_id)
    return body
