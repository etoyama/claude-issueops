"""Tests for the PreCompact orchestration layer.

The bin entrypoint (``bin/precompact_hook.py``) is intentionally thin:
it reads stdin, wires up real git/gh subprocesses, and delegates here.
These tests cover the orchestration with injected ``git_branch_fn`` /
``gh_fetch_fn`` callables, so the bin script remains untested but
trivially short.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from issueops.precompact import (
    BODY_EXCERPT_MAX_CHARS,
    snapshot_current_issue,
    run_precompact,
)
from issueops.state_save import IssueSnapshot


def _gh_payload(
    *,
    title: str = "feature: foo",
    body: str = "Body text",
    comments: list[str] | None = None,
) -> dict:
    return {
        "title": title,
        "body": body,
        "comments": [{"body": c} for c in (comments or [])],
    }


def test_snapshot_returns_none_when_branch_yields_no_issue():
    snap = snapshot_current_issue(
        branch="master",
        gh_fetch_fn=lambda n: pytest.fail("gh must not be called"),
        latest_in_progress_fn=lambda: None,
    )
    assert snap is None


def test_snapshot_uses_branch_issue_number_without_fallback():
    calls = []

    def gh_fetch(num: int) -> dict:
        calls.append(num)
        return _gh_payload()

    snap = snapshot_current_issue(
        branch="feat/132-thing",
        gh_fetch_fn=gh_fetch,
        latest_in_progress_fn=lambda: pytest.fail("fallback must not run"),
    )
    assert snap is not None
    assert snap.number == 132
    assert calls == [132]


def test_snapshot_falls_back_when_branch_does_not_match():
    snap = snapshot_current_issue(
        branch="master",
        gh_fetch_fn=lambda n: _gh_payload(title="from fallback"),
        latest_in_progress_fn=lambda: 7,
    )
    assert snap is not None
    assert snap.number == 7
    assert snap.title == "from fallback"


def test_snapshot_truncates_body_excerpt():
    long_body = "x" * (BODY_EXCERPT_MAX_CHARS + 100)
    snap = snapshot_current_issue(
        branch="feat/1-x",
        gh_fetch_fn=lambda n: _gh_payload(body=long_body),
        latest_in_progress_fn=lambda: None,
    )
    assert snap is not None
    assert len(snap.body_excerpt) == BODY_EXCERPT_MAX_CHARS


def test_snapshot_extracts_parent_epic_from_body():
    body = "Parent: #7\n\nSome description here."
    snap = snapshot_current_issue(
        branch="feat/10-x",
        gh_fetch_fn=lambda n: _gh_payload(body=body),
        latest_in_progress_fn=lambda: None,
    )
    assert snap is not None
    assert snap.parent_epic == 7


def test_snapshot_parent_epic_is_none_when_absent():
    snap = snapshot_current_issue(
        branch="feat/10-x",
        gh_fetch_fn=lambda n: _gh_payload(body="No parent here"),
        latest_in_progress_fn=lambda: None,
    )
    assert snap is not None
    assert snap.parent_epic is None


def test_snapshot_collects_decision_slugs_from_body_and_comments():
    body = (
        "## Decision: pick-postgres\n\n"
        "**What:** use postgres\n"
        "**Why:** SQL\n"
        "**Alternatives considered:** sqlite\n"
        "**Consequences:** ops cost\n"
    )
    comment = (
        "## Decision: schema-v2\n\n"
        "**What:** new schema\n"
        "**Why:** scale\n"
        "**Alternatives considered:** keep v1\n"
        "**Consequences:** migration\n"
    )
    snap = snapshot_current_issue(
        branch="feat/10-x",
        gh_fetch_fn=lambda n: _gh_payload(body=body, comments=[comment]),
        latest_in_progress_fn=lambda: None,
    )
    assert snap is not None
    assert set(snap.decision_slugs) == {"pick-postgres", "schema-v2"}


def test_snapshot_skips_malformed_decisions():
    # Missing "Why" — parser must reject.
    body = (
        "## Decision: incomplete\n\n"
        "**What:** something\n"
        "**Alternatives considered:** other\n"
        "**Consequences:** maybe\n"
    )
    snap = snapshot_current_issue(
        branch="feat/10-x",
        gh_fetch_fn=lambda n: _gh_payload(body=body),
        latest_in_progress_fn=lambda: None,
    )
    assert snap is not None
    assert snap.decision_slugs == ()


def test_run_precompact_writes_state_file_when_issue_resolves(tmp_path: Path):
    fixed = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    target = run_precompact(
        project_dir=tmp_path,
        session_id="sess-1",
        branch="feat/10-precompact-hook",
        gh_fetch_fn=lambda n: _gh_payload(title="precompact hook"),
        latest_in_progress_fn=lambda: None,
        now=fixed,
    )
    assert target is not None
    data = json.loads(target.read_text())
    assert data["pending_restore"]["issue_number"] == 10
    assert data["pending_restore"]["title"] == "precompact hook"


def test_run_precompact_is_noop_on_master(tmp_path: Path):
    target = run_precompact(
        project_dir=tmp_path,
        session_id="sess-1",
        branch="master",
        gh_fetch_fn=lambda n: pytest.fail("gh must not be called on master"),
        latest_in_progress_fn=lambda: None,
    )
    assert target is None
    assert not (tmp_path / "session-state").exists()


def test_run_precompact_swallows_gh_errors_and_returns_none(tmp_path: Path):
    # gh failures must not break compaction; the orchestrator returns
    # None and writes nothing.
    def failing_gh(n: int) -> dict:
        raise RuntimeError("gh: API rate limit exceeded")

    target = run_precompact(
        project_dir=tmp_path,
        session_id="sess-1",
        branch="feat/10-x",
        gh_fetch_fn=failing_gh,
        latest_in_progress_fn=lambda: None,
    )
    assert target is None
    assert not (tmp_path / "session-state" / "sess-1.json").exists()
