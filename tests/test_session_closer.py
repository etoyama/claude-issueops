"""Tests for ``issueops.session_closer`` orchestrator + helpers.

Per Test Design § Level 1 / Level 2 this file contains:

L1 helpers (orchestrator-internal pure logic):
- T-21: per-slug success/failure → captured_slugs population
- T-31: summary marker idempotency check
- T-32: summary marker format

L2 end-to-end (run_capture / run_close, callable injection only):
- T-101 〜 T-131: scenario-by-scenario run_capture / run_close behaviour
- T-134, T-135: pending-decisions append paths
- T-136: subcommand-separation contract (post-decisions must not write state)

T-132 / T-133 are intentionally **not** here — they live in
test_state_save.py / test_session_end.py per the tasks.md compat split.

Every state-writing test annotates which row of the design.md
"State Writes Table" it covers (10 rows: rows 1〜10).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from issueops.decision_extractor import Candidate, UserDecision
from issueops.gh_adapters import (
    GhFailure,
    GhFailureKind,
    PostResult,
)


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _make_candidate(slug: str, scope: str = "issue") -> Candidate:
    return Candidate(
        slug=slug,
        what=f"do {slug}",
        why=f"because {slug}",
        alternatives=f"not {slug}",
        consequences=f"{slug} happens",
        scope_hint=scope,  # type: ignore[arg-type]
    )


def _make_user_decision(slug: str, final_scope: str = "issue") -> UserDecision:
    return UserDecision(
        candidate=_make_candidate(slug, scope=final_scope),
        final_scope=final_scope,  # type: ignore[arg-type]
    )


def _ok_post(slug: str) -> PostResult:
    return PostResult(
        ok=True,
        comment_url=f"https://github.com/x/y/issues/1#c-{slug}",
        failure=None,
    )


def _fail_post(kind: GhFailureKind = GhFailureKind.UNKNOWN) -> PostResult:
    return PostResult(
        ok=False,
        comment_url=None,
        failure=GhFailure(
            kind=kind,
            stderr="boom",
            exit_code=1,
            hint="gh auth status を実行してください" if kind == GhFailureKind.AUTH else None,
        ),
    )


def _wrap_commit(project_dir: Path, calls: list[dict] | None = None):
    """Build a ``commit_state_fn`` that delegates to ``merge_update_state``.

    Records each invocation's patch into ``calls`` (when supplied) so
    tests can introspect what the orchestrator decided to write.
    """
    from issueops.state_writer import merge_update_state

    def _fn(*, session_id: str, patch: dict, now: datetime | None = None) -> Path:
        if calls is not None:
            calls.append(dict(patch))
        return merge_update_state(
            project_dir=project_dir,
            session_id=session_id,
            patch=patch,
            now=now,
        )

    return _fn


def _read_state(project_dir: Path, session_id: str) -> dict:
    p = project_dir / "session-state" / f"{session_id}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


# ---------------------------------------------------------------------------
# L1: helper unit tests (T-21, T-31, T-32)
# ---------------------------------------------------------------------------


def test_summary_marker_format():
    """T-31's prerequisite: helper renders a marker containing session_id."""
    from issueops.session_closer import build_summary_marker

    text = build_summary_marker("sess-abc")
    assert "<!-- claude-issueops:session-closer:summary:sess-abc -->" in text
    assert "## Session summary" in text


def test_summary_marker_idempotent():
    """T-31: ``is_summary_already_posted`` finds the marker in any comment."""
    from issueops.session_closer import is_summary_already_posted

    sid = "sess-31"
    posted_body = (
        "<!-- claude-issueops:session-closer:summary:sess-31 -->\n"
        "## Session summary\n"
    )
    assert is_summary_already_posted([posted_body], sid) is True
    assert is_summary_already_posted(["unrelated comment"], sid) is False
    # Different sid in marker → not a match (idempotency is per-session)
    other = (
        "<!-- claude-issueops:session-closer:summary:sess-other -->\n"
        "## Session summary\n"
    )
    assert is_summary_already_posted([other], sid) is False
    # Empty list passthrough
    assert is_summary_already_posted([], sid) is False


def test_run_capture_partial_success_state(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-21: per-slug success → captured_slugs has only successful slugs.

    Scenario row 2 (gh fail → user picks "save"): we feed 5 candidates
    where slugs ``s1``,``s3``,``s5`` succeed and ``s2``,``s4`` fail. The
    captured_slugs in the resulting state must be exactly the 3 successes.
    """
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [_make_user_decision(f"s{i}") for i in range(1, 6)]

    gh_post = gh_post_fn_factory(
        results=[
            _ok_post("s1"),
            _fail_post(),
            _ok_post("s3"),
            _fail_post(),
            _ok_post("s5"),
        ]
    )

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-21",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=999,
        extracted_candidate_count=5,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        failure_choice="save",
        now=fixed,
    )
    result = run_capture(req)

    assert result.posted_slugs == ["s1", "s3", "s5"]
    assert result.failed_slugs == ["s2", "s4"]
    state = _read_state(project_dir, "sess-21")
    assert state["captured_slugs"] == ["s1", "s3", "s5"]


# ---------------------------------------------------------------------------
# L2: run_capture (T-101 〜 T-110, T-117 〜 T-124, T-129 〜 T-131, T-134 〜 T-136)
# ---------------------------------------------------------------------------


def test_run_capture_happy_path(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-101: scenario row 1 — all candidates succeed, full state write."""
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [_make_user_decision(f"s{i}") for i in range(1, 6)]
    gh_post = gh_post_fn_factory(results=[_ok_post(f"s{i}") for i in range(1, 6)])

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-101",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=2048,
        extracted_candidate_count=5,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        now=fixed,
    )
    result = run_capture(req)

    assert result.posted_slugs == ["s1", "s2", "s3", "s4", "s5"]
    assert result.failed_slugs == []
    state = _read_state(project_dir, "sess-101")
    assert state["skill_ran_at"] == fixed.isoformat()
    assert state["last_processed_offset"] == 2048
    assert state["captured_slugs"] == ["s1", "s2", "s3", "s4", "s5"]
    # No pending file
    pending = project_dir / "session-state" / "sess-101.pending-decisions.json"
    assert not pending.exists()


