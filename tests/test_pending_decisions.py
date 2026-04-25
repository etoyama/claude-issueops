"""L1 unit tests for ``issueops.pending_decisions``.

Test ID coverage (per test-design.md L1 table):
- T-81: ``append_pending_decisions`` writes the schema_version=1 envelope
  and **appends** to ``entries`` rather than replacing on second call.
  Round-trips successfully through JSON parsing.
- T-82: ``pending_path`` rejects unsafe ``session_id`` values via
  ``path_utils._validate_session_id`` (path traversal / empty string),
  raising ``ValueError``.

Schema (design.md § Component 7)::

    {
      "schema_version": 1,
      "session_id": "...",
      "issue_number": 8,
      "entries": [
        { "saved_at": "ISO-8601",
          "decisions": [ {slug, what, why, alternatives,
                          consequences, scope}, ... ] }
      ]
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from issueops.decision_extractor import Candidate, UserDecision
from issueops.pending_decisions import (
    PENDING_SCHEMA_VERSION,
    append_pending_decisions,
    pending_path,
)


def _make_user_decision(slug: str = "use-bin-adapter", scope: str = "issue") -> UserDecision:
    return UserDecision(
        candidate=Candidate(
            slug=slug,
            what="adopt the bin-adapter pattern",
            why="single owner of subprocess",
            alternatives="inline subprocess in skill",
            consequences="extra IPC layer",
            scope_hint="issue",  # type: ignore[arg-type]
        ),
        final_scope=scope,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# T-81: append (not replace) + schema_version envelope
# ---------------------------------------------------------------------------


def test_pending_decisions_append_idempotent(project_dir: Path, freeze_now) -> None:
    """First call creates file; second call appends a new entries[] row.

    The schema_version stays 1 across both writes; existing entries are
    not rewritten or de-duplicated (callers own dedup semantics — this
    module is an append log).
    """
    sid = "session-abc"
    issue = 8
    fixed_t1 = freeze_now(hour=10)
    fixed_t2 = freeze_now(hour=11)

    d1 = _make_user_decision(slug="first-decision", scope="issue")
    d2 = _make_user_decision(slug="second-decision", scope="cross-issue")

    # First write — file does not yet exist.
    path1 = append_pending_decisions(
        project_dir=project_dir,
        session_id=sid,
        issue_number=issue,
        decisions=[d1],
        now=fixed_t1,
    )

    # Returned path matches pending_path() and the file is on disk.
    assert path1 == pending_path(project_dir, sid)
    assert path1.exists()

    payload1 = json.loads(path1.read_text())
    assert payload1["schema_version"] == PENDING_SCHEMA_VERSION == 1
    assert payload1["session_id"] == sid
    assert payload1["issue_number"] == issue
    assert isinstance(payload1["entries"], list)
    assert len(payload1["entries"]) == 1

    entry1 = payload1["entries"][0]
    assert entry1["saved_at"].startswith("2026-04-25T10:")
    assert len(entry1["decisions"]) == 1
    assert entry1["decisions"][0]["slug"] == "first-decision"
    assert entry1["decisions"][0]["scope"] == "issue"

    # Second write — must append, not replace.
    path2 = append_pending_decisions(
        project_dir=project_dir,
        session_id=sid,
        issue_number=issue,
        decisions=[d2],
        now=fixed_t2,
    )
    assert path2 == path1

    payload2 = json.loads(path2.read_text())
    assert payload2["schema_version"] == 1
    assert payload2["session_id"] == sid
    assert payload2["issue_number"] == issue
    assert len(payload2["entries"]) == 2
    # Original entry preserved
    assert payload2["entries"][0]["decisions"][0]["slug"] == "first-decision"
    # New entry has its own saved_at
    assert payload2["entries"][1]["decisions"][0]["slug"] == "second-decision"
    assert payload2["entries"][1]["saved_at"].startswith("2026-04-25T11:")
    assert payload2["entries"][1]["decisions"][0]["scope"] == "cross-issue"

    # Atomic write hygiene: no leftover .tmp files in the directory.
    leftover = list(path1.parent.glob(f"{path1.name}.tmp.*"))
    assert leftover == [], f"unexpected tmp leftovers: {leftover}"


def test_pending_decisions_schema_version_mismatch_raises(project_dir: Path, freeze_now) -> None:
    """A pre-existing pending file with the wrong schema_version is fatal.

    ``append_pending_decisions`` must not silently overwrite a file
    written by an incompatible future / past version of the skill.
    """
    sid = "session-xyz"
    target = pending_path(project_dir, sid)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "schema_version": 999,  # incompatible
        "session_id": sid,
        "issue_number": 8,
        "entries": [],
    }))

    with pytest.raises(ValueError):
        append_pending_decisions(
            project_dir=project_dir,
            session_id=sid,
            issue_number=8,
            decisions=[_make_user_decision()],
            now=freeze_now(),
        )


# ---------------------------------------------------------------------------
# T-82: unsafe session_id (delegates to path_utils._validate_session_id)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_sid",
    [
        "../escape",
        "with/slash",
        "with\\backslash",
        "..",
        "",
    ],
)
def test_pending_decisions_unsafe_session_id(project_dir: Path, bad_sid: str) -> None:
    """``pending_path`` must refuse path-traversal-style session_id values.

    The validation is delegated to ``path_utils._validate_session_id``;
    this test confirms ``pending_path`` actually calls it.
    """
    with pytest.raises(ValueError):
        pending_path(project_dir, bad_sid)

    # The append helper, which also goes through pending_path, must
    # raise the same ValueError without leaving any file behind.
    with pytest.raises(ValueError):
        append_pending_decisions(
            project_dir=project_dir,
            session_id=bad_sid,
            issue_number=8,
            decisions=[_make_user_decision()],
            now=datetime(2026, 4, 25, tzinfo=timezone.utc),
        )

    # Sanity: nothing was written under session-state/ for the bad ID.
    bad_artifacts = list((project_dir / "session-state").glob(f"*{bad_sid or 'EMPTY'}*"))
    # An empty-string session_id glob can match anything; filter to files
    # whose name actually contains the bad_sid substring.
    if bad_sid:
        assert all(bad_sid not in p.name for p in bad_artifacts)
