"""Bin-level dispatch tests for ``bin/session_closer.py``.

The bin file is the JSON-in/JSON-out adapter that Claude Code's skill
calls. Per design.md it must:

- Always exit 0 and emit a single parseable JSON document on stdout —
  even on failure (the skill needs the structured error to render the
  3-choice dialog).
- Validate ``schema_version`` and ``subcommand`` before any
  payload-specific parsing so a stale skill client gets the same
  ``internal`` error regardless of which subcommand it called.
- Translate ``FileNotFoundError`` from ``read-transcript`` to the
  ``transcript-missing`` error kind that SKILL.md routes specially.
- Never abort on a single per-decision gh failure inside
  ``post-decisions`` (R-9 graceful degradation).

These tests load the bin module via importlib (so the
``sys.path.insert`` at the top of the file does not contaminate other
test modules) and call the handlers / dispatcher directly, monkey-patching
the gh adapters so no real subprocess fires.

This file plugs the 0% coverage gap on ``bin/session_closer.py`` flagged
in the post-#8 team review (#31).
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from issueops.gh_adapters import GhFailure, GhFailureKind, PostResult


_REPO_ROOT = Path(__file__).resolve().parent.parent
_BIN_FILE = _REPO_ROOT / "bin" / "session_closer.py"


@pytest.fixture(scope="module")
def bin_mod():
    """Import ``bin/session_closer.py`` once per module via importlib.

    A regular ``import`` won't work because ``bin/`` is not on
    ``sys.path``. We load by file path and register under a unique
    module name so the import is repeatable.
    """
    spec = importlib.util.spec_from_file_location(
        "_test_bin_session_closer", _BIN_FILE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ok_post(slug: str) -> PostResult:
    return PostResult(
        ok=True,
        comment_url=f"https://github.com/x/y/issues/1#c-{slug}",
        failure=None,
    )


def _fail_post(kind: GhFailureKind = GhFailureKind.NETWORK) -> PostResult:
    return PostResult(
        ok=False,
        comment_url=None,
        failure=GhFailure(kind=kind, stderr="boom", exit_code=1, hint=None),
    )


def _candidate_payload(slug: str = "alpha") -> dict[str, Any]:
    return {
        "slug": slug,
        "what": f"do {slug}",
        "why": f"because {slug}",
        "alternatives": f"not {slug}",
        "consequences": f"{slug} happens",
        "scope_hint": "issue",
    }


def _user_decision_payload(slug: str = "alpha") -> dict[str, Any]:
    return {
        "candidate": _candidate_payload(slug),
        "final_scope": "issue",
    }


# ---------------------------------------------------------------------------
# Dispatcher: schema / subcommand / payload validation (always exit 0,
# always JSON, kind="internal" on validation failure).
# ---------------------------------------------------------------------------


def test_dispatch_rejects_schema_version_mismatch(bin_mod):
    response = bin_mod._dispatch({"schema_version": 99, "subcommand": "summary"})
    assert response["ok"] is False
    assert response["error"]["kind"] == "internal"
    assert "schema_version" in response["error"]["message"]


def test_dispatch_rejects_unknown_subcommand(bin_mod):
    response = bin_mod._dispatch(
        {"schema_version": bin_mod.SCHEMA_VERSION, "subcommand": "no-such-cmd"}
    )
    assert response["ok"] is False
    assert response["error"]["kind"] == "internal"
    assert "no-such-cmd" in response["error"]["message"]


def test_dispatch_translates_keyerror_to_internal(bin_mod):
    """A handler missing a required field should surface as ``internal``
    (not as an unhandled ``KeyError`` that strands the skill)."""
    # Flat envelope: subcommand-specific fields sit alongside
    # schema_version/subcommand. ``commit-state`` requires session_id
    # and patch — omitting both should produce an ``internal`` error.
    response = bin_mod._dispatch(
        {
            "schema_version": bin_mod.SCHEMA_VERSION,
            "subcommand": "commit-state",
        }
    )
    assert response["ok"] is False
    assert response["error"]["kind"] == "internal"


# ---------------------------------------------------------------------------
# read-transcript: success + transcript-missing routing.
# ---------------------------------------------------------------------------


def test_handle_read_transcript_returns_content_and_offset(
    tmp_path: Path, bin_mod
):
    transcript = tmp_path / "session.transcript"
    transcript.write_text("line1\nline2\nline3\n", encoding="utf-8")

    response = bin_mod._handle_read_transcript(
        {"transcript_path": str(transcript), "offset": 0}
    )

    assert response["ok"] is True
    assert "line1" in response["result"]["content"]
    assert response["result"]["end_offset"] == transcript.stat().st_size


def test_handle_read_transcript_missing_routes_to_transcript_missing(
    tmp_path: Path, bin_mod
):
    bogus = tmp_path / "does-not-exist.transcript"

    response = bin_mod._dispatch(
        {
            "schema_version": bin_mod.SCHEMA_VERSION,
            "subcommand": "read-transcript",
            "transcript_path": str(bogus),
            "offset": 0,
        }
    )

    assert response["ok"] is False
    assert response["error"]["kind"] == "transcript-missing"


# ---------------------------------------------------------------------------
# resolve-issue: integration with branch + in-progress fallback.
# ---------------------------------------------------------------------------


def test_handle_resolve_issue_uses_branch_number(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    # Tier 1 (in-progress list) is consulted, but the branch number wins
    # when present. Empty in-progress list means Tier 2 (branch) decides.
    monkeypatch.setattr(bin_mod, "gh_list_in_progress", lambda **_kw: [])

    response = bin_mod._handle_resolve_issue(
        {"branch": "feat/77-something", "project_dir": str(tmp_path)}
    )
    assert response["ok"] is True
    assert response["result"]["issue_number"] == 77


def test_handle_resolve_issue_ambiguous_returns_candidates(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(bin_mod, "gh_list_in_progress", lambda **_kw: [10, 20])

    response = bin_mod._handle_resolve_issue(
        {"branch": "master", "project_dir": str(tmp_path)}
    )
    assert response["ok"] is True
    # Spec contract: single ``ambiguous_candidates`` field carries both
    # the signal and the candidate list (#29).
    assert sorted(response["result"]["ambiguous_candidates"]) == [10, 20]
    assert "ambiguous" not in response["result"]
    assert "candidates" not in response["result"]


def test_handle_resolve_issue_returns_error_when_unresolvable(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(bin_mod, "gh_list_in_progress", lambda **_kw: [])
    response = bin_mod._handle_resolve_issue(
        {"branch": "master", "project_dir": str(tmp_path)}
    )
    assert response["ok"] is False
    assert response["error"]["kind"] == "issue-resolution"


# ---------------------------------------------------------------------------
# resolve-issue: --target unified flag (Story 2 #76, Epic 01)
# ---------------------------------------------------------------------------
#
# Story 1 (#77) added the pure-module primitives:
#   parse_target_spec / resolve_meta_target / TargetSpec / TargetResolutionError
# Story 2 wires them through bin/session_closer.py:_handle_resolve_issue so
# a `target` payload field activates a new code path. ``target`` absent
# keeps the existing Tier 1/2 behaviour (backward compatibility, AC-4).


def test_handle_resolve_issue_target_meta_single_returns_issue_number(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    """target=meta + Meta list 1 件 → 即採用、issue_number を返す。"""
    monkeypatch.setattr(bin_mod, "gh_list_meta_issues", lambda **_kw: [69])
    # Tier 1 fallback shouldn't be consulted when target is supplied.
    monkeypatch.setattr(
        bin_mod, "gh_list_in_progress",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    response = bin_mod._handle_resolve_issue(
        {"target": "meta", "branch": "master", "project_dir": str(tmp_path)}
    )

    assert response["ok"] is True
    assert response["result"]["issue_number"] == 69


def test_handle_resolve_issue_target_meta_multiple_returns_ambiguous(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    """target=meta + Meta list 複数 → ambiguous_candidates、SKILL.md が AskUserQuestion。"""
    monkeypatch.setattr(bin_mod, "gh_list_meta_issues", lambda **_kw: [69, 75])

    response = bin_mod._handle_resolve_issue(
        {"target": "meta", "project_dir": str(tmp_path)}
    )

    assert response["ok"] is True
    assert sorted(response["result"]["ambiguous_candidates"]) == [69, 75]
    assert "issue_number" not in response["result"]


def test_handle_resolve_issue_target_meta_empty_returns_target_resolution_error(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    """target=meta + 0 件 → 厳格 error kind=target-resolution (F1)。"""
    monkeypatch.setattr(bin_mod, "gh_list_meta_issues", lambda **_kw: [])

    response = bin_mod._handle_resolve_issue(
        {"target": "meta", "project_dir": str(tmp_path)}
    )

    assert response["ok"] is False
    assert response["error"]["kind"] == "target-resolution"
    # hint should help the user recover.
    assert response["error"].get("hint")


def test_handle_resolve_issue_target_issue_returns_value_directly(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    """target=issue:42 → issue_number=42、gh CLI を一切叩かない。"""
    monkeypatch.setattr(
        bin_mod, "gh_list_meta_issues",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    monkeypatch.setattr(
        bin_mod, "gh_list_in_progress",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    response = bin_mod._handle_resolve_issue(
        {"target": "issue:42", "project_dir": str(tmp_path)}
    )

    assert response["ok"] is True
    assert response["result"]["issue_number"] == 42


@pytest.mark.parametrize(
    "bad",
    ["", "invalid", "meta:42", "issue:", "issue:abc", "issue:0", "story:42", "epic:42"],
)
def test_handle_resolve_issue_invalid_target_spec_is_classified(
    tmp_path: Path, bin_mod, bad: str
):
    """target syntax 不正 → ok=false, error.kind=invalid-target-spec。

    SKILL.md 側で先に validate されるべきだが、防御的に bin でも検出して
    `internal` ではなく専用 kind に分類する (Living Design Doc § 3)。
    """
    response = bin_mod._handle_resolve_issue(
        {"target": bad, "project_dir": str(tmp_path)}
    )

    assert response["ok"] is False
    assert response["error"]["kind"] == "invalid-target-spec"


def test_handle_resolve_issue_target_meta_gh_failure_classified(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    """gh_list_meta_issues が GhFailure を raise → dispatcher で gh-failure kind に変換。

    Story 2 自身は GhFailure を catch しない (orchestrator-style propagation)。
    dispatcher の汎用 `except GhFailure` 経路でユーザーに auth/network/rate-limit
    が伝わることを E2E で pin する。
    """
    def _boom(**_kw):
        raise GhFailure(
            kind=GhFailureKind.AUTH,
            stderr="bad credentials",
            exit_code=1,
            hint="gh auth status を実行してください",
        )

    monkeypatch.setattr(bin_mod, "gh_list_meta_issues", _boom)

    response = bin_mod._dispatch(
        {
            "schema_version": 1,
            "subcommand": "resolve-issue",
            "target": "meta",
            "project_dir": str(tmp_path),
        }
    )

    assert response["ok"] is False
    assert response["error"]["kind"] == "gh-failure"
    assert response["error"]["gh_failure_kind"] == "auth"


def test_handle_resolve_issue_without_target_keeps_tier12_behavior(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    """target 未指定なら Story 1 以前の挙動 (Tier 1/2) を完全に維持する。"""
    monkeypatch.setattr(bin_mod, "gh_list_in_progress", lambda **_kw: [99])
    # gh_list_meta_issues must NOT be called when target is absent.
    monkeypatch.setattr(
        bin_mod, "gh_list_meta_issues",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    response = bin_mod._handle_resolve_issue(
        {"branch": "master", "project_dir": str(tmp_path)}
    )

    assert response["ok"] is True
    assert response["result"]["issue_number"] == 99


# ---------------------------------------------------------------------------
# post-decisions: graceful degradation on per-decision failures.
# ---------------------------------------------------------------------------


def test_handle_post_decisions_partial_failure_records_each(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    queue = [_ok_post("a"), _fail_post(GhFailureKind.AUTH), _ok_post("c")]

    def fake_post(issue, body, *, cwd):
        return queue.pop(0)

    monkeypatch.setattr(bin_mod, "gh_post_comment", fake_post)

    response = bin_mod._handle_post_decisions(
        {
            "issue_number": 99,
            "project_dir": str(tmp_path),
            "decisions": [
                _user_decision_payload("a"),
                _user_decision_payload("b"),
                _user_decision_payload("c"),
            ],
        }
    )

    assert response["ok"] is True
    res = response["result"]
    assert res["posted"] == ["a", "c"]
    # The single failure should appear with its kind, not abort the loop.
    assert len(res["failed"]) == 1
    assert res["failed"][0]["slug"] == "b"
    assert res["failed"][0]["kind"] == GhFailureKind.AUTH.value
    # The last failure surfaces gh_failure_kind for SKILL.md's dialog.
    assert res["gh_failure_kind"] == GhFailureKind.AUTH.value


# ---------------------------------------------------------------------------
# filter-dedup: gh failure during Tier 2 is reported but doesn't abort.
# ---------------------------------------------------------------------------


def test_handle_filter_dedup_records_tier2_skip_kind(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    def boom(issue, *, cwd):
        raise GhFailure(
            kind=GhFailureKind.RATE_LIMIT, stderr="rate", exit_code=1
        )

    monkeypatch.setattr(bin_mod, "gh_view_comments", boom)

    response = bin_mod._handle_filter_dedup(
        {
            "issue_number": 1,
            "project_dir": str(tmp_path),
            "candidates": [_candidate_payload("alpha")],
            "captured_slugs": [],
        }
    )

    assert response["ok"] is True
    assert response["result"]["tier2_skipped_kind"] == GhFailureKind.RATE_LIMIT.value
    # The candidate survives Tier 1 (no local-slug match) and Tier 2 was
    # skipped, so it remains in the output.
    assert len(response["result"]["candidates"]) == 1


# ---------------------------------------------------------------------------
# commit-state: thin wrapper around merge_update_state writes a real file.
# ---------------------------------------------------------------------------


def test_handle_commit_state_writes_state_file(tmp_path: Path, bin_mod):
    response = bin_mod._handle_commit_state(
        {
            "session_id": "sess-bin-cs",
            "patch": {"skill_ran_at": "2026-04-25T12:00:00+00:00"},
            "project_dir": str(tmp_path),
        }
    )

    assert response["ok"] is True
    state_path = Path(response["result"]["state_path"])
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert data["skill_ran_at"] == "2026-04-25T12:00:00+00:00"


def test_handle_commit_state_rejects_non_object_patch(tmp_path: Path, bin_mod):
    response = bin_mod._handle_commit_state(
        {
            "session_id": "sess-bin-cs2",
            "patch": "not-a-dict",
            "project_dir": str(tmp_path),
        }
    )
    assert response["ok"] is False
    assert response["error"]["kind"] == "internal"


# ---------------------------------------------------------------------------
# summary: idempotency + view/post failure routing.
# ---------------------------------------------------------------------------


def test_handle_summary_idempotent_when_marker_already_present(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    sid = "sess-bin-sum-idem"
    marker_body = (
        "<!-- claude-issueops:session-closer:summary:" + sid + " -->\n"
        "## Session summary\n"
    )
    monkeypatch.setattr(
        bin_mod,
        "gh_view_comments",
        lambda issue, *, cwd: [{"body": marker_body}],
    )
    # Post must not be called when idempotent.
    monkeypatch.setattr(
        bin_mod,
        "gh_post_comment",
        lambda *a, **kw: pytest.fail("idempotent path must not post"),
    )

    response = bin_mod._handle_summary(
        {
            "issue_number": 1,
            "session_id": sid,
            "project_dir": str(tmp_path),
        }
    )
    assert response["ok"] is True
    assert response["result"]["posted"] is False
    assert response["result"]["skipped"] == "idempotent"


def test_handle_summary_view_failure_returns_view_failed(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    def view_boom(issue, *, cwd):
        raise GhFailure(kind=GhFailureKind.NETWORK, stderr="net", exit_code=1)

    monkeypatch.setattr(bin_mod, "gh_view_comments", view_boom)

    response = bin_mod._handle_summary(
        {
            "issue_number": 1,
            "session_id": "sess-bin-sum-vf",
            "project_dir": str(tmp_path),
        }
    )
    assert response["ok"] is True
    assert response["result"]["posted"] is False
    assert response["result"]["skipped"] == "view-failed"
    assert response["result"]["gh_failure_kind"] == GhFailureKind.NETWORK.value


def test_handle_summary_posts_when_no_existing_marker(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(bin_mod, "gh_view_comments", lambda issue, *, cwd: [])
    monkeypatch.setattr(
        bin_mod,
        "gh_post_comment",
        lambda issue, body, *, cwd: _ok_post("summary"),
    )

    response = bin_mod._handle_summary(
        {
            "issue_number": 1,
            "session_id": "sess-bin-sum-new",
            "project_dir": str(tmp_path),
        }
    )
    assert response["ok"] is True
    assert response["result"]["posted"] is True
    assert "marker" in response["result"]


def test_handle_summary_renders_captured_slugs_in_posted_body(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    """#64: ``captured_slugs_total`` in the payload must be rendered as a
    ``### Captured decisions`` subsection in the posted body.

    Re-creates the exact repro from the #64 ticket (boatrace-insight
    dogfooding): three slugs in, body must list them. Prior to the fix,
    the bin handler ignored ``captured_slugs_total`` and posted only the
    marker + H2 header.
    """
    monkeypatch.setattr(bin_mod, "gh_view_comments", lambda issue, *, cwd: [])
    posted_bodies: list[str] = []

    def fake_post(issue, body, *, cwd):
        posted_bodies.append(body)
        return _ok_post("summary")

    monkeypatch.setattr(bin_mod, "gh_post_comment", fake_post)

    response = bin_mod._handle_summary(
        {
            "issue_number": 1,
            "session_id": "sess-bin-sum-slugs",
            "project_dir": str(tmp_path),
            "captured_slugs_total": ["slug-a", "slug-b", "slug-c"],
        }
    )

    assert response["ok"] is True
    assert response["result"]["posted"] is True
    assert len(posted_bodies) == 1
    body = posted_bodies[0]
    assert "### Captured decisions (3)" in body
    assert "- slug-a" in body
    assert "- slug-b" in body
    assert "- slug-c" in body


def test_handle_summary_omitting_captured_slugs_posts_marker_only_body(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    """Backward compat: when ``captured_slugs_total`` is absent, the
    posted body equals the marker token (no spurious subsection).
    """
    monkeypatch.setattr(bin_mod, "gh_view_comments", lambda issue, *, cwd: [])
    posted_bodies: list[str] = []

    def fake_post(issue, body, *, cwd):
        posted_bodies.append(body)
        return _ok_post("summary")

    monkeypatch.setattr(bin_mod, "gh_post_comment", fake_post)

    bin_mod._handle_summary(
        {
            "issue_number": 1,
            "session_id": "sess-bin-sum-compat",
            "project_dir": str(tmp_path),
        }
    )
    assert len(posted_bodies) == 1
    body = posted_bodies[0]
    assert "### Captured decisions" not in body
    assert (
        "<!-- claude-issueops:session-closer:summary:sess-bin-sum-compat -->"
        in body
    )


def test_handle_summary_payload_body_overrides_captured_slugs(
    tmp_path: Path, bin_mod, monkeypatch: pytest.MonkeyPatch
):
    """An explicit ``body`` field in the payload wins over the
    auto-rendered body so SKILL.md retains the escape hatch for
    callers that want full control over the comment body.
    """
    monkeypatch.setattr(bin_mod, "gh_view_comments", lambda issue, *, cwd: [])
    posted_bodies: list[str] = []

    def fake_post(issue, body, *, cwd):
        posted_bodies.append(body)
        return _ok_post("summary")

    monkeypatch.setattr(bin_mod, "gh_post_comment", fake_post)

    override = "## Custom summary body"
    bin_mod._handle_summary(
        {
            "issue_number": 1,
            "session_id": "sess-bin-sum-override",
            "project_dir": str(tmp_path),
            "captured_slugs_total": ["should-not-appear"],
            "body": override,
        }
    )
    assert posted_bodies == [override]


# ---------------------------------------------------------------------------
# escalate: only cross-issue decisions touch memory_dir.
# ---------------------------------------------------------------------------


def test_handle_escalate_writes_only_cross_issue(tmp_path: Path, bin_mod):
    memory_dir = tmp_path / "memory"
    cross = _user_decision_payload("global")
    cross["final_scope"] = "cross-issue"
    issue_scope = _user_decision_payload("local")  # final_scope = "issue"

    response = bin_mod._handle_escalate(
        {
            "decisions": [cross, issue_scope],
            "project_memory_dir": str(memory_dir),
        }
    )

    assert response["ok"] is True
    assert response["result"]["written"] == ["global"]
    # Issue-scope decisions must not create files in memory_dir.
    files = [p.name for p in memory_dir.iterdir() if p.is_file()]
    assert not any("local" in f for f in files)


# ---------------------------------------------------------------------------
# save-pending: appends a per-session pending file.
# ---------------------------------------------------------------------------


def test_handle_save_pending_writes_pending_file(tmp_path: Path, bin_mod):
    response = bin_mod._handle_save_pending(
        {
            "session_id": "sess-bin-sp",
            "issue_number": 7,
            "decisions": [_user_decision_payload("a")],
            "project_dir": str(tmp_path),
        }
    )

    assert response["ok"] is True
    pending = Path(response["result"]["pending_path"])
    assert pending.exists()
    data = json.loads(pending.read_text())
    assert data["issue_number"] == 7
    assert len(data["entries"]) == 1


# ---------------------------------------------------------------------------
# main(): JSON-in / JSON-out + invalid stdin handling, exit 0 always.
# ---------------------------------------------------------------------------


def test_main_returns_internal_error_on_invalid_stdin_json(
    bin_mod, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    rc = bin_mod.main()
    assert rc == 0  # exit 0 even on parse failure (skill needs the JSON)
    out = capsys.readouterr().out
    response = json.loads(out)
    assert response["ok"] is False
    assert response["error"]["kind"] == "internal"


def test_main_returns_internal_error_on_non_object_stdin(
    bin_mod, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(sys, "stdin", io.StringIO('"a string is not an object"'))
    rc = bin_mod.main()
    assert rc == 0
    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is False


def test_dispatch_accepts_flat_envelope_per_design_contract(
    tmp_path: Path, bin_mod
):
    """#30, M-4: round-trip a flat envelope shaped exactly as the
    SKILL.md ``read-transcript`` example. Pinning this verbatim shape
    catches drift between SKILL.md, design.md and the dispatcher.
    """
    transcript = tmp_path / "session.transcript"
    transcript.write_text("hello\nworld\n", encoding="utf-8")

    envelope = {
        "schema_version": bin_mod.SCHEMA_VERSION,
        "subcommand": "read-transcript",
        "session_id": "sess-contract",
        "project_dir": str(tmp_path),
        "transcript_path": str(transcript),
        "offset": 0,
    }
    response = bin_mod._dispatch(envelope)

    assert response["ok"] is True
    assert response["result"]["end_offset"] == transcript.stat().st_size
    assert "hello" in response["result"]["content"]


def test_main_dispatches_valid_envelope(
    tmp_path: Path,
    bin_mod,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    # Flat envelope per design.md "Skill ↔ bin Contract": fields sit
    # alongside schema_version/subcommand, no payload wrapper.
    envelope = {
        "schema_version": bin_mod.SCHEMA_VERSION,
        "subcommand": "commit-state",
        "session_id": "sess-bin-main",
        "patch": {"skill_ran_at": "2026-04-25T12:00:00+00:00"},
        "project_dir": str(tmp_path),
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(envelope)))
    rc = bin_mod.main()
    assert rc == 0
    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is True
    assert "state_path" in response["result"]