def test_run_capture_no_candidates_early_exit(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-102: scenario row 6 — 0 candidates, skill_ran_at + offset only."""
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    gh_post = gh_post_fn_factory(results=[])

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-102",
        issue_number=42,
        user_decisions=[],
        transcript_end_offset=512,
        extracted_candidate_count=0,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        now=fixed,
    )
    result = run_capture(req)

    assert result.posted_slugs == []
    assert result.failed_slugs == []
    assert gh_post.calls == []  # type: ignore[attr-defined]
    state = _read_state(project_dir, "sess-102")
    assert state["skill_ran_at"] == fixed.isoformat()
    assert state["last_processed_offset"] == 512
    assert state.get("captured_slugs", None) in (None, [])


def test_run_capture_partial_failure(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-103: scenario row 2 — 5 candidates, 3 succeed → captured 3 + pending 2."""
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [_make_user_decision(f"s{i}") for i in range(1, 6)]
    gh_post = gh_post_fn_factory(
        results=[
            _ok_post("s1"),
            _fail_post(GhFailureKind.AUTH),
            _ok_post("s3"),
            _fail_post(GhFailureKind.AUTH),
            _ok_post("s5"),
        ]
    )

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-103",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=1024,
        extracted_candidate_count=5,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        failure_choice="save",
        now=fixed,
    )
    result = run_capture(req)

    assert result.posted_slugs == ["s1", "s3", "s5"]
    assert result.failed_slugs == ["s2", "s4"]
    state = _read_state(project_dir, "sess-103")
    assert state["captured_slugs"] == ["s1", "s3", "s5"]
    assert state["last_processed_offset"] == 1024

    pending = project_dir / "session-state" / "sess-103.pending-decisions.json"
    assert pending.exists()
    pending_data = json.loads(pending.read_text())
    saved_slugs = [d["slug"] for d in pending_data["entries"][0]["decisions"]]
    assert saved_slugs == ["s2", "s4"]


def test_run_capture_offset_committed_on_completion(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-104: scenario row 1 — last_processed_offset commits only after full pass."""
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [_make_user_decision("only")]
    gh_post = gh_post_fn_factory(results=[_ok_post("only")])

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-104",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=4096,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        now=fixed,
    )
    run_capture(req)

    state = _read_state(project_dir, "sess-104")
    assert state["last_processed_offset"] == 4096


def test_run_capture_writes_skill_ran_at_always(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-105: any non-error completion writes skill_ran_at (rows 1-6, 8)."""
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [_make_user_decision("only")]
    gh_post = gh_post_fn_factory(results=[_fail_post()])

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-105",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        failure_choice="save",
        now=fixed,
    )
    run_capture(req)

    state = _read_state(project_dir, "sess-105")
    assert state["skill_ran_at"] == fixed.isoformat()


def test_run_capture_transcript_missing_no_state_change(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-106: scenario row 7 — transcript missing → no state file written.

    We simulate this by having the SKILL.md path-equivalent (the caller)
    not invoke run_capture at all; orchestrator's contract here is that
    when ``user_decisions=[]`` AND ``extracted_candidate_count<0`` (a
    sentinel meaning "transcript was missing"), it must skip every
    write. Implementation-wise we route this via a separate
    ``transcript_missing=True`` flag on the request.
    """
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    gh_post = gh_post_fn_factory(results=[])

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-106",
        issue_number=42,
        user_decisions=[],
        transcript_end_offset=0,
        extracted_candidate_count=0,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        transcript_missing=True,
        now=fixed,
    )
    result = run_capture(req)

    assert result.aborted is True
    state_path = project_dir / "session-state" / "sess-106.json"
    assert not state_path.exists()


def test_run_capture_sigint_keeps_previous_state(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-107: scenario row 10 — KeyboardInterrupt before commit-state.

    We pre-create a state file with a known snapshot, then make
    ``commit_state_fn`` raise ``KeyboardInterrupt``. The state file must
    remain byte-for-byte identical to its snapshot, and no tmp files
    should linger.
    """
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [_make_user_decision("only")]
    gh_post = gh_post_fn_factory(results=[_ok_post("only")])

    state_path = project_dir / "session-state" / "sess-107.json"
    snapshot = json.dumps({"session_id": "sess-107", "captured_slugs": ["pre"]})
    state_path.write_text(snapshot)
    snapshot_bytes = state_path.read_bytes()

    def boom(**_kwargs):
        raise KeyboardInterrupt()

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-107",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=512,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        commit_state_fn=boom,
        now=fixed,
    )
    with pytest.raises(KeyboardInterrupt):
        run_capture(req)

    assert state_path.read_bytes() == snapshot_bytes
    tmps = [
        p for p in (project_dir / "session-state").iterdir()
        if ".tmp." in p.name
    ]
    assert tmps == []


