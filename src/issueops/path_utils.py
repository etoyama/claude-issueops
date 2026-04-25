"""Path-construction + atomic I/O primitives shared by state writers and readers.

Extracted from :mod:`issueops.state_save` to break a future import
cycle: :mod:`issueops.state_writer` provides the canonical atomic-write
window for the per-session state file, and :mod:`issueops.state_save`
itself is refactored to write through ``state_writer``. Both modules
need ``state_file_path`` and ``_validate_session_id`` but neither must
import the other.

Public API:
- :func:`state_file_path` — return the canonical state-file path for
  ``(project_dir, session_id)``, refusing path-traversal attempts.
- :func:`acquire_file_lock` — context manager wrapping ``fcntl.flock``
  on a sibling ``.lock`` file. Serializes read-modify-write across
  concurrent processes / hooks (POSIX only — best-effort no-op on
  non-POSIX).
- :func:`atomic_write_json` — write a dict to a path with ``fsync`` on
  the tmp file *and* the parent directory, then ``os.replace``. Tmp
  filename embeds ``pid+monotonic_ns+uuid8`` so concurrent writers do
  not collide.

The validation rule mirrors the original ``state_save._validate_session_id``
so existing callers continue to see ``ValueError`` for the same inputs.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    import fcntl  # POSIX only
except ImportError:  # pragma: no cover — Windows fallback
    fcntl = None  # type: ignore[assignment]


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


@contextmanager
def acquire_file_lock(target: Path) -> Iterator[None]:
    """Acquire an exclusive advisory lock for read-modify-write on ``target``.

    Uses a sibling ``<target>.lock`` file so the lock survives
    ``os.replace`` of the target (which would otherwise leave the lock
    on the orphaned inode). ``fcntl.flock`` is POSIX-only; on platforms
    without ``fcntl`` this is a no-op (best-effort — Windows callers
    keep the same atomicity from ``os.replace`` but lose lost-update
    protection).

    The ``.lock`` file is left in place after release: re-creating it
    on every call would race with concurrent locks. It is empty and
    cheap to leave behind.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f"{target.name}.lock")
    if fcntl is None:
        yield
        return
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def atomic_write_json(target: Path, payload: dict) -> None:
    """Write ``payload`` as JSON to ``target`` atomically and durably.

    Steps (each is a documented invariant — do not re-order):

    1. Render JSON ahead of opening any file so a serialization failure
       leaves the target untouched.
    2. Open a same-directory tmp whose name embeds
       ``pid+monotonic_ns+uuid8`` to guarantee no collisions across
       concurrent writers (PreCompact / UserPromptSubmit / SessionEnd /
       session-closer can all race).
    3. ``os.fsync`` the tmp before ``os.replace`` so a crash between
       write and rename leaves the tmp file durable on disk (otherwise
       on ext4 with auto_da_alloc the tmp may end up as an empty file
       and the replace promotes the empty file to target).
    4. ``os.replace`` for POSIX/Windows-atomic rename.
    5. ``os.fsync`` the parent directory so the rename itself is
       durable across crashes.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    suffix = f"{os.getpid()}.{time.monotonic_ns()}.{uuid.uuid4().hex[:8]}"
    tmp = target.with_name(f"{target.name}.tmp.{suffix}")

    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)

    os.replace(tmp, target)

    parent_fd = os.open(str(target.parent), os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    except OSError:  # pragma: no cover — directories are not fsyncable on some FS
        pass
    finally:
        os.close(parent_fd)
