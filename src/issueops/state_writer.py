"""Atomic merge writer for the per-session state file.

This module is the **single window** for every state-file write across
the PreCompact / UserPromptSubmit / SessionEnd / session-closer hooks.
By centralising the read-merge-write cycle here we satisfy NFR
Reliability (atomic write, race-safe tmp filenames) once and let every
caller share the same guarantees.

Design contract (see ``.spec-workflow/specs/session-closer/design.md``
§ "Atomic Write Pattern"):

1. Read the existing state file. If it cannot be parsed as JSON, move
   it aside via :func:`quarantine_corrupt` and start from an empty
   dict — never silently overwrite a corrupt file.
2. Merge the supplied ``patch`` on top of the existing dict (top-level
   keys only; lists are *replaced*, never concatenated — callers own
   list-merging semantics if they need it).
3. Force ``session_id`` into the merged result so callers cannot
   accidentally rename a session by omitting it.
4. Write to a same-directory tmp file whose name embeds
   ``pid + monotonic_ns + uuid4[:8]`` so concurrent processes and
   re-entrant calls never collide.
5. ``os.replace`` the tmp into the target (POSIX/Windows-atomic, same
   filesystem so no cross-fs rename failures).

This module **must not import** :mod:`issueops.state_save`; both share
:mod:`issueops.path_utils` to break the otherwise circular dependency.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from issueops.path_utils import (
    acquire_file_lock,
    atomic_write_json,
    state_file_path,
)

__all__ = ["merge_update_state", "quarantine_corrupt"]


def merge_update_state(
    *,
    project_dir: Path,
    session_id: str,
    patch: dict,
    now: datetime | None = None,
) -> Path:
    """Merge ``patch`` into the state file for ``session_id`` atomically.

    Returns the resolved target path on success. ``ValueError`` is
    raised for unsafe ``session_id`` values (delegated to
    :func:`issueops.path_utils.state_file_path`).

    Existing sibling fields written by other hooks (e.g.
    ``briefing_done``, ``pending_restore``, ``last_summary_at``) are
    preserved. List values inside ``patch`` overwrite their
    counterparts in the existing file — caller is responsible for any
    list-merge logic before calling.

    Concurrency: the read-merge-write critical section runs under
    :func:`issueops.path_utils.acquire_file_lock` so concurrent writers
    cannot stomp on each other (``flock`` advisory, POSIX). Without the
    lock two writers could each read the pre-state, merge their patch,
    race on ``os.replace``, and silently drop one patch.

    Durability: the underlying write goes through
    :func:`issueops.path_utils.atomic_write_json` which fsyncs the tmp
    file and the parent directory before returning.
    """
    target = state_file_path(project_dir, session_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    with acquire_file_lock(target):
        existing: dict = {}
        if target.exists():
            try:
                data = json.loads(target.read_text())
            except json.JSONDecodeError:
                quarantine_corrupt(target, now=now)
                data = {}
            if isinstance(data, dict):
                existing = data
            else:
                # Root is not an object (list/string/number). Quarantine
                # to preserve the unexpected payload — silently treating
                # it as ``{}`` would clobber the file on the next merge.
                quarantine_corrupt(target, now=now)

        merged = {**existing, "session_id": session_id, **patch}
        atomic_write_json(target, merged)
        return target


def quarantine_corrupt(target: Path, *, now: datetime | None = None) -> Path:
    """Rename ``target`` to ``<name>.corrupt-<ISO8601 microsec>``.

    Microsecond precision in the suffix avoids same-second collisions
    when many corrupt files are quarantined back-to-back. Returns the
    quarantine path so callers can log it.
    """
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S.%f")
    quarantine = target.with_name(f"{target.name}.corrupt-{ts}")
    target.rename(quarantine)
    return quarantine