def test_user_question_payload_for_failure_3choices(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-108: failure result payload exposes fields needed for 3-choice UI."""
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [_make_user_decision("s1"), _make_user_decision("s2")]
    gh_post = gh_post_fn_factory(
        results=[_ok_post("s1"), _fail_post(GhFailureKind.AUTH)]
    )

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-108",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=2,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        failure_choice="save",
        now=fixed,
    )
    result = run_capture(req)

    assert result.failed_slugs == ["s2"]
    assert result.gh_failure_kind == GhFailureKind.AUTH
    assert result.gh_hint == "gh auth status を実行してください"
    assert result.failed_slug_summaries == [{"slug": "s2", "what": "do s2"}]


def test_user_question_payload_for_multiselect(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-109: posted_decisions payload carries slug + 1-line what summary."""
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [
        _make_user_decision("alpha"),
        _make_user_decision("beta"),
    ]
    gh_post = gh_post_fn_factory(
        results=[_ok_post("alpha"), _ok_post("beta")]
    )

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-109",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=2,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        now=fixed,
    )
    result = run_capture(req)

    assert result.posted_slug_summaries == [
        {"slug": "alpha", "what": "do alpha"},
        {"slug": "beta", "what": "do beta"},
    ]


def test_dedup_remote_failure_warning_message(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-110: when caller signals tier2_skipped, run_capture echoes warning.

    SKILL.md handles the tier-2 dedup gh failure detection itself (since
    filter-dedup is a separate subcommand). The orchestrator just has to
    surface the failure_kind into the result so SKILL.md can include it
    in the user-facing 1-line summary.
    """
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [_make_user_decision("s1")]
    gh_post = gh_post_fn_factory(results=[_ok_post("s1")])

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-110",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        tier2_skipped_kind=GhFailureKind.NETWORK,
        now=fixed,
    )
    result = run_capture(req)

    assert result.warnings
    assert any("tier2" in w.lower() for w in result.warnings)
    assert any("network" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# L2: run_close (T-111 〜 T-116, T-125 〜 T-128)
# ---------------------------------------------------------------------------


def test_run_close_invokes_capture_flow(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """T-111: run_close runs the capture flow, posts decisions, then summary."""
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    user_decisions = [_make_user_decision("alpha")]
    gh_post = gh_post_fn_factory(
        results=[_ok_post("alpha"), _ok_post("summary")]
    )
    gh_view = gh_view_comments_fn_factory(results=[[]])

    req = CloseRequest(
        project_dir=project_dir,
        session_id="sess-111",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        gh_view_comments_fn=gh_view,
        commit_state_fn=_wrap_commit(project_dir),
        memory_dir=project_dir / "memory",
        now=fixed,
    )
    result = run_close(req)

    assert result.capture.posted_slugs == ["alpha"]
    # Two posts: one decision + one summary
    assert len(gh_post.calls) == 2  # type: ignore[attr-defined]


def test_run_close_summary_when_decisions_posted(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """T-112: at least 1 posted decision → summary marker on issue."""
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    user_decisions = [_make_user_decision("a")]
    gh_post = gh_post_fn_factory(
        results=[_ok_post("a"), _ok_post("summary")]
    )
    gh_view = gh_view_comments_fn_factory(results=[[]])

    req = CloseRequest(
        project_dir=project_dir,
        session_id="sess-112",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        gh_view_comments_fn=gh_view,
        commit_state_fn=_wrap_commit(project_dir),
        memory_dir=project_dir / "memory",
        now=fixed,
    )
    result = run_close(req)

    assert result.summary_posted is True
    # Last call to gh_post should be the summary body containing the marker.
    # calls is list[(args, kwargs)] — body is positional arg index 1.
    last_args, _last_kwargs = gh_post.calls[-1]  # type: ignore[attr-defined]
    body = last_args[1]
    assert "session-closer:summary:sess-112" in body


def test_run_close_summary_idempotent(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """T-113: existing summary marker for same sid → skip the post."""
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    user_decisions = [_make_user_decision("a")]
    gh_post = gh_post_fn_factory(results=[_ok_post("a")])
    existing_comments = [
        {"body": "<!-- claude-issueops:session-closer:summary:sess-113 -->\n## Session summary\n"}
    ]
    gh_view = gh_view_comments_fn_factory(results=[existing_comments])

    req = CloseRequest(
        project_dir=project_dir,
        session_id="sess-113",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        gh_view_comments_fn=gh_view,
        commit_state_fn=_wrap_commit(project_dir),
        memory_dir=project_dir / "memory",
        now=fixed,
    )
    result = run_close(req)

    assert result.summary_posted is False
    assert result.summary_skipped_reason == "idempotent"
    # Only the single decision was posted (no summary call)
    assert len(gh_post.calls) == 1  # type: ignore[attr-defined]


def test_run_close_escalates_cross_issue_only(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """T-114: run_close escalates only cross-issue scoped decisions."""
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    user_decisions = [
        _make_user_decision("issue-only", final_scope="issue"),
        _make_user_decision("global", final_scope="cross-issue"),
    ]
    gh_post = gh_post_fn_factory(
        results=[_ok_post("issue-only"), _ok_post("global"), _ok_post("summary")]
    )
    gh_view = gh_view_comments_fn_factory(results=[[]])
    memory_dir = project_dir / "memory"

    req = CloseRequest(
        project_dir=project_dir,
        session_id="sess-114",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=2,
        gh_post_fn=gh_post,
        gh_view_comments_fn=gh_view,
        commit_state_fn=_wrap_commit(project_dir),
        memory_dir=memory_dir,
        now=fixed,
    )
    result = run_close(req)

    assert result.escalated_slugs == ["global"]
    assert (memory_dir / "reference_global.md").exists()
    assert not (memory_dir / "reference_issue-only.md").exists()


def test_run_close_skips_when_zero_decisions(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """T-115: scenario row 6 — 0 candidates → no summary, no escalation."""
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    gh_post = gh_post_fn_factory(results=[])
    gh_view = gh_view_comments_fn_factory(results=[[]])
    memory_dir = project_dir / "memory"

    req = CloseRequest(
        project_dir=project_dir,
        session_id="sess-115",
        issue_number=42,
        user_decisions=[],
        transcript_end_offset=512,
        extracted_candidate_count=0,
        gh_post_fn=gh_post,
        gh_view_comments_fn=gh_view,
        commit_state_fn=_wrap_commit(project_dir),
        memory_dir=memory_dir,
        now=fixed,
    )
    result = run_close(req)

    assert result.summary_posted is False
    assert result.escalated_slugs == []
    assert gh_post.calls == []  # type: ignore[attr-defined]
    if memory_dir.exists():
        assert list(memory_dir.iterdir()) == []
    state = _read_state(project_dir, "sess-115")
    assert state["skill_ran_at"] == fixed.isoformat()


def test_user_decision_overrides_scope_hint(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """T-116: scope_hint=cross-issue but final_scope=issue → no memory write."""
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    cand = Candidate(
        slug="hinted",
        what="x",
        why="y",
        alternatives="z",
        consequences="c",
        scope_hint="cross-issue",
    )
    user_decisions = [UserDecision(candidate=cand, final_scope="issue")]
    gh_post = gh_post_fn_factory(
        results=[_ok_post("hinted"), _ok_post("summary")]
    )
    gh_view = gh_view_comments_fn_factory(results=[[]])
    memory_dir = project_dir / "memory"

    req = CloseRequest(
        project_dir=project_dir,
        session_id="sess-116",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        gh_view_comments_fn=gh_view,
        commit_state_fn=_wrap_commit(project_dir),
        memory_dir=memory_dir,
        now=fixed,
    )
    result = run_close(req)

    assert result.escalated_slugs == []
    assert not (memory_dir / "reference_hinted.md").exists()


def test_run_capture_rejected_candidates_skipped(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-117: rejected candidates never reach gh_post.

    SKILL.md filters rejections out of ``user_decisions`` before passing
    to the orchestrator; orchestrator's job is to behave as if they
    never existed. With 5 extracted candidates but only 2 user_decisions,
    gh_post must be called exactly twice.
    """
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [_make_user_decision("kept-1"), _make_user_decision("kept-2")]
    gh_post = gh_post_fn_factory(
        results=[_ok_post("kept-1"), _ok_post("kept-2")]
    )

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-117",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=5,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        now=fixed,
    )
    result = run_capture(req)

    assert result.posted_slugs == ["kept-1", "kept-2"]
    assert len(gh_post.calls) == 2  # type: ignore[attr-defined]
    state = _read_state(project_dir, "sess-117")
    assert state["captured_slugs"] == ["kept-1", "kept-2"]


