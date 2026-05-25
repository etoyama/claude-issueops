#!/usr/bin/env python3
"""Bin adapter for the ``session-closer`` skill (Task 13).

Reads a single JSON document from stdin describing one of eight
subcommands and writes a single JSON response to stdout. The contract
is documented in ``.spec-workflow/specs/session-closer/design.md``
under "Skill ↔ bin Contract".

Envelope shape is **flat** (#30, M-4): ``schema_version`` and
``subcommand`` sit alongside the subcommand-specific fields. Handlers
receive the entire envelope and read only the fields they need — no
``payload`` wrapper. SKILL.md's example envelopes match this exactly.

Architectural rules (do **not** add logic here):

1. **Logic-free dispatch** — every subcommand handler is a thin
   ``parse envelope → call pure module → wrap result`` translator. All
   non-trivial behaviour lives in ``src/issueops/*.py`` (#30, M-2 in
   particular consolidates the post-decisions loop into
   ``session_closer.post_decisions_batch``).
2. **No subprocess of our own** — the wrappers in ``gh_adapters`` own
   every shell-out, with ``shell=True`` forbidden.
3. **Always exit 0** — success and failure both produce a valid JSON
   document on stdout. The skill parses the JSON regardless and
   surfaces ``ok: false`` errors back to the user. An exit ≠ 0 would
   make the skill see truncated output and lose the structured error.
4. **Schema check first** — ``schema_version`` and ``subcommand`` are
   validated before any field-specific parsing so a stale skill client
   gets a clean ``internal`` error.

Subcommand handlers (one per row of the design.md subcommand table):

- ``read-transcript``  → :func:`_handle_read_transcript`
- ``resolve-issue``    → :func:`_handle_resolve_issue`
- ``filter-dedup``     → :func:`_handle_filter_dedup`
- ``post-decisions``   → :func:`_handle_post_decisions`
- ``commit-state``     → :func:`_handle_commit_state`
- ``summary``          → :func:`_handle_summary`
- ``escalate``         → :func:`_handle_escalate`
- ``save-pending``     → :func:`_handle_save_pending`
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))

from issueops.decision_extractor import (  # noqa: E402
    Candidate,
    UserDecision,
    candidate_to_decision,
    render_decision_body,
)
from issueops.dedup_checker import filter_local, filter_remote  # noqa: E402
from issueops.gh_adapters import (  # noqa: E402
    GhFailure,
    GhFailureKind,
    PostResult,
    classify_gh_failure,
    gh_list_in_progress,
    gh_post_comment,
    gh_view_comments,
)
from issueops.issue_resolver import (  # noqa: E402
    AmbiguousResolution,
    IssueResolutionError,
    resolve_target_issue,
)
from issueops.marker_parser import Decision, parse_decisions  # noqa: E402
from issueops.memory_escalate import update_memory_index, write_memory_file  # noqa: E402
from issueops.pending_decisions import append_pending_decisions  # noqa: E402
from issueops.session_closer import (  # noqa: E402
    build_summary_body,
    build_summary_marker,
    is_summary_already_posted,
    post_decisions_batch,
)
from issueops.state_writer import merge_update_state  # noqa: E402
from issueops.transcript_reader import read_transcript_since  # noqa: E402

SCHEMA_VERSION = 1

_VALID_SUBCOMMANDS = frozenset(
    {
        "read-transcript",
        "resolve-issue",
        "filter-dedup",
        "post-decisions",
        "commit-state",
        "summary",
        "escalate",
        "save-pending",
    }
)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _ok(result: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "ok": True, "result": result}


def _err(
    kind: str,
    message: str,
    *,
    hint: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"kind": kind, "message": message}
    if hint is not None:
        error["hint"] = hint
    if extra:
        error.update(extra)
    return {"schema_version": SCHEMA_VERSION, "ok": False, "error": error}


# ---------------------------------------------------------------------------
# Payload coercion helpers
# ---------------------------------------------------------------------------


def _project_dir(payload: dict[str, Any]) -> Path:
    """Resolve project_dir from payload or ``CLAUDE_PROJECT_DIR`` env var.

    Per the project memory note, ``CLAUDE_PROJECT_DIR`` is the canonical
    project-dir reference. Payload-supplied paths win when present so
    tests / SKILL.md can override.
    """
    raw = payload.get("project_dir")
    if isinstance(raw, str) and raw:
        return Path(raw)
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    return Path.cwd()


def _candidate_from_dict(d: dict[str, Any]) -> Candidate:
    return Candidate(
        slug=str(d["slug"]),
        what=str(d["what"]),
        why=str(d["why"]),
        alternatives=str(d["alternatives"]),
        consequences=str(d["consequences"]),
        scope_hint=d["scope_hint"],  # type: ignore[arg-type]
    )


def _candidate_to_dict(c: Candidate) -> dict[str, Any]:
    return {
        "slug": c.slug,
        "what": c.what,
        "why": c.why,
        "alternatives": c.alternatives,
        "consequences": c.consequences,
        "scope_hint": c.scope_hint,
    }


def _user_decision_from_dict(d: dict[str, Any]) -> UserDecision:
    return UserDecision(
        candidate=_candidate_from_dict(d["candidate"]),
        final_scope=d["final_scope"],  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _handle_read_transcript(payload: dict[str, Any]) -> dict[str, Any]:
    """Read transcript bytes from ``offset`` to EOF.

    Maps to design.md subcommand ``read-transcript`` (R-3.1, R-3.4).
    Raises :class:`FileNotFoundError` when the path is missing — the
    top-level handler converts that to a ``transcript-missing`` error.
    """
    transcript_path = Path(str(payload["transcript_path"]))
    offset = int(payload.get("offset", 0))
    sliced = read_transcript_since(transcript_path, offset=offset)
    return _ok({"content": sliced.content, "end_offset": sliced.end_offset})


def _handle_resolve_issue(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve target issue via Tier 1 (gh in-progress) + Tier 2 (branch).

    Maps to ``resolve-issue`` (R-6). ``branch`` defaults to ``""`` so a
    detached HEAD or unavailable git falls through to AmbiguousResolution
    or IssueResolutionError per the state-transition table.
    """
    branch = str(payload.get("branch") or "")
    cwd = _project_dir(payload)

    def _list() -> list[int]:
        return gh_list_in_progress(cwd=cwd)

    try:
        result = resolve_target_issue(
            branch=branch,
            list_in_progress_fn=_list,
        )
    except IssueResolutionError as exc:
        return _err("issue-resolution", str(exc))

    if isinstance(result, AmbiguousResolution):
        # Spec contract (SKILL.md / design.md / VERIFICATION.md V-9):
        # ``{ambiguous_candidates: [int, ...]}`` — single field carries
        # both the "ambiguous" signal and the candidate list. Earlier
        # split-field shape was bin-internal drift (#29).
        return _ok({"ambiguous_candidates": list(result.candidates)})
    return _ok({"issue_number": int(result)})


