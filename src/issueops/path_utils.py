"""Path-construction primitives shared by state writers and readers.

Extracted from :mod:`issueops.state_save` to break a future import
cycle: :mod:`issueops.state_writer` provides the canonical atomic-write
window for the per-session state file, and :mod:`issueops.state_save`
itself is refactored to write through ``state_writer``. Both modules
need ``state_file_path`` and ``_validate_session_id`` but neither must
import the other.

Public API:
- :func:`state_file_path` — return the canonical state-file path for
  ``(project_dir, session_id)``, refusing path-traversal attempts.

The validation rule mirrors the original ``state_save._validate_session_id``
so existing callers continue to see ``ValueError`` for the same inputs.
"""

from __future__ import annotations

from pathlib import Path


def _validate_session_id(session_id: str) -> None:
    """Reject session IDs that could escape the ``session-state/`` directory.

    Raises ``ValueError`` for any ``session_id`` that:
    - contains a forward slash (``/``)
    - contains a backslash (``\\``) — protects Windows callers too
    - contains ``..`` (path traversal)
    - is the empty string
    """
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        raise ValueError(f"unsafe session_id: {session_id!r}")
    if not session_id:
        raise ValueError("session_id must not be empty")


def state_file_path(project_dir: Path, session_id: str) -> Path:
    """Return the canonical state-file path for ``session_id``.

    The path is ``<project_dir>/session-state/<session_id>.json``.
    Refuses session IDs that contain path separators or ``..`` so a
    malicious or malformed value cannot escape ``session-state/``.
    """
    _validate_session_id(session_id)
    return project_dir / "session-state" / f"{session_id}.json"
