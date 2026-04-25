"""Tests for ``issueops.path_utils`` — state-file path construction.

This module is extracted from ``issueops.state_save`` to break a future
import cycle: ``state_writer`` will need ``state_file_path`` while
``state_save`` will be refactored to use ``state_writer``. Both must
depend on a shared low-level module that imports neither.

Test IDs (per Test Design § Level 1):
- T-92: state_file_path normal session_id → canonical path
- T-93: unsafe session_id (path separators / `..`) → ValueError
- T-94: empty session_id → ValueError
"""

from __future__ import annotations

from pathlib import Path

import pytest

from issueops.path_utils import state_file_path


def test_path_utils_state_file_path_normal(tmp_path: Path):
    """T-92: normal session_id resolves to ``<project>/session-state/<sid>.json``."""
    p = state_file_path(tmp_path, "abc123")
    assert p == tmp_path / "session-state" / "abc123.json"


def test_path_utils_unsafe_session_id_raises(tmp_path: Path):
    """T-93: session_id containing path separators or ``..`` is rejected."""
    with pytest.raises(ValueError, match="session_id"):
        state_file_path(tmp_path, "../escape")
    with pytest.raises(ValueError, match="session_id"):
        state_file_path(tmp_path, "a/b")
    with pytest.raises(ValueError, match="session_id"):
        state_file_path(tmp_path, "a\\b")


def test_path_utils_empty_session_id_raises(tmp_path: Path):
    """T-94: empty session_id is rejected (boundary case)."""
    with pytest.raises(ValueError, match="session_id"):
        state_file_path(tmp_path, "")
