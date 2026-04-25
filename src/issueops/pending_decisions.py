"""Append-only pending-decisions log for the gh-failure "save" branch.

When ``gh issue comment`` fails for one or more candidates and the user
chooses **保存** in the 3-choice dialog (R-9.4), the unposted decisions
must survive across sessions. This module owns that file:

- :func:`pending_path` — canonical location next to the per-session
  state file: ``<project_dir>/session-state/<session_id>.pending-decisions.json``.
  Path validation is delegated to
  :func:`issueops.path_utils._validate_session_id` (single source of truth).

- :func:`append_pending_decisions` — read-merge-atomic-write the file,
  appending a new ``entries[]`` row each call. The schema is **versioned**
  (``schema_version=1``); a mismatched existing version raises
  ``ValueError`` rather than silently rewriting the file.

Why we re-implement the atomic write inline (rather than calling
:mod:`state_writer`): the pending file's schema is fundamentally
different (``entries`` is an append list, not a flat key-merge), and
``state_writer.merge_update_state`` would replace lists per design.
We follow the *same* tmp-naming and ``os.replace`` discipline so all
session-state writes share the same race-safety properties.

Schema (design.md § Component 7)::

    {
      "schema_version": 1,
      "session_id": "<sid>",
      "issue_number": 8,
      "entries": [
        { "saved_at": "ISO-8601",
          "decisions": [
            { "slug": "...", "what": "...", "why": "...",
              "alternatives": "...", "consequences": "...",
              "scope": "issue" | "cross-issue" }
          ] }
      ]
    }
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from issueops.decision_extractor import UserDecision
from issueops.path_utils import _validate_session_id

__all__ = [
    "PENDING_SCHEMA_VERSION",
    "pending_path",
    "append_pending_decisions",
]


PENDING_SCHEMA_VERSION = 1


def pending_path(project_dir: Path, session_id: str) -> Path:
    """Return the canonical pending-decisions path for ``session_id``.

    The path is ``<project_dir>/session-state/<session_id>.pending-decisions.json``.
    Refuses unsafe ``session_id`` values via
    :func:`issueops.path_utils._validate_session_id` so the file cannot
    escape ``session-state/``.
    """
    _validate_session_id(session_id)
    return project_dir / "session-state" / f"{session_id}.pending-decisions.json"


def _serialize_decision(d: UserDecision) -> dict:
    """Flatten a ``UserDecision`` into the JSON shape stored in entries."""
    c = d.candidate
    return {
        "slug": c.slug,
        "what": c.what,
        "why": c.why,
        "alternatives": c.alternatives,
        "consequences": c.consequences,
        "scope": d.final_scope,
    }


def append_pending_decisions(
    *,
    project_dir: Path,
    session_id: str,
    issue_number: int,
    decisions: list[UserDecision],
    now: datetime | None = None,
) -> Path:
    """Append ``decisions`` to the pending file as a single new entry.

    Returns the resolved target path on success.

    - Existing files with ``schema_version != PENDING_SCHEMA_VERSION``
      raise ``ValueError`` (callers must not silently overwrite an
      incompatible file).
    - When the file does not exist yet, a fresh envelope is created.
    - The write is atomic: a same-directory tmp (``<file>.tmp.<pid>.<monotonic_ns>.<uuid8>``)
      is filled, then ``os.replace``\\ d on top of the target. The tmp
      naming pattern matches :mod:`state_writer` so concurrent hooks
      and re-entrant calls never collide.
    """
    target = pending_path(project_dir, session_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Read existing envelope (if any).
    if target.exists():
        try:
            existing = json.loads(target.read_text())
        except json.JSONDecodeError as e:
            # We don't quarantine the pending file: unlike the state file
            # this is the user's only record of unposted decisions. Force
            # the caller to inspect manually.
            raise ValueError(
                f"pending file is not valid JSON: {target}"
            ) from e
        if not isinstance(existing, dict):
            raise ValueError(
                f"pending file root must be a JSON object: {target}"
            )
        existing_version = existing.get("schema_version")
        if existing_version != PENDING_SCHEMA_VERSION:
            raise ValueError(
                f"pending file schema_version mismatch: "
                f"got {existing_version!r}, expected {PENDING_SCHEMA_VERSION}"
            )
        entries = list(existing.get("entries") or [])
    else:
        entries = []

    saved_at = (now or datetime.now(timezone.utc)).isoformat()

    new_entry = {
        "saved_at": saved_at,
        "decisions": [_serialize_decision(d) for d in decisions],
    }
    entries.append(new_entry)

    payload = {
        "schema_version": PENDING_SCHEMA_VERSION,
        "session_id": session_id,
        "issue_number": int(issue_number),
        "entries": entries,
    }

    # Atomic write — same pattern as ``state_writer.merge_update_state``:
    # pid + monotonic_ns + uuid4[:8] tmp suffix, ``os.replace`` for
    # POSIX/Windows-atomic rename within the same directory.
    suffix = f"{os.getpid()}.{time.monotonic_ns()}.{uuid.uuid4().hex[:8]}"
    tmp = target.with_name(f"{target.name}.tmp.{suffix}")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    os.replace(tmp, target)
    return target