def test_run_capture_all_rejected_only_skill_ran_at(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-118: scenario row 5 — all rejected → skill_ran_at only.

    Crucially: ``last_processed_offset`` must NOT be written so the next
    invocation can re-extract from the same range (the user may change
    their mind).
    """
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    gh_post = gh_post_fn_factory(results=[])

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-118",
        issue_number=42,
        user_decisions=[],
        transcript_end_offset=4096,
        extracted_candidate_count=5,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        now=fixed,
    )
    run_capture(req)

    state = _read_state(project_dir, "sess-118")
    assert state["skill_ran_at"] == fixed.isoformat()
    assert "last_processed_offset" not in state
    assert "captured_slugs" not in state or state["captured_slugs"] == []
    assert gh_post.calls == []  # type: ignore[attr-defined]


def test_dedup_local_excludes_captured(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-119: captured_slugs from prior state aren't re-posted.

    SKILL.md does the dedup before invoking the orchestrator, so the
    contract here is: orchestrator must *append* the new successful
    slugs to the existing ``captured_slugs`` (not replace), preserving
    the persistence invariant.
    """
    from issueops.session_closer import CaptureRequest, run_capture

    # Pre-create state with some prior captures.
    state_path = project_dir / "session-state" / "sess-119.json"
    state_path.write_text(
        json.dumps(
            {
                "session_id": "sess-119",
                "captured_slugs": ["prior-1", "prior-2"],
            }
        )
    )

    fixed = freeze_now()
    user_decisions = [_make_user_decision("new-1")]
    gh_post = gh_post_fn_factory(results=[_ok_post("new-1")])

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-119",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        now=fixed,
    )
    run_capture(req)

    state = _read_state(project_dir, "sess-119")
    assert state["captured_slugs"] == ["prior-1", "prior-2", "new-1"]


def test_dedup_remote_uses_marker_parser(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-120: ``filter_remote`` integration via decisions injected by SKILL.md.

    SKILL.md parses the gh-view output through marker_parser and passes
    the resulting Decision[] to the orchestrator-or-its-helper. Here we
    verify the orchestrator itself respects the
    ``existing_remote_decisions`` field by skipping any user_decision
    whose slug appears among them.
    """
    from issueops.marker_parser import Decision
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [
        _make_user_decision("dup"),
        _make_user_decision("fresh"),
    ]
    # Only one gh_post call is expected, for "fresh"; "dup" is filtered.
    gh_post = gh_post_fn_factory(results=[_ok_post("fresh")])

    existing = [
        Decision(slug="dup", what="x", why="y", alternatives="z", consequences="c")
    ]

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-120",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=2,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        existing_remote_decisions=existing,
        now=fixed,
    )
    result = run_capture(req)

    assert result.posted_slugs == ["fresh"]
    assert len(gh_post.calls) == 1  # type: ignore[attr-defined]


def test_dedup_gh_failure_falls_back_tier1_only(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-121: tier2_skipped_kind=auth bypasses remote dedup but still posts.

    Equivalent to scenario row 1 except a warning is emitted recording
    that Tier 2 was skipped due to gh failure.
    """
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [_make_user_decision("a")]
    gh_post = gh_post_fn_factory(results=[_ok_post("a")])

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-121",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        tier2_skipped_kind=GhFailureKind.AUTH,
        now=fixed,
    )
    result = run_capture(req)

    assert result.posted_slugs == ["a"]
    assert any("auth" in w.lower() and "tier2" in w.lower() for w in result.warnings)