def _handle_filter_dedup(payload: dict[str, Any]) -> dict[str, Any]:
    """Run Tier 1 (local slugs) + Tier 2 (remote markers) dedup.

    Maps to ``filter-dedup`` (R-5). Tier 2 fetches issue comments via
    :func:`gh_view_comments`; gh failures classify into the response's
    ``tier2_skipped_kind`` so SKILL.md can warn the user without
    aborting the flow.
    """
    raw_candidates = payload.get("candidates") or []
    captured_slugs = list(payload.get("captured_slugs") or [])
    issue_number = int(payload["issue_number"])
    cwd = _project_dir(payload)

    candidates = [_candidate_from_dict(c) for c in raw_candidates]
    after_local = filter_local(candidates, captured_slugs=captured_slugs)

    # Tier 2: gh-fetch existing comments and parse Decisions.
    tier2_skipped_kind: str | None = None
    existing_decisions: list[Decision] = []
    try:
        comments = gh_view_comments(issue_number, cwd=cwd)
    except GhFailure as exc:
        tier2_skipped_kind = exc.kind.value
        comments = []
    if comments and not tier2_skipped_kind:
        for c in comments:
            body = c.get("body", "") if isinstance(c, dict) else ""
            existing_decisions.extend(parse_decisions(body))

    after_remote = filter_remote(after_local, existing_decisions=existing_decisions)

    out: dict[str, Any] = {
        "candidates": [_candidate_to_dict(c) for c in after_remote],
    }
    if tier2_skipped_kind is not None:
        out["tier2_skipped_kind"] = tier2_skipped_kind
    return _ok(out)


