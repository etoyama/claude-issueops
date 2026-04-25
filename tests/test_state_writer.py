"""Tests for ``issueops.state_writer`` — atomic state-file merge writer.

This is the single window for every state-file write across the
PreCompact / UserPromptSubmit / SessionEnd / session-closer hooks. The
writer must:
- Read existing JSON (or quarantine corrupt) → merge ``patch`` → atomic
  ``os.replace`` of a same-directory tmp file
- Use a tmp filename that cannot collide between concurrent processes
  or re-entrant calls (``<file>.tmp.<pid>.<monotonic_ns>.<uuid8>``)
- Preserve sibling fields written by other hooks
- Handle corrupt JSON by quarantining to ``*.corrupt-<ISO8601 microsec>``

Test IDs (per Test Design § Level 1):
- T-61: ``last_processed_offset`` update goes through ``os.replace``
- T-62: ``skill_ran_at`` is written in ISO-8601 UTC
- T-63: existing sibling fields (briefing_done / pending_restore) preserved
- T-64: unsafe session_id raises ``ValueError`` (delegates to path_utils)
- T-65: missing state file is created with only the merged fields
- T-66: corrupt JSON is quarantined to ``*.corrupt-<ISO8601 microsec>``
- T-67: tmp filename includes pid + monotonic_ns + uuid (collision-proof)
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest

from issueops.state_writer import merge_update_state


def test_state_writer_offset_atomic(project_dir: Path):
    """T-61: writing ``last_processed_offset`` uses ``os.replace``.

    Patches ``os.replace`` to record the call and verifies the tmp file
    is moved into place atomically (i.e., we never call ``os.rename``
    or ``Path.write_text`` directly on the target).
    """
    calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        real_replace(src, dst)

    with mock_patch("issueops.state_writer.os.replace", side_effect=spy_replace):
        target = merge_update_state(
            project_dir=project_dir,
            session_id="sess-61",
            patch={"last_processed_offset": 12345},
        )

    assert target.exists()
    data = json.loads(target.read_text())
    assert data["last_processed_offset"] == 12345
    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == str(target)
    # tmp file is in the same directory as the target (cross-fs rename safety)
    assert Path(src).parent == target.parent


def test_state_writer_skill_ran_at_isoformat(project_dir: Path, freeze_now):
    """T-62: ``skill_ran_at`` is stored as ISO-8601 UTC string."""
    fixed = freeze_now()
    iso = fixed.isoformat()

    target = merge_update_state(
        project_dir=project_dir,
        session_id="sess-62",
        patch={"skill_ran_at": iso},
        now=fixed,
    )

    data = json.loads(target.read_text())
    parsed = datetime.fromisoformat(data["skill_ran_at"])
    assert parsed.tzinfo is not None
    assert data["skill_ran_at"] == iso


def test_state_writer_preserves_siblings(project_dir: Path):
    """T-63: pre-existing sibling fields are kept (not blown away)."""
    target = project_dir / "session-state" / "sess-63.json"
    target.write_text(
        json.dumps(
            {
                "session_id": "sess-63",
                "briefing_done": True,
                "pending_restore": {"issue_number": 99},
                "last_summary_at": "2026-04-24T09:00:00+00:00",
            }
        )
    )

    merge_update_state(
        project_dir=project_dir,
        session_id="sess-63",
        patch={"skill_ran_at": "2026-04-25T13:00:00+00:00"},
    )

    data = json.loads(target.read_text())
    assert data["briefing_done"] is True
    assert data["pending_restore"] == {"issue_number": 99}
    assert data["last_summary_at"] == "2026-04-24T09:00:00+00:00"
    assert data["skill_ran_at"] == "2026-04-25T13:00:00+00:00"
    assert data["session_id"] == "sess-63"


def test_state_writer_invalid_session_id_raises(project_dir: Path):
    """T-64: unsafe session_id raises ValueError (delegates to path_utils)."""
    with pytest.raises(ValueError, match="session_id"):
        merge_update_state(
            project_dir=project_dir,
            session_id="../escape",
            patch={"skill_ran_at": "x"},
        )
    with pytest.raises(ValueError, match="session_id"):
        merge_update_state(
            project_dir=project_dir,
            session_id="",
            patch={"skill_ran_at": "x"},
        )


def test_state_writer_creates_minimal(tmp_path: Path):
    """T-65: when the state file does not exist, only patched fields land.

    The writer must not invent default values for sibling fields owned
    by other hooks; absent file → file containing exactly session_id +
    the patch entries.
    """
    target = merge_update_state(
        project_dir=tmp_path,
        session_id="sess-65",
        patch={"skill_ran_at": "2026-04-25T13:00:00+00:00"},
    )

    assert target.exists()
    data = json.loads(target.read_text())
    assert data == {
        "session_id": "sess-65",
        "skill_ran_at": "2026-04-25T13:00:00+00:00",
    }


def test_state_writer_quarantines_corrupt_json(project_dir: Path, freeze_now):
    """T-66: corrupt JSON is renamed to ``*.corrupt-<ISO8601 microsec>``."""
    target = project_dir / "session-state" / "sess-66.json"
    target.write_text("{ this is not json")

    fixed = freeze_now(microsecond=123456)
    merge_update_state(
        project_dir=project_dir,
        session_id="sess-66",
        patch={"skill_ran_at": "2026-04-25T13:00:00+00:00"},
        now=fixed,
    )

    # The new file is valid and contains only the patched fields.
    data = json.loads(target.read_text())
    assert data["skill_ran_at"] == "2026-04-25T13:00:00+00:00"

    # A quarantine sibling exists with the microsecond-precise suffix.
    siblings = list((project_dir / "session-state").iterdir())
    quarantines = [
        p for p in siblings if re.search(r"\.json\.corrupt-[0-9T:.]+", p.name)
    ]
    assert len(quarantines) == 1
    assert "123456" in quarantines[0].name  # microsecond resolution


def test_state_writer_tmp_uniqueness(project_dir: Path):
    """T-67: tmp filename embeds pid + monotonic_ns + uuid for collision-safety.

    Run merge_update_state twice within the same process (re-entrant case)
    while spying on ``os.replace`` to capture the chosen tmp paths. Both
    must be distinct and follow the documented pattern.
    """
    seen: list[str] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        seen.append(Path(src).name)
        real_replace(src, dst)

    with mock_patch("issueops.state_writer.os.replace", side_effect=spy_replace):
        merge_update_state(
            project_dir=project_dir,
            session_id="sess-67",
            patch={"skill_ran_at": "a"},
        )
        merge_update_state(
            project_dir=project_dir,
            session_id="sess-67",
            patch={"skill_ran_at": "b"},
        )

    assert len(seen) == 2
    assert seen[0] != seen[1]
    pattern = re.compile(
        r"^sess-67\.json\.tmp\.\d+\.\d+\.[0-9a-f]{8}$"
    )
    for name in seen:
        assert pattern.match(name), f"tmp name does not match expected pattern: {name}"