def test_run_capture_issue_resolution_error(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-122: scenario row 8 — issue not resolvable → skill_ran_at only.

    Modeled by passing ``issue_resolution_failed=True`` (SKILL.md sets
    this when bin's ``resolve-issue`` returned ``ok=false``).
    """
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    gh_post = gh_post_fn_factory(results=[])

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-122",
        issue_number=0,
        user_decisions=[],
        transcript_end_offset=10,
        extracted_candidate_count=0,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        issue_resolution_failed=True,
        now=fixed,
    )
    run_capture(req)

    state = _read_state(project_dir, "sess-122")
    assert state["skill_ran_at"] == fixed.isoformat()
    assert "last_processed_offset" not in state
    assert "captured_slugs" not in state or state["captured_slugs"] == []
    assert gh_post.calls == []  # type: ignore[attr-defined]


def test_run_capture_state_merge_preserves_siblings(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-123: orchestrator preserves briefing_done / pending_restore."""
    from issueops.session_closer import CaptureRequest, run_capture

    state_path = project_dir / "session-state" / "sess-123.json"
    state_path.write_text(
        json.dumps(
            {
                "session_id": "sess-123",
                "briefing_done": True,
                "pending_restore": {"issue_number": 99},
                "last_summary_at": "2026-04-24T09:00:00+00:00",
            }
        )
    )

    fixed = freeze_now()
    user_decisions = [_make_user_decision("only")]
    gh_post = gh_post_fn_factory(results=[_ok_post("only")])

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-123",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        now=fixed,
    )
    run_capture(req)

    state = _read_state(project_dir, "sess-123")
    assert state["briefing_done"] is True
    assert state["pending_restore"] == {"issue_number": 99}
    assert state["last_summary_at"] == "2026-04-24T09:00:00+00:00"
    assert state["captured_slugs"] == ["only"]


def test_run_capture_handles_corrupt_state(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-124: corrupt state file → quarantine + new state with patch only.

    The orchestrator delegates to ``commit_state_fn`` which (when wrapping
    ``state_writer.merge_update_state``) handles quarantine internally;
    we just verify the end state is sane and a quarantine sibling exists.
    """
    from issueops.session_closer import CaptureRequest, run_capture

    state_path = project_dir / "session-state" / "sess-124.json"
    state_path.write_text("{ broken json")

    fixed = freeze_now()
    user_decisions = [_make_user_decision("only")]
    gh_post = gh_post_fn_factory(results=[_ok_post("only")])

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-124",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        now=fixed,
    )
    run_capture(req)

    state = _read_state(project_dir, "sess-124")
    assert state["captured_slugs"] == ["only"]
    siblings = list((project_dir / "session-state").iterdir())
    quarantines = [p for p in siblings if ".corrupt-" in p.name]
    assert len(quarantines) == 1


def test_run_close_calls_write_memory_file_for_cross_issue(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """T-125: cross-issue posted decisions trigger write_memory_file."""
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    user_decisions = [_make_user_decision("global", final_scope="cross-issue")]
    gh_post = gh_post_fn_factory(
        results=[_ok_post("global"), _ok_post("summary")]
    )
    gh_view = gh_view_comments_fn_factory(results=[[]])
    memory_dir = project_dir / "memory"

    req = CloseRequest(
        project_dir=project_dir,
        session_id="sess-125",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        gh_view_comments_fn=gh_view,
        commit_state_fn=_wrap_commit(project_dir),
        memory_dir=memory_dir,
        now=fixed,
    )
    run_close(req)

    target = memory_dir / "reference_global.md"
    assert target.exists()
    body = target.read_text()
    assert "name: global" in body
    assert "**What:** do global" in body


def test_run_close_calls_update_memory_index_idempotent(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """T-126: MEMORY.md gets exactly one index line per slug across two runs."""
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    user_decisions = [_make_user_decision("global", final_scope="cross-issue")]
    memory_dir = project_dir / "memory"

    # Two consecutive close runs with the same cross-issue slug.
    for sid in ("sess-126a", "sess-126b"):
        gh_post = gh_post_fn_factory(
            results=[_ok_post("global"), _ok_post("summary")]
        )
        gh_view = gh_view_comments_fn_factory(results=[[]])
        req = CloseRequest(
            project_dir=project_dir,
            session_id=sid,
            issue_number=42,
            user_decisions=user_decisions,
            transcript_end_offset=10,
            extracted_candidate_count=1,
            gh_post_fn=gh_post,
            gh_view_comments_fn=gh_view,
            commit_state_fn=_wrap_commit(project_dir),
            memory_dir=memory_dir,
            now=fixed,
        )
        run_close(req)

    index = (memory_dir / "MEMORY.md").read_text()
    assert index.count("reference_global.md") == 1


def test_run_close_skips_memory_for_issue_scope(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """T-127: pure issue-scope run never creates the memory dir.

    This is a hard assertion: not just "no reference file", but "no
    memory dir touched at all" — verifies the orchestrator branches on
    cross-issue presence before invoking memory_escalate.
    """
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    user_decisions = [_make_user_decision("only", final_scope="issue")]
    gh_post = gh_post_fn_factory(
        results=[_ok_post("only"), _ok_post("summary")]
    )
    gh_view = gh_view_comments_fn_factory(results=[[]])
    memory_dir = project_dir / "memory"

    req = CloseRequest(
        project_dir=project_dir,
        session_id="sess-127",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        gh_view_comments_fn=gh_view,
        commit_state_fn=_wrap_commit(project_dir),
        memory_dir=memory_dir,
        now=fixed,
    )
    result = run_close(req)

    assert result.escalated_slugs == []
    assert not memory_dir.exists()


def test_run_close_memory_failure_keeps_posted(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """T-128: memory write exception → warning, posted_slugs untouched."""
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    user_decisions = [_make_user_decision("global", final_scope="cross-issue")]
    gh_post = gh_post_fn_factory(
        results=[_ok_post("global"), _ok_post("summary")]
    )
    gh_view = gh_view_comments_fn_factory(results=[[]])
    memory_dir = project_dir / "memory"

    def boom_write_memory(decision, mdir):
        raise OSError("disk full")

    req = CloseRequest(
        project_dir=project_dir,
        session_id="sess-128",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        gh_view_comments_fn=gh_view,
        commit_state_fn=_wrap_commit(project_dir),
        memory_dir=memory_dir,
        write_memory_fn=boom_write_memory,
        now=fixed,
    )
    result = run_close(req)

    assert result.capture.posted_slugs == ["global"]
    assert any("memory" in w.lower() for w in result.warnings)


def test_run_capture_save_pending_on_failure(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-129: scenario row 2 — failure_choice='save' writes pending JSON."""
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [_make_user_decision("a"), _make_user_decision("b")]
    gh_post = gh_post_fn_factory(
        results=[_ok_post("a"), _fail_post(GhFailureKind.AUTH)]
    )

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-129",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=2,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        failure_choice="save",
        now=fixed,
    )
    run_capture(req)

    pending = project_dir / "session-state" / "sess-129.pending-decisions.json"
    assert pending.exists()
    data = json.loads(pending.read_text())
    assert data["schema_version"] == 1
    assert data["session_id"] == "sess-129"
    assert data["issue_number"] == 42
    assert len(data["entries"]) == 1
    saved_slugs = [d["slug"] for d in data["entries"][0]["decisions"]]
    assert saved_slugs == ["b"]


def test_run_capture_discard_on_failure(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-130: scenario row 3 — failure_choice='discard' writes no pending."""
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [_make_user_decision("a"), _make_user_decision("b")]
    gh_post = gh_post_fn_factory(
        results=[_ok_post("a"), _fail_post(GhFailureKind.NETWORK)]
    )

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-130",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=2,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        failure_choice="discard",
        now=fixed,
    )
    run_capture(req)

    pending = project_dir / "session-state" / "sess-130.pending-decisions.json"
    assert not pending.exists()
    state = _read_state(project_dir, "sess-130")
    # captured success only, offset still committed (row 3)
    assert state["captured_slugs"] == ["a"]
    assert state["last_processed_offset"] == 10


def test_run_capture_abort_on_failure(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-131: scenario row 4 — failure_choice='abort' → no offset write."""
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    user_decisions = [_make_user_decision("a"), _make_user_decision("b")]
    gh_post = gh_post_fn_factory(
        results=[_ok_post("a"), _fail_post(GhFailureKind.NETWORK)]
    )

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-131",
        issue_number=42,
        user_decisions=user_decisions,
        transcript_end_offset=10,
        extracted_candidate_count=2,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        failure_choice="abort",
        now=fixed,
    )
    run_capture(req)

    pending = project_dir / "session-state" / "sess-131.pending-decisions.json"
    assert not pending.exists()
    state = _read_state(project_dir, "sess-131")
    assert state["skill_ran_at"] == fixed.isoformat()
    assert state["captured_slugs"] == ["a"]
    assert "last_processed_offset" not in state


def test_pending_decisions_append_existing_file(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-134: a second 'save' run appends a new entry to existing pending."""
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()

    # First run — one failure to seed pending file.
    gh_post1 = gh_post_fn_factory(results=[_fail_post()])
    req1 = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-134",
        issue_number=42,
        user_decisions=[_make_user_decision("first")],
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post1,
        commit_state_fn=_wrap_commit(project_dir),
        failure_choice="save",
        now=fixed,
    )
    run_capture(req1)

    # Second run — different failure, must append (not replace).
    gh_post2 = gh_post_fn_factory(results=[_fail_post()])
    req2 = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-134",
        issue_number=42,
        user_decisions=[_make_user_decision("second")],
        transcript_end_offset=20,
        extracted_candidate_count=1,
        gh_post_fn=gh_post2,
        commit_state_fn=_wrap_commit(project_dir),
        failure_choice="save",
        now=fixed,
    )
    run_capture(req2)

    pending = project_dir / "session-state" / "sess-134.pending-decisions.json"
    data = json.loads(pending.read_text())
    assert len(data["entries"]) == 2
    assert [d["slug"] for d in data["entries"][0]["decisions"]] == ["first"]
    assert [d["slug"] for d in data["entries"][1]["decisions"]] == ["second"]


def test_pending_decisions_issue_number_mismatch_defensive(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-135: a 'save' with a different issue_number still appends defensively.

    Per design.md § Component 7 a single session is not expected to post
    to multiple issues, but the implementation must defensively tolerate
    it (no crash) — the new entry is appended and a warning surfaced.
    """
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()

    # Seed pending file under issue_number=42.
    req1 = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-135",
        issue_number=42,
        user_decisions=[_make_user_decision("a")],
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post_fn_factory(results=[_fail_post()]),
        commit_state_fn=_wrap_commit(project_dir),
        failure_choice="save",
        now=fixed,
    )
    run_capture(req1)

    # Now save under a *different* issue_number=99.
    req2 = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-135",
        issue_number=99,
        user_decisions=[_make_user_decision("b")],
        transcript_end_offset=20,
        extracted_candidate_count=1,
        gh_post_fn=gh_post_fn_factory(results=[_fail_post()]),
        commit_state_fn=_wrap_commit(project_dir),
        failure_choice="save",
        now=fixed,
    )
    result = run_capture(req2)

    pending = project_dir / "session-state" / "sess-135.pending-decisions.json"
    data = json.loads(pending.read_text())
    assert len(data["entries"]) == 2
    # T-135: pin to the exact warning the implementation emits, so the
    # assertion does not silently pass when the warning is reworded or
    # an unrelated warning containing "issue" leaks in (#31).
    assert any("issue_number mismatch" in w for w in result.warnings)