def _handle_post_decisions(payload: dict[str, Any]) -> dict[str, Any]:
    """Post each decision; collect successes and per-decision failures.

    Maps to ``post-decisions`` (R-1.3, R-9.1, R-9.2). State is *not*
    touched here — that is the ``commit-state`` subcommand's job.

    Logic-free dispatch (#30, M-2): the loop, exception handling, and
    last-failure tracking all live in
    ``session_closer._post_decisions``. This handler only translates
    the JSON wire shape, wires the gh adapter with the right ``cwd``,
    and serialises the resulting :class:`PostBatchResult` back to JSON.
    """
    issue_number = int(payload["issue_number"])
    cwd = _project_dir(payload)
    raw_decisions = payload.get("decisions") or []
    decisions = [_user_decision_from_dict(d) for d in raw_decisions]

    def _post(issue: int, body: str) -> PostResult:
        return gh_post_comment(issue, body, cwd=cwd)

    batch = post_decisions_batch(issue_number, decisions, _post)

    failed_payload: list[dict[str, Any]] = []
    for fp in batch.failed:
        entry: dict[str, Any] = {"slug": fp.decision.candidate.slug}
        if fp.failure is not None:
            entry["error"] = fp.failure.stderr
            entry["kind"] = fp.failure.kind.value
            if fp.failure.hint:
                entry["hint"] = fp.failure.hint
        elif fp.exception_text is not None:
            entry["error"] = fp.exception_text
        else:
            entry["error"] = ""
        failed_payload.append(entry)

    out: dict[str, Any] = {
        "posted": [ud.candidate.slug for ud in batch.posted],
        "failed": failed_payload,
    }
    if batch.last_failure_kind is not None:
        out["gh_failure_kind"] = batch.last_failure_kind.value
    if batch.last_failure_hint is not None:
        out["gh_hint"] = batch.last_failure_hint
    return _ok(out)


def _handle_commit_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Atomically merge a state-file patch via :func:`merge_update_state`.

    Maps to ``commit-state`` (R-1.4, R-1.5, R-7). The single-window
    write lets PreCompact / UserPromptSubmit / SessionEnd / session-closer
    share NFR Reliability guarantees.
    """
    project_dir = _project_dir(payload)
    session_id = str(payload["session_id"])
    patch = payload.get("patch") or {}
    if not isinstance(patch, dict):
        return _err("internal", "patch must be a JSON object")

    state_path = merge_update_state(
        project_dir=project_dir,
        session_id=session_id,
        patch=patch,
    )
    return _ok({"state_path": str(state_path)})


def _handle_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Post the close-mode summary if not already present (R-2.2 / R-2.3).

    The skill drives idempotency: this handler checks the issue's
    existing comments for the same-session marker and skips when a
    duplicate is found.

    Body composition (#64 fix):
    - If ``body`` is supplied verbatim in the payload, it wins (escape
      hatch for SKILL.md to fully control the comment body).
    - Otherwise, the body is :func:`build_summary_body` applied to
      ``captured_slugs_total`` — adds a ``### Captured decisions``
      subsection when slugs are present, falls back to the marker-only
      shape when not. Non-string / empty slug entries are dropped.
    """
    issue_number = int(payload["issue_number"])
    session_id = str(payload["session_id"])
    cwd = _project_dir(payload)

    raw_slugs = payload.get("captured_slugs_total") or []
    captured_slugs_total = [s for s in raw_slugs if isinstance(s, str) and s]

    try:
        existing = gh_view_comments(issue_number, cwd=cwd)
    except GhFailure as exc:
        # Treat fetch-failure as non-idempotent (we cannot prove a
        # duplicate); SKILL.md surfaces the warning via ``warnings``.
        return _ok(
            {
                "posted": False,
                "skipped": "view-failed",
                "gh_failure_kind": exc.kind.value,
            }
        )

    marker_token = build_summary_marker(session_id)
    if is_summary_already_posted(existing, session_id):
        return _ok({"posted": False, "skipped": "idempotent", "marker": marker_token})

    default_body = build_summary_body(session_id, captured_slugs_total)
    body = str(payload.get("body") or default_body)
    result = gh_post_comment(issue_number, body, cwd=cwd)
    if not result.ok:
        out: dict[str, Any] = {
            "posted": False,
            "skipped": "post-failed",
            "marker": marker_token,
        }
        if result.failure is not None:
            out["gh_failure_kind"] = result.failure.kind.value
            if result.failure.hint:
                out["gh_hint"] = result.failure.hint
        return _ok(out)
    return _ok({"posted": True, "marker": marker_token, "comment_url": result.comment_url})


