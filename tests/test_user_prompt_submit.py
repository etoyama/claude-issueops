"""Tests for the UserPromptSubmit hook (briefing + restore, D-2 design).

This hook is the single entry point for both:
- session briefing (Tier 1 in-progress overview + Tier 2 current issue)
- compact-restore (re-injecting pending_restore written by PreCompact)

The two modes are mutually exclusive in any single firing. State
transitions are pinned by these tests so ``#11`` (SessionEnd) and
``#8`` (skill) can rely on the schema.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from issueops.state_save import IssueSnapshot
from issueops.user_prompt_submit import (
    BRIEFING_MARKER,
    RESTORE_MARKER,
    InjectDecision,
    decide_inject,
    render_briefing,
    render_restore,
    run_user_prompt_submit,
)


def _state(**overrides) -> dict:
    base: dict = {"session_id": "sess-1"}
    base.update(overrides)
    return base


def _pending_restore(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "issue_number": 10,
        "title": "PreCompact hook",
        "body_excerpt": "body",
        "decision_slugs": [],
        "parent_epic": 7,
        "saved_at": "2026-04-25T12:00:00+00:00",
    }
    base.update(overrides)
    return base


def _snapshot(**overrides) -> IssueSnapshot:
    base = dict(
        number=132,
        title="feature: session continuity",
        body_excerpt="Body here",
        decision_slugs=("h2-replacement",),
        parent_epic=7,
    )
    base.update(overrides)
    return IssueSnapshot(**base)


# --- decide_inject -----------------------------------------------------


def test_decide_inject_briefing_when_state_missing():
    assert decide_inject(None) == InjectDecision(mode="briefing")


def test_decide_inject_briefing_when_briefing_not_done():
    assert decide_inject(_state(briefing_done=False)) == InjectDecision(mode="briefing")


def test_decide_inject_restore_when_pending_restore_present_and_briefing_done():
    state = _state(briefing_done=True, pending_restore=_pending_restore())
    assert decide_inject(state) == InjectDecision(mode="restore")


def test_decide_inject_none_when_briefing_done_and_no_pending_restore():
    assert decide_inject(_state(briefing_done=True)) == InjectDecision(mode="none")


def test_decide_inject_briefing_wins_when_both_apply():
    # Defensive: if briefing somehow hasn't run but PreCompact already
    # wrote pending_restore, the fresh-session briefing takes priority.
    state = _state(briefing_done=False, pending_restore=_pending_restore())
    assert decide_inject(state) == InjectDecision(mode="briefing")


# --- render_briefing ---------------------------------------------------


def test_render_briefing_includes_tier1_and_tier2_sections():
    body = render_briefing(
        in_progress=[
            {"number": 9, "title": "UserPromptSubmit hook"},
            {"number": 8, "title": "session-closer skill"},
        ],
        current_issue=_snapshot(),
    )
    assert "Tier 1" in body
    assert "Tier 2" in body
    assert "#9" in body
    assert "#8" in body
    assert "UserPromptSubmit hook" in body


def test_render_briefing_marker_present():
    body = render_briefing(in_progress=[], current_issue=None)
    assert BRIEFING_MARKER in body


def test_render_briefing_handles_no_current_issue():
    # On master, current issue can't be resolved — Tier 2 should say so.
    body = render_briefing(
        in_progress=[{"number": 1, "title": "x"}], current_issue=None
    )
    assert "Tier 2" in body
    assert "#1" in body


def test_render_briefing_includes_decision_slugs():
    body = render_briefing(
        in_progress=[],
        current_issue=_snapshot(decision_slugs=("d-one", "d-two")),
    )
    assert "d-one" in body
    assert "d-two" in body


def test_render_briefing_handles_empty_in_progress():
    body = render_briefing(in_progress=[], current_issue=_snapshot())
    assert "Tier 1" in body
    assert "feature: session continuity" in body


# --- render_restore ----------------------------------------------------


def test_render_restore_includes_issue_number_and_title():
    body = render_restore(_pending_restore())
    assert "#10" in body
    assert "PreCompact hook" in body


def test_render_restore_marker_present():
    body = render_restore(_pending_restore())
    assert RESTORE_MARKER in body


def test_render_restore_includes_body_excerpt_and_decisions():
    body = render_restore(
        _pending_restore(body_excerpt="this is the body", decision_slugs=["x"])
    )
    assert "this is the body" in body
    assert "x" in body


# --- run_user_prompt_submit -------------------------------------------


def _write_state(tmp_path: Path, payload: dict, session_id: str = "sess-1") -> Path:
    d = tmp_path / "session-state"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session_id}.json"
    p.write_text(json.dumps(payload))
    return p


def test_run_briefing_marks_briefing_done(tmp_path: Path):
    out = run_user_prompt_submit(
        project_dir=tmp_path,
        session_id="sess-1",
        in_progress_fn=lambda: [{"number": 9, "title": "x"}],
        current_issue_fn=lambda: _snapshot(),
    )
    assert out is not None
    assert BRIEFING_MARKER in out

    state = json.loads((tmp_path / "session-state" / "sess-1.json").read_text())
    assert state["briefing_done"] is True


def test_run_restore_clears_pending_restore(tmp_path: Path):
    _write_state(
        tmp_path,
        _state(briefing_done=True, pending_restore=_pending_restore()),
    )
    out = run_user_prompt_submit(
        project_dir=tmp_path,
        session_id="sess-1",
        in_progress_fn=lambda: pytest.fail("must not call gh on restore"),
        current_issue_fn=lambda: pytest.fail("must not call gh on restore"),
    )
    assert out is not None
    assert RESTORE_MARKER in out

    state = json.loads((tmp_path / "session-state" / "sess-1.json").read_text())
    assert "pending_restore" not in state
    assert state["briefing_done"] is True


def test_run_returns_none_when_nothing_to_inject(tmp_path: Path):
    _write_state(tmp_path, _state(briefing_done=True))
    out = run_user_prompt_submit(
        project_dir=tmp_path,
        session_id="sess-1",
        in_progress_fn=lambda: pytest.fail("must not call gh"),
        current_issue_fn=lambda: pytest.fail("must not call gh"),
    )
    assert out is None


def test_run_swallows_gh_failures_during_briefing(tmp_path: Path):
    def failing() -> list:
        raise RuntimeError("gh down")

    out = run_user_prompt_submit(
        project_dir=tmp_path,
        session_id="sess-1",
        in_progress_fn=failing,
        current_issue_fn=lambda: None,
    )
    # Briefing still fires with whatever data we could get; an empty
    # briefing is better than blocking the prompt.
    assert out is not None
    assert BRIEFING_MARKER in out

    # And briefing_done is set so we don't retry next prompt.
    state = json.loads((tmp_path / "session-state" / "sess-1.json").read_text())
    assert state["briefing_done"] is True


def test_run_briefing_preserves_other_state_fields(tmp_path: Path):
    _write_state(tmp_path, _state(skill_ran_at="2026-04-25T13:00:00+00:00"))
    run_user_prompt_submit(
        project_dir=tmp_path,
        session_id="sess-1",
        in_progress_fn=lambda: [],
        current_issue_fn=lambda: None,
    )
    state = json.loads((tmp_path / "session-state" / "sess-1.json").read_text())
    assert state["skill_ran_at"] == "2026-04-25T13:00:00+00:00"
    assert state["briefing_done"] is True