def test_run_capture_post_without_commit_keeps_state(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """T-136: subcommand-separation contract.

    Setup:
        - Pre-create state file with a known snapshot.
        - ``gh_post_fn`` succeeds for one decision.
        - ``commit_state_fn`` raises ``RuntimeError`` (simulating a SKILL
          ↔ bin handoff failure between post-decisions and commit-state).

    Assert (all three):
        1. ``gh_post`` *was* called (post-decisions ran independently).
        2. State file content equals the pre-snapshot byte-for-byte.
        3. No ``<sid>.json.tmp.*`` residue files in session-state dir.
    """
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()

    state_path = project_dir / "session-state" / "sess-136.json"
    snapshot = json.dumps(
        {
            "session_id": "sess-136",
            "skill_ran_at": "2026-04-24T09:00:00+00:00",
            "captured_slugs": ["pre-existing"],
            "last_processed_offset": 7,
        },
        indent=2,
    )
    state_path.write_text(snapshot)
    snapshot_bytes = state_path.read_bytes()

    gh_post = gh_post_fn_factory(results=[_ok_post("only")])

    def commit_explodes(**_kwargs):
        raise RuntimeError("commit boom")

    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-136",
        issue_number=42,
        user_decisions=[_make_user_decision("only")],
        transcript_end_offset=999,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        commit_state_fn=commit_explodes,
        now=fixed,
    )
    with pytest.raises(RuntimeError, match="commit boom"):
        run_capture(req)

    # (1) gh_post was called
    assert len(gh_post.calls) == 1  # type: ignore[attr-defined]

    # (2) state file unchanged byte-for-byte
    assert state_path.read_bytes() == snapshot_bytes

    # (3) no tmp residue
    siblings = list((project_dir / "session-state").iterdir())
    tmps = [p for p in siblings if ".tmp." in p.name]
    assert tmps == []


