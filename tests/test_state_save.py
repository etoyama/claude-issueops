"""Tests for per-session state file save (PreCompact restore payload).

The hook fires on PreCompact, which is forbidden from injecting
``additionalContext`` directly. Instead we persist a snapshot of the
current issue context to a per-session state file; the next
``UserPromptSubmit`` reads that file and injects (D-2 pattern, see
project memory). These tests pin the on-disk schema so that ``#9``
(UserPromptSubmit hook) and ``#11`` (SessionEnd fallback) can rely on
the structure.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from issueops.state_save import (
    STATE_SCHEMA_VERSION,
    IssueSnapshot,
    build_pending_restore,
    save_pending_restore,
    state_file_path,
)


def _snapshot(**overrides) -> IssueSnapshot:
    base = dict(
        number=132,
        title="feature: session continuity",
        body_excerpt="We need to keep context across sessions.",
        decision_slugs=("h2-replacement", "sessionstart-removal"),
        parent_epic=7,
    )
    base.update(overrides)
    return IssueSnapshot(**base)


def test_state_file_path_uses_session_state_subdir(tmp_path: Path):
    p = state_file_path(tmp_path, "abc123")
    assert p == tmp_path / "session-state" / "abc123.json"


def test_state_file_path_rejects_session_id_with_path_separator(tmp_path: Path):
    # session_id is untrusted input from the hook payload; reject anything
    # that could escape the session-state/ directory.
    with pytest.raises(ValueError, match="session_id"):
        state_file_path(tmp_path, "../escape")
    with pytest.raises(ValueError, match="session_id"):
        state_file_path(tmp_path, "a/b")


def test_build_pending_restore_includes_all_snapshot_fields():
    fixed = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    payload = build_pending_restore(_snapshot(), now=fixed)

    assert payload["schema_version"] == STATE_SCHEMA_VERSION
    assert payload["issue_number"] == 132
    assert payload["title"] == "feature: session continuity"
    assert payload["body_excerpt"] == "We need to keep context across sessions."
    assert payload["decision_slugs"] == ["h2-replacement", "sessionstart-removal"]
    assert payload["parent_epic"] == 7
    assert payload["saved_at"] == "2026-04-25T12:00:00+00:00"


def test_build_pending_restore_handles_no_parent_epic():
    payload = build_pending_restore(_snapshot(parent_epic=None))
    assert payload["parent_epic"] is None


def test_build_pending_restore_emits_iso8601_utc_when_now_omitted():
    payload = build_pending_restore(_snapshot())
    # Round-trip through fromisoformat to verify it's a valid ISO-8601 string.
    parsed = datetime.fromisoformat(payload["saved_at"])
    assert parsed.tzinfo is not None


def test_save_pending_restore_creates_state_dir_and_writes_file(tmp_path: Path):
    fixed = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    target = save_pending_restore(
        project_dir=tmp_path,
        session_id="sess-1",
        snapshot=_snapshot(),
        now=fixed,
    )

    assert target == tmp_path / "session-state" / "sess-1.json"
    data = json.loads(target.read_text())
    assert data["session_id"] == "sess-1"
    assert data["pending_restore"]["issue_number"] == 132
    assert data["pending_restore"]["saved_at"] == "2026-04-25T12:00:00+00:00"


def test_save_pending_restore_preserves_unrelated_existing_fields(tmp_path: Path):
    # The session-closer skill (#8) and other hooks may write sibling
    # fields into the same file (e.g., briefing_done, last_summary).
    # PreCompact must not clobber them.
    state_dir = tmp_path / "session-state"
    state_dir.mkdir()
    state_path = state_dir / "sess-1.json"
    state_path.write_text(
        json.dumps(
            {
                "session_id": "sess-1",
                "briefing_done": True,
                "last_summary_at": "2026-04-24T09:00:00+00:00",
            }
        )
    )

    save_pending_restore(
        project_dir=tmp_path,
        session_id="sess-1",
        snapshot=_snapshot(),
    )

    data = json.loads(state_path.read_text())
    assert data["briefing_done"] is True
    assert data["last_summary_at"] == "2026-04-24T09:00:00+00:00"
    assert data["pending_restore"]["issue_number"] == 132


def test_save_pending_restore_overwrites_previous_pending_restore(tmp_path: Path):
    # A second PreCompact in the same session should reflect the latest
    # snapshot, not append.
    save_pending_restore(
        project_dir=tmp_path,
        session_id="sess-1",
        snapshot=_snapshot(number=100),
    )
    save_pending_restore(
        project_dir=tmp_path,
        session_id="sess-1",
        snapshot=_snapshot(number=200),
    )

    data = json.loads((tmp_path / "session-state" / "sess-1.json").read_text())
    assert data["pending_restore"]["issue_number"] == 200


def test_save_pending_restore_returns_none_payload_when_snapshot_is_none(
    tmp_path: Path,
):
    # When the hook can't resolve a current issue (e.g., on master), we
    # still want a deterministic outcome: no state file is created.
    target = save_pending_restore(
        project_dir=tmp_path,
        session_id="sess-1",
        snapshot=None,
    )
    assert target is None
    assert not (tmp_path / "session-state").exists()


def test_save_pending_restore_rejects_bad_session_id(tmp_path: Path):
    with pytest.raises(ValueError, match="session_id"):
        save_pending_restore(
            project_dir=tmp_path,
            session_id="../escape",
            snapshot=_snapshot(),
        )
