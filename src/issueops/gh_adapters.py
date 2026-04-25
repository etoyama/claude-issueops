"""Single owner of subprocess + failure classification for the
session-closer skill.

Per design.md § Component 8 this module is the **only** place that
shells out to ``gh`` / ``git`` for session-closer flows. Every wrapper
uses argv arrays — ``shell=True`` is forbidden (NFR Security).

Public surface:

- :class:`GhFailureKind` — StrEnum of the four classifications used by
  SKILL.md to decide error UX (auth hint, rate-limit retry, etc.).
- :class:`GhFailure` — frozen dataclass + Exception carrier.
- :class:`PostResult` — return type of :func:`gh_post_comment`.
- :func:`classify_gh_failure` — pure stderr/exit-code → ``GhFailure``
  classifier (the only piece unit-tested in L1; see test-design.md
  T-71/T-72).
- Subprocess wrappers: :func:`gh_view_comments`, :func:`gh_post_comment`,
  :func:`gh_list_in_progress`, :func:`git_branch`. Their behaviour is
  exercised in L3 verification (V-1〜V-15) where real CLIs run.

The wrappers deliberately keep their signatures minimal so the bin
adapter (``bin/session_closer.py``) can DI them into orchestrator
callables verbatim.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

__all__ = [
    "GhFailureKind",
    "GhFailure",
    "PostResult",
    "classify_gh_failure",
    "gh_view_comments",
    "gh_post_comment",
    "gh_list_in_progress",
    "git_branch",
]


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


class GhFailureKind(StrEnum):
    """Four-way classification of ``gh`` failures (R-9.1)."""

    NETWORK = "network"
    AUTH = "auth"
    RATE_LIMIT = "rate-limit"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GhFailure(Exception):
    """Carrier for a classified ``gh`` failure.

    Subclassing ``Exception`` lets callers ``raise`` it when they want
    to abort, but most flows use it as a value (per-candidate failures
    must not abort the loop — see R-9 graceful degradation).
    """

    kind: GhFailureKind
    stderr: str
    exit_code: int
    hint: str | None = None


@dataclass(frozen=True)
class PostResult:
    """Outcome of a single :func:`gh_post_comment` call."""

    ok: bool
    comment_url: str | None
    failure: GhFailure | None


# ---------------------------------------------------------------------------
# Pure classifier (unit-tested in T-71 / T-72)
# ---------------------------------------------------------------------------

_AUTH_PATTERNS = (
    re.compile(r"authenticat", re.IGNORECASE),  # authentication / Authenticate / authenticated
    re.compile(r"\bauth status\b", re.IGNORECASE),
    re.compile(r"\b401\b"),
    re.compile(r"bad credentials", re.IGNORECASE),
)

_RATE_LIMIT_PATTERNS = (
    re.compile(r"rate limit", re.IGNORECASE),
    re.compile(r"\b429\b"),
)

_NETWORK_PATTERNS = (
    re.compile(r"could not resolve host", re.IGNORECASE),
    re.compile(r"connection refused", re.IGNORECASE),
    re.compile(r"\btimeout\b", re.IGNORECASE),
    re.compile(r"\bdial tcp\b", re.IGNORECASE),
    re.compile(r"network is unreachable", re.IGNORECASE),
)

_AUTH_HINT = "gh auth status を実行してください"


def classify_gh_failure(stderr: str, exit_code: int) -> GhFailure:
    """Classify a ``gh`` failure by inspecting stderr (case-insensitive).

    Order of checks matters when a stderr fragment could match multiple
    patterns: AUTH wins over rate-limit and network because mis-routing
    an auth failure into "retry later" wastes the user's time.
    """
    text = stderr or ""

    if any(p.search(text) for p in _AUTH_PATTERNS):
        return GhFailure(
            kind=GhFailureKind.AUTH,
            stderr=stderr,
            exit_code=exit_code,
            hint=_AUTH_HINT,
        )

    if any(p.search(text) for p in _RATE_LIMIT_PATTERNS):
        return GhFailure(
            kind=GhFailureKind.RATE_LIMIT,
            stderr=stderr,
            exit_code=exit_code,
            hint=None,
        )

    if any(p.search(text) for p in _NETWORK_PATTERNS):
        return GhFailure(
            kind=GhFailureKind.NETWORK,
            stderr=stderr,
            exit_code=exit_code,
            hint=None,
        )

    return GhFailure(
        kind=GhFailureKind.UNKNOWN,
        stderr=stderr,
        exit_code=exit_code,
        hint=None,
    )


# ---------------------------------------------------------------------------
# Subprocess wrappers — argv only, no shell.
# ---------------------------------------------------------------------------
# These are exercised in L3 (V-1〜V-15) against real ``gh`` / ``git`` and
# in L2 via callable injection through the orchestrator. They deliberately
# stay thin: parse stdout JSON when applicable, surface failures as
# ``PostResult`` / raised ``GhFailure`` so callers never see raw
# ``subprocess.CalledProcessError``.


def _run(argv: list[str], *, cwd: Path, timeout: float = 15.0) -> subprocess.CompletedProcess:
    """Tiny wrapper around ``subprocess.run`` with the project's defaults.

    Always ``capture_output=True``, always ``text=True``, never
    ``shell=True``. ``check`` is left off so callers can inspect
    ``returncode`` and route the failure through ``classify_gh_failure``.
    """
    return subprocess.run(  # noqa: S603 — argv is constructed from validated ints/strings
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def gh_view_comments(issue_number: int, *, cwd: Path) -> list[dict]:
    """Return the ``comments`` array from ``gh issue view --json comments``.

    On non-zero exit, raise the classified ``GhFailure`` so callers can
    distinguish auth/network/rate-limit (R-9.1). Empty stdout returns ``[]``.
    """
    argv = [
        "gh", "issue", "view", str(int(issue_number)),
        "--json", "comments",
    ]
    proc = _run(argv, cwd=cwd)
    if proc.returncode != 0:
        raise classify_gh_failure(proc.stderr, proc.returncode)
    payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    comments = payload.get("comments") if isinstance(payload, dict) else []
    return list(comments or [])


def gh_post_comment(issue_number: int, body: str, *, cwd: Path) -> PostResult:
    """Post a comment via ``gh issue comment <n> --body <body>``.

    Returns :class:`PostResult` always; the caller decides how to
    aggregate failures across multiple candidates (per R-9 the loop
    must not abort on a single failure).
    """
    argv = [
        "gh", "issue", "comment", str(int(issue_number)),
        "--body", body,
    ]
    proc = _run(argv, cwd=cwd)
    if proc.returncode != 0:
        return PostResult(
            ok=False,
            comment_url=None,
            failure=classify_gh_failure(proc.stderr, proc.returncode),
        )
    # gh prints the comment URL on stdout when posting succeeds.
    url = (proc.stdout or "").strip() or None
    return PostResult(ok=True, comment_url=url, failure=None)


def gh_list_in_progress(*, cwd: Path) -> list[int]:
    """Return issue numbers labelled ``status:in-progress`` (Tier 1)."""
    argv = [
        "gh", "issue", "list",
        "--state", "open",
        "--label", "status:in-progress",
        "--json", "number",
        "--limit", "100",
    ]
    proc = _run(argv, cwd=cwd)
    if proc.returncode != 0:
        raise classify_gh_failure(proc.stderr, proc.returncode)
    items = json.loads(proc.stdout) if proc.stdout.strip() else []
    return [int(it["number"]) for it in items if isinstance(it, dict) and "number" in it]


def git_branch(cwd: Path) -> str:
    """Return the current git branch name, or ``""`` when detached / unavailable."""
    argv = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
    try:
        proc = _run(argv, cwd=cwd, timeout=5.0)
    except (subprocess.SubprocessError, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()