# ---------------------------------------------------------------------------
# #31: targeted coverage for previously-untested branches in session_closer.py
# ---------------------------------------------------------------------------


def test_run_capture_existing_state_file_is_json_list_falls_back_to_empty(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """Cover ``_read_existing_captured_slugs`` when state file's JSON
    root is a list (not a dict). Should silently degrade to ``[]`` so the
    new run can still capture without crashing on malformed prior state.
    """
    from issueops.path_utils import state_file_path
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    state_file_path(project_dir, "sess-cov-418").write_text(
        "[1, 2, 3]", encoding="utf-8"
    )

    gh_post = gh_post_fn_factory(results=[_ok_post("alpha")])
    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-cov-418",
        issue_number=42,
        user_decisions=[_make_user_decision("alpha")],
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        now=fixed,
    )
    result = run_capture(req)

    # The new slug is captured; the malformed prior is treated as no
    # prior (the list root contributes nothing).
    assert result.posted_slugs == ["alpha"]
    state = _read_state(project_dir, "sess-cov-418")
    assert state["captured_slugs"] == ["alpha"]


def test_run_capture_swallows_prior_pending_parse_error(
    project_dir: Path, gh_post_fn_factory, freeze_now
):
    """Cover the prior-pending read ``except Exception`` path: a malformed
    pending file must not crash the pre-read (``prior_issue`` defensively
    becomes ``None``).

    Note that ``append_pending_decisions`` itself does NOT tolerate the
    same malformed file (by design — it refuses to silently overwrite the
    user's only record of unposted decisions). So the call still raises,
    but the exception originates from the writer, not from the pre-read.
    What we're locking in here is: the pre-read catches its own parse
    error, no ``issue_number mismatch`` warning is emitted (prior_issue
    is None, not a stale int), and the failure surfaces as a writer
    error rather than as a swallowed corruption.
    """
    from issueops.pending_decisions import pending_path
    from issueops.session_closer import CaptureRequest, run_capture

    fixed = freeze_now()
    pending_path(project_dir, "sess-cov-500").write_text(
        "{not valid json", encoding="utf-8"
    )

    gh_post = gh_post_fn_factory(results=[_fail_post(GhFailureKind.NETWORK)])
    req = CaptureRequest(
        project_dir=project_dir,
        session_id="sess-cov-500",
        issue_number=42,
        user_decisions=[_make_user_decision("a")],
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        commit_state_fn=_wrap_commit(project_dir),
        failure_choice="save",
        now=fixed,
    )
    with pytest.raises(ValueError, match="pending file is not valid JSON"):
        run_capture(req)


