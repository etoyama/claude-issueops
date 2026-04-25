"""Orchestrator for the ``session-closer`` skill.

This module ties the pure helper modules
(:mod:`transcript_reader`, :mod:`decision_extractor`, :mod:`dedup_checker`,
:mod:`issue_resolver`, :mod:`gh_adapters`, :mod:`pending_decisions`,
:mod:`state_writer`, :mod:`memory_escalate`) into the two top-level
flows the SKILL.md drives:

- :func:`run_capture` — capture mode (R-1): post user-confirmed
  Decisions to the issue, then commit state.
- :func:`run_close` — close mode (R-2): capture flow + idempotent
  summary post + memory escalation for cross-issue scope decisions.

Architectural rules (design.md § Components and Interfaces):

1. **No subprocess, no I/O of our own** beyond what the injected
   callables do. ``gh_post_fn`` / ``gh_view_comments_fn`` /
   ``commit_state_fn`` / ``write_memory_fn`` / ``update_index_fn`` are
   all dependency-injected so the entire flow is unit-testable without
   spawning processes.
2. **No interactive prompts.** ``AskUserQuestion`` lives in SKILL.md,
   never in Python. The orchestrator receives ``user_decisions`` (the
   already-approved + scope-confirmed subset) and a
   ``failure_choice`` selected by the user via SKILL.md when posts fail.
3. **Subcommand separation.** ``gh_post_fn`` is invoked first; only
   *after* it returns does the orchestrator call ``commit_state_fn``.
   If the latter raises, the post-side has already happened but state
   stays at its previous value (T-136). This is the heart of the
   "post-decisions / commit-state split" introduced in design.md to
   resolve the Codex re-review's responsibility-mixing finding.
4. **State Writes Table compliance.** Every code path corresponds to
   exactly one of the 10 rows of the design.md "State Writes Table".
   See ``_build_state_patch`` for the row → patch mapping.
5. **Memory escalation only for cross-issue.** ``final_scope == "issue"``
   never touches the memory directory. Verified by T-127 which asserts
   the memory dir is *not even created* when no cross-issue exists.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from issueops.decision_extractor import (
    UserDecision,
    candidate_to_decision,
)
from issueops.gh_adapters import GhFailure, GhFailureKind, PostResult
from issueops.marker_parser import Decision
from issueops.memory_escalate import (
    update_memory_index,
    write_memory_file,
)
from issueops.pending_decisions import append_pending_decisions

__all__ = [
    "CaptureRequest",
    "CloseRequest",
    "CaptureResult",
    "CloseResult",
    "run_capture",
    "run_close",
    "build_summary_marker",
    "is_summary_already_posted",
]


FailureChoice = Literal["save", "discard", "abort"]


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaptureRequest:
    """Input to :func:`run_capture`.

    Built by SKILL.md from prior subcommand results (read-transcript +
    resolve-issue + filter-dedup) and the user's AskUserQuestion picks.

    Attributes
    ----------
    project_dir, session_id, issue_number :
        Identity of the session we are closing and the target issue.
    user_decisions :
        The candidates the user *approved*, with their final scope. May
        be empty (all-rejected, no-candidates, or transcript-missing).
    transcript_end_offset :
        Byte offset reported by ``read_transcript_since``. Persisted as
        ``last_processed_offset`` only on successful flow completions.
    extracted_candidate_count :
        Number of candidates that survived parse + dedup *before* user
        review. Used to disambiguate "0 candidates" (table row 6) from
        "all rejected" (table row 5) — both produce empty
        ``user_decisions`` but write different state.
    gh_post_fn :
        Callable performing one ``gh issue comment`` post per decision.
        Signature: ``(issue_number: int, body: str) -> PostResult``
        (extra kwargs ignored — gh_adapters' real wrapper accepts cwd).
    commit_state_fn :
        Callable wrapping :func:`state_writer.merge_update_state`.
        Signature: ``(*, session_id: str, patch: dict, now: datetime|None) -> Path``.
    failure_choice :
        User's selection from the 3-choice dialog when posts fail.
        ``save`` (default) writes pending; ``discard`` drops; ``abort``
        skips the offset write so the same range is reprocessable.
    transcript_missing :
        Set by SKILL.md when ``read-transcript`` returned
        ``transcript-missing``. Routes to table row 7 (no state write).
    issue_resolution_failed :
        Set by SKILL.md when ``resolve-issue`` returned an
        ``IssueResolutionError``. Routes to table row 8 (skill_ran_at only).
    existing_remote_decisions :
        Optional Decision[] parsed by SKILL.md from
        ``gh issue view --json comments``. The orchestrator also runs a
        last-line dedup against these (defense in depth — SKILL.md should
        already have filtered, but if a race added a Decision between
        filter-dedup and post-decisions we still skip it).
    tier2_skipped_kind :
        When the Tier 2 dedup gh call failed in SKILL.md, the failure
        kind is forwarded so the result's ``warnings`` carry it.
    now :
        Injected datetime for deterministic ``skill_ran_at``.
    """

    project_dir: Path
    session_id: str
    issue_number: int
    user_decisions: list[UserDecision]
    transcript_end_offset: int
    extracted_candidate_count: int
    gh_post_fn: Callable[..., PostResult]
    commit_state_fn: Callable[..., Path]
    failure_choice: FailureChoice = "save"
    transcript_missing: bool = False
    issue_resolution_failed: bool = False
    existing_remote_decisions: tuple[Decision, ...] = ()
    tier2_skipped_kind: GhFailureKind | None = None
    now: datetime | None = None


@dataclass(frozen=True)
class CloseRequest:
    """Input to :func:`run_close` — superset of CaptureRequest.

    Adds the gh-view callable (for summary idempotency check), the
    memory directory, and overridable memory-write functions so tests
    can inject failures (T-128).
    """

    project_dir: Path
    session_id: str
    issue_number: int
    user_decisions: list[UserDecision]
    transcript_end_offset: int
    extracted_candidate_count: int
    gh_post_fn: Callable[..., PostResult]
    gh_view_comments_fn: Callable[..., list[dict]]
    commit_state_fn: Callable[..., Path]
    memory_dir: Path
    failure_choice: FailureChoice = "save"
    transcript_missing: bool = False
    issue_resolution_failed: bool = False
    existing_remote_decisions: tuple[Decision, ...] = ()
    tier2_skipped_kind: GhFailureKind | None = None
    write_memory_fn: Callable[[Decision, Path], Any] = write_memory_file
    update_index_fn: Callable[[Path, Decision], Any] = update_memory_index
    now: datetime | None = None


@dataclass(frozen=True)
class CaptureResult:
    """Output of :func:`run_capture`.

    The fields are flat-and-explicit so SKILL.md can ``json.dumps`` the
    result for the bin → skill stdout payload directly.
    """

    posted_slugs: list[str]
    failed_slugs: list[str]
    posted_decisions: list[UserDecision]
    failed_decisions: list[UserDecision]
    posted_slug_summaries: list[dict]
    failed_slug_summaries: list[dict]
    gh_failure_kind: GhFailureKind | None
    gh_hint: str | None
    pending_path: Path | None
    state_path: Path | None
    aborted: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CloseResult:
    """Output of :func:`run_close` — embeds the inner CaptureResult."""

    capture: CaptureResult
    summary_posted: bool
    summary_skipped_reason: str | None
    escalated_slugs: list[str]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers (T-31, T-32 are exercised against these)
# ---------------------------------------------------------------------------


_SUMMARY_MARKER_PREFIX = "<!-- claude-issueops:session-closer:summary:"


def build_summary_marker(session_id: str) -> str:
    """Return the canonical close-mode summary comment body.

    The marker token embeds ``session_id`` so multiple close calls in
    the same session detect each other (R-2.3 idempotency); cross-session
    summaries on the same issue do *not* collide because each carries its
    own session id.
    """
    return (
        f"{_SUMMARY_MARKER_PREFIX}{session_id} -->\n"
        "## Session summary\n"
    )


def is_summary_already_posted(comments: list[Any], session_id: str) -> bool:
    """True iff any comment carries the summary marker for ``session_id``.

    ``comments`` is tolerant of either str entries (raw bodies) or dict
    entries from ``gh issue view --json comments`` (``{"body": "..."}``);
    SKILL.md routes the latter through filter-dedup but the orchestrator
    accepts both shapes for defensive callability.
    """
    needle = f"{_SUMMARY_MARKER_PREFIX}{session_id} -->"
    for c in comments or []:
        body = c if isinstance(c, str) else (c.get("body", "") if isinstance(c, dict) else "")
        if needle in (body or ""):
            return True
    return False


def _slug_summary(d: UserDecision) -> dict:
    """Render a 1-line summary entry suitable for AskUserQuestion display."""
    return {"slug": d.candidate.slug, "what": d.candidate.what}


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# run_capture
# ---------------------------------------------------------------------------


def _empty_result(*, aborted: bool = False, warnings: list[str] | None = None) -> CaptureResult:
    return CaptureResult(
        posted_slugs=[],
        failed_slugs=[],
        posted_decisions=[],
        failed_decisions=[],
        posted_slug_summaries=[],
        failed_slug_summaries=[],
        gh_failure_kind=None,
        gh_hint=None,
        pending_path=None,
        state_path=None,
        aborted=aborted,
        warnings=list(warnings or []),
    )


def _filter_remote_dedup(
    user_decisions: list[UserDecision],
    existing: tuple[Decision, ...],
) -> tuple[list[UserDecision], list[str]]:
    """Drop user_decisions whose slug already appears on the issue.

    Returns ``(kept, skipped_slugs)``.
    """
    if not existing:
        return list(user_decisions), []
    seen = {d.slug for d in existing}
    kept: list[UserDecision] = []
    skipped: list[str] = []
    for ud in user_decisions:
        if ud.candidate.slug in seen:
            skipped.append(ud.candidate.slug)
        else:
            kept.append(ud)
    return kept, skipped


def _render_decision_body(ud: UserDecision) -> str:
    """Render the gh-issue-comment body for a UserDecision.

    Uses :func:`candidate_to_decision` to round-trip through the frozen
    marker_parser.Decision shape so the marker text is identical to what
    the marker_parser will see on the next dedup check (R-5.2).
    """
    decision = candidate_to_decision(ud.candidate)
    return (
        f"<!-- claude-issueops:decision:{decision.slug} -->\n"
        f"## Decision: {decision.slug}\n"
        "\n"
        f"**What:** {decision.what}\n"
        "\n"
        f"**Why:** {decision.why}\n"
        "\n"
        f"**Alternatives considered:** {decision.alternatives}\n"
        "\n"
        f"**Consequences:** {decision.consequences}\n"
    )


def _post_decisions(
    issue_number: int,
    decisions: list[UserDecision],
    gh_post_fn: Callable[..., PostResult],
) -> tuple[list[UserDecision], list[UserDecision], GhFailureKind | None, str | None]:
    """Post each decision serially; collect successes/failures.

    Returns ``(posted, failed, last_failure_kind, last_failure_hint)``.
    Per R-9 a single failure must not abort the loop; the orchestrator
    aggregates and lets SKILL.md decide via the 3-choice dialog.
    """
    posted: list[UserDecision] = []
    failed: list[UserDecision] = []
    last_kind: GhFailureKind | None = None
    last_hint: str | None = None
    for ud in decisions:
        body = _render_decision_body(ud)
        result = gh_post_fn(issue_number, body)
        if result is not None and result.ok:
            posted.append(ud)
        else:
            failed.append(ud)
            if result is not None and result.failure is not None:
                last_kind = result.failure.kind
                last_hint = result.failure.hint
    return posted, failed, last_kind, last_hint


def _build_capture_state_patch(
    *,
    posted: list[UserDecision],
    failed: list[UserDecision],
    extracted_candidate_count: int,
    user_decision_count: int,
    transcript_end_offset: int,
    failure_choice: FailureChoice,
    skill_ran_at_iso: str,
    existing_captured_slugs: list[str],
) -> dict:
    """Compose the ``commit_state_fn`` patch per the State Writes Table.

    Rows mapped here:
      - **Row 1**: all success → skill_ran_at + offset + captured_slugs.
      - **Row 2**: partial fail + save → same as row 1 (pending file
        is written separately by the orchestrator).
      - **Row 3**: partial fail + discard → same as row 1.
      - **Row 4**: partial fail + abort → skill_ran_at + captured_slugs
        (no offset).
      - **Row 5**: extracted > 0 but user_decisions empty (all rejected)
        → skill_ran_at only.
      - **Row 6**: extracted == 0 → skill_ran_at + offset only
        (caller can mark progress safely).
    """
    patch: dict[str, Any] = {"skill_ran_at": skill_ran_at_iso}

    if user_decision_count == 0:
        if extracted_candidate_count == 0:
            # Row 6: no candidates → mark progress.
            patch["last_processed_offset"] = transcript_end_offset
        # Row 5: extracted but rejected → no offset, no slugs.
        return patch

    new_slugs = [ud.candidate.slug for ud in posted]
    merged_slugs = list(existing_captured_slugs) + [
        s for s in new_slugs if s not in existing_captured_slugs
    ]
    patch["captured_slugs"] = merged_slugs

    has_failure = bool(failed)
    if has_failure and failure_choice == "abort":
        # Row 4: keep slugs, no offset.
        return patch

    # Rows 1 / 2 / 3: always commit offset.
    patch["last_processed_offset"] = transcript_end_offset
    return patch


def _read_existing_captured_slugs(project_dir: Path, session_id: str) -> list[str]:
    """Read ``state.captured_slugs`` if the file exists and is parseable.

    Used so the new run *appends* to (rather than replaces) prior slugs.
    Corrupt JSON yields ``[]`` and is left for ``commit_state_fn``
    (state_writer) to quarantine on its own write path.
    """
    import json

    from issueops.path_utils import state_file_path

    target = state_file_path(project_dir, session_id)
    if not target.exists():
        return []
    try:
        data = json.loads(target.read_text())
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    slugs = data.get("captured_slugs")
    if not isinstance(slugs, list):
        return []
    return [s for s in slugs if isinstance(s, str)]


def run_capture(req: CaptureRequest) -> CaptureResult:
    """Execute the capture flow per R-1 and the State Writes Table.

    See module docstring for the architectural rules. Returns a
    :class:`CaptureResult` describing what landed where; raises only
    when ``commit_state_fn`` itself raises (T-107 / T-136).
    """
    warnings: list[str] = []
    if req.tier2_skipped_kind is not None:
        warnings.append(
            f"tier2 dedup skipped due to gh failure: {req.tier2_skipped_kind.value}"
        )

    # Row 7: transcript missing → no state write at all.
    if req.transcript_missing:
        return _empty_result(aborted=True, warnings=warnings)

    now = _now(req.now)
    skill_ran_at = now.isoformat()

    # Row 8: issue resolution failed → skill_ran_at only.
    if req.issue_resolution_failed:
        path = req.commit_state_fn(
            session_id=req.session_id,
            patch={"skill_ran_at": skill_ran_at},
            now=now,
        )
        return CaptureResult(
            posted_slugs=[],
            failed_slugs=[],
            posted_decisions=[],
            failed_decisions=[],
            posted_slug_summaries=[],
            failed_slug_summaries=[],
            gh_failure_kind=None,
            gh_hint=None,
            pending_path=None,
            state_path=path,
            aborted=True,
            warnings=warnings,
        )

    # Defense-in-depth remote dedup (T-120). SKILL.md should already
    # have filtered via filter-dedup, but a race between filter-dedup
    # and post-decisions could let a duplicate through.
    decisions_to_post, _skipped_remote = _filter_remote_dedup(
        req.user_decisions, req.existing_remote_decisions
    )

    # Post all approved decisions, aggregating failures.
    posted, failed, gh_failure_kind, gh_hint = _post_decisions(
        req.issue_number, decisions_to_post, req.gh_post_fn
    )

    # Save pending if requested (R-9.4). Pending is the user's
    # only record of unposted decisions, so we write it BEFORE
    # commit_state_fn so a state-commit crash still leaves the
    # pending file intact.
    pending_path: Path | None = None
    if failed and req.failure_choice == "save":
        # T-135: read the pre-existing pending file's issue_number BEFORE
        # appending, so we can defensively warn when the new request's
        # issue diverges from what the file already records (a session
        # is expected to target a single issue, but we tolerate the mismatch).
        from issueops.pending_decisions import pending_path as _pending_path

        prior_target = _pending_path(req.project_dir, req.session_id)
        prior_issue: int | None = None
        if prior_target.exists():
            try:
                import json as _json

                prior = _json.loads(prior_target.read_text())
                if isinstance(prior, dict):
                    prior_issue = prior.get("issue_number")
            except Exception:
                prior_issue = None

        pending_path = append_pending_decisions(
            project_dir=req.project_dir,
            session_id=req.session_id,
            issue_number=req.issue_number,
            decisions=failed,
            now=now,
        )

        if prior_issue is not None and prior_issue != req.issue_number:
            warnings.append(
                f"pending file issue_number mismatch: prior {prior_issue}, "
                f"current {req.issue_number} (entries appended defensively)"
            )

    existing_captured = _read_existing_captured_slugs(req.project_dir, req.session_id)

    patch = _build_capture_state_patch(
        posted=posted,
        failed=failed,
        extracted_candidate_count=req.extracted_candidate_count,
        user_decision_count=len(req.user_decisions),
        transcript_end_offset=req.transcript_end_offset,
        failure_choice=req.failure_choice,
        skill_ran_at_iso=skill_ran_at,
        existing_captured_slugs=existing_captured,
    )

    # Subcommand-separation guarantee: gh_post has *already* run.
    # ``commit_state_fn`` is the only state mutation; if it raises we
    # propagate so SKILL.md can route the error, while the gh side
    # remains visible on the issue (T-136).
    state_path = req.commit_state_fn(
        session_id=req.session_id,
        patch=patch,
        now=now,
    )

    return CaptureResult(
        posted_slugs=[ud.candidate.slug for ud in posted],
        failed_slugs=[ud.candidate.slug for ud in failed],
        posted_decisions=list(posted),
        failed_decisions=list(failed),
        posted_slug_summaries=[_slug_summary(ud) for ud in posted],
        failed_slug_summaries=[_slug_summary(ud) for ud in failed],
        gh_failure_kind=gh_failure_kind,
        gh_hint=gh_hint,
        pending_path=pending_path,
        state_path=state_path,
        aborted=False,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# run_close
# ---------------------------------------------------------------------------


def _post_summary_if_needed(
    *,
    issue_number: int,
    session_id: str,
    posted_count: int,
    gh_post_fn: Callable[..., PostResult],
    gh_view_comments_fn: Callable[..., list[dict]],
) -> tuple[bool, str | None]:
    """Run R-2.2 / R-2.3: post the close-mode summary unless skipped.

    Returns ``(posted, skipped_reason)``. ``skipped_reason`` is one of:
      - ``"no-decisions"``  — total posted in this session is 0.
      - ``"idempotent"``    — same-sid summary marker already on issue.
      - ``"view-failed"``   — could not fetch comments to verify
        idempotency; we refuse to post blind so the user does not see a
        duplicate summary on retry.
      - ``"post-failed"``   — gh post itself failed (best-effort).
      - ``None``            — we did post the summary.
    """
    if posted_count == 0:
        return False, "no-decisions"

    # Idempotency relies on us being able to *read* prior comments. If
    # the view call fails, falling back to "post anyway" risks a
    # duplicate summary the next time the user retries — worse than
    # skipping. Match the bin adapter's behaviour at lines 362-372.
    try:
        existing = gh_view_comments_fn(issue_number)
    except (GhFailure, OSError):
        return False, "view-failed"

    if is_summary_already_posted(existing, session_id):
        return False, "idempotent"

    body = build_summary_marker(session_id)
    result = gh_post_fn(issue_number, body)
    if result is not None and result.ok:
        return True, None
    # Treat post failures here as best-effort; SKILL.md surfaces them
    # via the result's warnings if needed.
    return False, "post-failed"


def _escalate_cross_issue(
    *,
    posted: list[UserDecision],
    memory_dir: Path,
    write_memory_fn: Callable[[Decision, Path], Any],
    update_index_fn: Callable[[Path, Decision], Any],
) -> tuple[list[str], list[str]]:
    """Run R-8 for every cross-issue decision in ``posted``.

    Returns ``(escalated_slugs, warnings)``. We iterate in input order
    so ``MEMORY.md`` reflects the order decisions were posted.
    """
    cross = [ud for ud in posted if ud.final_scope == "cross-issue"]
    if not cross:
        # Hard rule: no cross-issue → memory_dir must not be touched.
        return [], []

    escalated: list[str] = []
    warnings: list[str] = []
    for ud in cross:
        decision = candidate_to_decision(ud.candidate)
        try:
            write_memory_fn(decision, memory_dir)
        except Exception as exc:  # noqa: BLE001 — R-8.4 best-effort
            warnings.append(
                f"memory write failed for slug {decision.slug}: {exc}"
            )
            continue
        try:
            update_index_fn(memory_dir, decision)
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"memory index update failed for slug {decision.slug}: {exc}"
            )
            continue
        escalated.append(decision.slug)
    return escalated, warnings


def run_close(req: CloseRequest) -> CloseResult:
    """Execute the close flow per R-2.

    Composition: run_capture → optional summary → optional escalation.
    """
    capture_req = CaptureRequest(
        project_dir=req.project_dir,
        session_id=req.session_id,
        issue_number=req.issue_number,
        user_decisions=req.user_decisions,
        transcript_end_offset=req.transcript_end_offset,
        extracted_candidate_count=req.extracted_candidate_count,
        gh_post_fn=req.gh_post_fn,
        commit_state_fn=req.commit_state_fn,
        failure_choice=req.failure_choice,
        transcript_missing=req.transcript_missing,
        issue_resolution_failed=req.issue_resolution_failed,
        existing_remote_decisions=req.existing_remote_decisions,
        tier2_skipped_kind=req.tier2_skipped_kind,
        now=req.now,
    )
    capture = run_capture(capture_req)

    # R-2.5: 0 decisions AND 0 cross-issue → skip both summary and
    # escalation. We also short-circuit when capture aborted.
    if capture.aborted:
        return CloseResult(
            capture=capture,
            summary_posted=False,
            summary_skipped_reason="aborted",
            escalated_slugs=[],
            warnings=list(capture.warnings),
        )

    summary_posted, summary_skipped_reason = _post_summary_if_needed(
        issue_number=req.issue_number,
        session_id=req.session_id,
        posted_count=len(capture.posted_slugs),
        gh_post_fn=req.gh_post_fn,
        gh_view_comments_fn=req.gh_view_comments_fn,
    )

    escalated, esc_warnings = _escalate_cross_issue(
        posted=capture.posted_decisions,
        memory_dir=req.memory_dir,
        write_memory_fn=req.write_memory_fn,
        update_index_fn=req.update_index_fn,
    )

    return CloseResult(
        capture=capture,
        summary_posted=summary_posted,
        summary_skipped_reason=summary_skipped_reason,
        escalated_slugs=escalated,
        warnings=list(capture.warnings) + esc_warnings,
    )
