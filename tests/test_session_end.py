"""Tests for the SessionEnd fallback hook.

When the session-closer skill (``#8``) does not run before a session
ends, this hook posts a minimal fallback summary so the issue still
has a record of the session. The skill, when it does run, writes
``skill_ran_at`` into the per-session state file; this hook honors
that flag and stays silent to avoid duplicate posts (AC5b).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from issueops.session_end import (
    render_summary,
    run_session_end,
    should_post_summary,
)


def _state(**overrides) -> dict:
    base: dict = {
        "session_id": "sess-1",
        "pending_restore": {
            "schema_version": 1,
            "issue_number": 10,
            "title": "feature: PreCompact hook (state save)",
            "body_excerpt": "...",
            "decision_slugs": [],
            "parent_epic": 7,
            "saved_at": "2026-04-25T12:00:00+00:00",
        },
    }
    base.update(overrides)
    return base


def _write_state(tmp_path: Path, payload: dict, session_id: str = "sess-1") -> Path:
    d = tmp_path / "session-state"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session_id}.json"
    p.write_text(json.dumps(payload))
    return p


def test_should_post_summary_false_when_state_is_none():
    assert should_post_summary(None) is False


def test_should_post_summary_false_when_skill_already_ran():
    state = _state(skill_ran_at="2026-04-25T13:00:00+00:00")
    assert should_post_summary(state) is False


def test_should_post_summary_true_when_pending_restore_present_and_no_skill():
    assert should_post_summary(_state()) is True


def test_should_post_summary_false_when_no_pending_restore():
    # Nothing to summarize — skill never ran AND PreCompact never fired.
    state = {"session_id": "sess-1"}
    assert should_post_summary(state) is False


def test_render_summary_includes_issue_title_and_number():
    fixed = datetime(2026, 4, 25, 14, 0, 0, tzinfo=timezone.utc)
    body = render_summary(_state(), ended_at=fixed)
    assert "#10" in body
    assert "feature: PreCompact hook (state save)" in body


def test_render_summary_includes_ended_at_iso():
    fixed = datetime(2026, 4, 25, 14, 0, 0, tzinfo=timezone.utc)
    body = render_summary(_state(), ended_at=fixed)
    assert "2026-04-25T14:00:00+00:00" in body


def test_render_summary_is_marked_as_fallback():
    # Users (and the skill) need to distinguish a fallback summary from
    # a full skill-driven close. Pin a stable marker token.
    body = render_summary(_state(), ended_at=datetime.now(timezone.utc))
    assert "session-end-fallback" in body


def test_run_session_end_skips_when_state_file_missing(tmp_path: Path):
    posted: list = []
    result = run_session_end(
        project_dir=tmp_path,
        session_id="missing-session",
        post_comment_fn=lambda n, b: posted.append((n, b)),
    )
    assert result is None
    assert posted == []


def test_run_session_end_skips_when_skill_ran(tmp_path: Path):
    _write_state(tmp_path, _state(skill_ran_at="2026-04-25T13:00:00+00:00"))
    posted: list = []
    result = run_session_end(
        project_dir=tmp_path,
        session_id="sess-1",
        post_comment_fn=lambda n, b: posted.append((n, b)),
    )
    assert result is None
    assert posted == []


def test_run_session_end_posts_summary_when_needed(tmp_path: Path):
    _write_state(tmp_path, _state())
    posted: list = []
    fixed = datetime(2026, 4, 25, 14, 0, 0, tzinfo=timezone.utc)
    result = run_session_end(
        project_dir=tmp_path,
        session_id="sess-1",
        post_comment_fn=lambda n, b: posted.append((n, b)),
        ended_at=fixed,
    )
    assert result == 10
    assert len(posted) == 1
    issue_number, body = posted[0]
    assert issue_number == 10
    assert "session-end-fallback" in body


def test_run_session_end_swallows_post_failures(tmp_path: Path):
    _write_state(tmp_path, _state())

    def failing_post(n: int, b: str) -> None:
        raise RuntimeError("network down")

    result = run_session_end(
        project_dir=tmp_path,
        session_id="sess-1",
        post_comment_fn=failing_post,
    )
    assert result is None


def test_run_session_end_handles_corrupt_state_file(tmp_path: Path):
    d = tmp_path / "session-state"
    d.mkdir()
    (d / "sess-1.json").write_text("{ this is not json")
    posted: list = []
    result = run_session_end(
        project_dir=tmp_path,
        session_id="sess-1",
        post_comment_fn=lambda n, b: posted.append((n, b)),
    )
    assert result is None
    assert posted == []