def test_run_close_summary_view_failed_when_gh_view_raises(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """Cover the ``view-failed`` summary branch: gh_view_comments raises
    a GhFailure → summary post is *skipped* (we cannot prove idempotency).
    """
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    gh_post = gh_post_fn_factory(results=[_ok_post("a")])
    gh_view = gh_view_comments_fn_factory(
        results=[GhFailure(kind=GhFailureKind.NETWORK, stderr="net", exit_code=1)]
    )

    req = CloseRequest(
        project_dir=project_dir,
        session_id="sess-cov-589",
        issue_number=42,
        user_decisions=[_make_user_decision("a")],
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        gh_view_comments_fn=gh_view,
        commit_state_fn=_wrap_commit(project_dir),
        memory_dir=project_dir / "memory",
        now=fixed,
    )
    result = run_close(req)

    assert result.summary_posted is False
    assert result.summary_skipped_reason == "view-failed"
    # Decision post happened, but no second call for the summary.
    assert len(gh_post.calls) == 1  # type: ignore[attr-defined]


def test_run_close_summary_post_failed_returns_reason(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """Cover the ``post-failed`` summary branch: decision posts succeed
    but the subsequent summary post fails. The summary skipped-reason
    must surface so SKILL.md can warn the user.
    """
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    gh_post = gh_post_fn_factory(
        results=[_ok_post("a"), _fail_post(GhFailureKind.NETWORK)]
    )
    gh_view = gh_view_comments_fn_factory(results=[[]])

    req = CloseRequest(
        project_dir=project_dir,
        session_id="sess-cov-601",
        issue_number=42,
        user_decisions=[_make_user_decision("a")],
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        gh_view_comments_fn=gh_view,
        commit_state_fn=_wrap_commit(project_dir),
        memory_dir=project_dir / "memory",
        now=fixed,
    )
    result = run_close(req)

    assert result.summary_posted is False
    assert result.summary_skipped_reason == "post-failed"


def test_run_close_memory_index_failure_emits_warning(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """Cover the index-failure branch in ``_escalate_cross_issue``: the
    write succeeds but the index update raises → warning, no crash, the
    slug is not counted as escalated.
    """
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    gh_post = gh_post_fn_factory(
        results=[_ok_post("global"), _ok_post("summary")]
    )
    gh_view = gh_view_comments_fn_factory(results=[[]])
    memory_dir = project_dir / "memory"

    def write_ok(_decision, _mdir):
        return None

    def index_boom(_mdir, _decision):
        raise RuntimeError("index boom")

    req = CloseRequest(
        project_dir=project_dir,
        session_id="sess-cov-634",
        issue_number=42,
        user_decisions=[_make_user_decision("global", final_scope="cross-issue")],
        transcript_end_offset=10,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        gh_view_comments_fn=gh_view,
        commit_state_fn=_wrap_commit(project_dir),
        memory_dir=memory_dir,
        write_memory_fn=write_ok,
        update_index_fn=index_boom,
        now=fixed,
    )
    result = run_close(req)

    assert result.escalated_slugs == []
    assert any("memory index update failed" in w for w in result.warnings)


def test_post_decisions_batch_swallows_exception_per_decision(
    gh_post_fn_factory,
):
    """#30, M-2: a raised exception from the gh wrapper must not abort
    the batch — the rest of the decisions still get a chance to post,
    and the failure surfaces with ``exception_text`` instead of a
    classified GhFailure.
    """
    from issueops.session_closer import post_decisions_batch

    raising = gh_post_fn_factory(
        results=[
            _ok_post("a"),
            RuntimeError("boom"),
            _ok_post("c"),
        ]
    )

    batch = post_decisions_batch(
        42,
        [_make_user_decision(s) for s in ("a", "b", "c")],
        raising,
    )

    assert [ud.candidate.slug for ud in batch.posted] == ["a", "c"]
    assert len(batch.failed) == 1
    failed = batch.failed[0]
    assert failed.decision.candidate.slug == "b"
    assert failed.failure is None
    assert failed.exception_text is not None
    assert "RuntimeError" in failed.exception_text
    assert "boom" in failed.exception_text


def test_run_close_short_circuits_when_capture_aborted(
    project_dir: Path, gh_post_fn_factory, gh_view_comments_fn_factory, freeze_now
):
    """Cover the ``capture.aborted`` short-circuit in run_close: when the
    capture flow aborts (e.g. transcript_missing), run_close must not
    attempt summary or escalation, and ``summary_skipped_reason`` is
    pinned to ``aborted``.
    """
    from issueops.session_closer import CloseRequest, run_close

    fixed = freeze_now()
    gh_post = gh_post_fn_factory(results=[])
    gh_view = gh_view_comments_fn_factory(results=[])

    req = CloseRequest(
        project_dir=project_dir,
        session_id="sess-cov-669",
        issue_number=42,
        user_decisions=[_make_user_decision("a")],
        transcript_end_offset=0,
        extracted_candidate_count=1,
        gh_post_fn=gh_post,
        gh_view_comments_fn=gh_view,
        commit_state_fn=_wrap_commit(project_dir),
        memory_dir=project_dir / "memory",
        transcript_missing=True,
        now=fixed,
    )
    result = run_close(req)

    assert result.capture.aborted is True
    assert result.summary_posted is False
    assert result.summary_skipped_reason == "aborted"
    assert result.escalated_slugs == []
    # Neither post nor view was called — short-circuit honoured.
    assert gh_post.calls == []  # type: ignore[attr-defined]
    assert gh_view.calls == []  # type: ignore[attr-defined]
