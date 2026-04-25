"""Verification fixture loader (AskUserQuestion bypass).

This module is part of the L3 verification harness. It exists so that the
Claude Code session can replay user responses to ``AskUserQuestion`` from a
JSON fixture file, allowing Claude to drive the full skill flow end-to-end
without a human in the loop.

To prevent accidental activation in normal operation, fixture loading is
guarded by a **double check** on environment variables:

  - ``CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE``: path to a JSON fixture. The
    path must resolve to a location *inside* a directory named
    ``verification-fixtures`` (path-traversal attempts are rejected).
  - ``CLAUDE_ISSUEOPS_VERIFICATION_MODE``: must be exactly ``"1"``.

If both are set and valid, the JSON is parsed and returned as a ``dict``.
If only one is set (or one is set incorrectly), a one-line warning is
written to ``stderr`` and the loader returns ``None``. If neither is set,
the loader returns ``None`` silently — that is the normal case in
production.

Schema (per ``design.md`` "verification fixture schema"):

    {
      "schema_version": 1,
      "responses": [
        {"question_id": "...", "selections": [...]}
      ]
    }

A ``schema_version`` mismatch raises :class:`ValueError`, matching the
convention used by the other ``schema_version: 1`` modules in this
codebase.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Required directory name that any fixture path must live inside.
_FIXTURE_DIR_NAME = "verification-fixtures"

#: Schema version this loader understands.
FIXTURE_SCHEMA_VERSION = 1

_ENV_FIXTURE = "CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE"
_ENV_MODE = "CLAUDE_ISSUEOPS_VERIFICATION_MODE"


def _warn(message: str) -> None:
    """Emit a single-line warning to stderr (no trailing newline duplication)."""

    sys.stderr.write(f"[verification_fixture] {message}\n")


def _path_is_under_fixture_dir(resolved: Path) -> bool:
    """Return True iff any path component is ``verification-fixtures``.

    Using component membership (rather than ``startswith`` against a fixed
    prefix) keeps the check robust against the project's working directory:
    the fixture lives at ``<repo>/verification-fixtures/<file>`` regardless
    of how the user invoked the skill.
    """

    return _FIXTURE_DIR_NAME in resolved.parts


def load_fixture_or_none() -> dict | None:
    """Load the verification fixture if (and only if) both guards pass.

    Returns the parsed JSON ``dict`` on success, or ``None`` when fixture
    mode is disabled or misconfigured. Misconfigurations (one env var set
    without the other, or a path outside ``verification-fixtures/``) emit
    a stderr warning so they cannot silently degrade tests.

    Raises :class:`ValueError` if the JSON parses but ``schema_version``
    does not match :data:`FIXTURE_SCHEMA_VERSION`.
    """

    fixture_value = os.environ.get(_ENV_FIXTURE)
    mode_value = os.environ.get(_ENV_MODE)

    fixture_set = bool(fixture_value)
    mode_ok = mode_value == "1"

    # Common case: no fixture mode at all. Stay silent.
    if not fixture_set and not mode_ok:
        return None

    # One side configured, the other missing/invalid: warn and bail out.
    if fixture_set and not mode_ok:
        _warn(
            f"{_ENV_FIXTURE} is set but {_ENV_MODE}=1 is missing; "
            "fixture mode is incomplete and will be ignored."
        )
        return None

    if mode_ok and not fixture_set:
        _warn(
            f"{_ENV_MODE}=1 is set but {_ENV_FIXTURE} is missing; "
            "fixture mode is incomplete and will be ignored."
        )
        return None

    # Both are present. Validate the path before reading.
    assert fixture_value is not None  # for type-checkers
    candidate = Path(fixture_value)
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        _warn(f"could not resolve fixture path {fixture_value!r}: {exc}")
        return None

    if not _path_is_under_fixture_dir(resolved):
        _warn(
            f"fixture path {fixture_value!r} is not under "
            f"{_FIXTURE_DIR_NAME}/; refusing to load."
        )
        return None

    if not resolved.is_file():
        _warn(f"fixture path {fixture_value!r} does not point to a file.")
        return None

    text = resolved.read_text(encoding="utf-8")
    data = json.loads(text)  # JSONDecodeError propagates per spec.

    if not isinstance(data, dict):
        raise ValueError(
            f"fixture root must be a JSON object, got {type(data).__name__}"
        )

    schema_version = data.get("schema_version")
    if schema_version != FIXTURE_SCHEMA_VERSION:
        raise ValueError(
            f"fixture schema_version mismatch: expected "
            f"{FIXTURE_SCHEMA_VERSION}, got {schema_version!r}"
        )

    return data