def _handle_escalate(payload: dict[str, Any]) -> dict[str, Any]:
    """Escalate cross-issue decisions to the project memory directory.

    Maps to ``escalate`` (R-8). Per the design rule, only
    ``final_scope == "cross-issue"`` decisions touch ``memory_dir`` —
    issue-scoped entries are silently ignored.
    """
    raw_decisions = payload.get("decisions") or []
    memory_dir = Path(str(payload["project_memory_dir"]))

    decisions = [_user_decision_from_dict(d) for d in raw_decisions]
    written: list[str] = []
    warnings: list[str] = []

    for ud in decisions:
        if ud.final_scope != "cross-issue":
            continue
        decision = candidate_to_decision(ud.candidate)
        try:
            write_memory_file(decision, memory_dir)
        except Exception as exc:  # noqa: BLE001 — R-8.4 best-effort
            warnings.append(
                f"memory write failed for slug {decision.slug}: {exc}"
            )
            continue
        try:
            update_memory_index(memory_dir, decision)
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"memory index update failed for slug {decision.slug}: {exc}"
            )
            continue
        written.append(decision.slug)

    out: dict[str, Any] = {"written": written}
    if warnings:
        out["warnings"] = warnings
    return _ok(out)


def _handle_save_pending(payload: dict[str, Any]) -> dict[str, Any]:
    """Append unposted decisions to the per-session pending file (R-9.4).

    Maps to ``save-pending``. Returns the resolved file path so SKILL.md
    can echo it to the user.
    """
    project_dir = _project_dir(payload)
    session_id = str(payload["session_id"])
    issue_number = int(payload["issue_number"])
    raw_decisions = payload.get("decisions") or []
    decisions = [_user_decision_from_dict(d) for d in raw_decisions]

    path = append_pending_decisions(
        project_dir=project_dir,
        session_id=session_id,
        issue_number=issue_number,
        decisions=decisions,
    )
    return _ok({"pending_path": str(path)})


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


_HANDLERS = {
    "read-transcript": _handle_read_transcript,
    "resolve-issue": _handle_resolve_issue,
    "filter-dedup": _handle_filter_dedup,
    "post-decisions": _handle_post_decisions,
    "commit-state": _handle_commit_state,
    "summary": _handle_summary,
    "escalate": _handle_escalate,
    "save-pending": _handle_save_pending,
}


def _dispatch(envelope: dict[str, Any]) -> dict[str, Any]:
    """Route to the right handler after validating the envelope.

    Envelope shape (per design.md "Skill ↔ bin Contract") is **flat** —
    ``schema_version`` and ``subcommand`` sit alongside the
    subcommand-specific fields, with no ``payload`` wrapper. The
    handler receives the entire envelope and reads only the fields it
    needs (#30, M-4).

    Validation order matters: schema_version is checked before
    subcommand so a stale skill client always gets the same error
    regardless of which subcommand it tried to call.
    """
    if envelope.get("schema_version") != SCHEMA_VERSION:
        return _err(
            "internal",
            f"schema mismatch: expected schema_version={SCHEMA_VERSION}, "
            f"got {envelope.get('schema_version')!r}",
        )

    subcommand = envelope.get("subcommand")
    if subcommand not in _VALID_SUBCOMMANDS:
        return _err("internal", f"unknown subcommand: {subcommand!r}")

    handler = _HANDLERS[subcommand]  # type: ignore[index]
    try:
        return handler(envelope)
    except FileNotFoundError as exc:
        # read-transcript is the typical caller; map to the design
        # error kind so SKILL.md can route a "transcript missing" UX.
        return _err("transcript-missing", str(exc))
    except KeyError as exc:
        return _err("internal", f"missing required field: {exc}")
    except (ValueError, TypeError) as exc:
        return _err("internal", str(exc))
    except GhFailure as exc:
        out_extra: dict[str, Any] = {"gh_failure_kind": exc.kind.value}
        if exc.hint:
            out_extra["hint"] = exc.hint
        return _err("gh-failure", exc.stderr or "gh command failed", extra=out_extra)
    except Exception as exc:  # noqa: BLE001 — last-resort catch-all
        return _err("internal", f"{type(exc).__name__}: {exc}")


def main() -> int:
    """Read JSON from stdin, dispatch, write JSON to stdout. Exit 0 always."""
    raw = sys.stdin.read()
    try:
        envelope = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        sys.stdout.write(json.dumps(_err("internal", f"stdin JSON parse failed: {exc}")))
        return 0

    if not isinstance(envelope, dict):
        sys.stdout.write(json.dumps(_err("internal", "stdin payload must be a JSON object")))
        return 0

    response = _dispatch(envelope)
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Last-ditch: print the traceback to stderr but still emit a
        # parseable JSON to stdout so the skill can surface the error.
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "ok": False,
                    "error": {
                        "kind": "internal",
                        "message": "unhandled exception in bin adapter",
                    },
                }
            )
        )
        sys.exit(0)
