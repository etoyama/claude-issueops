"""L1 unit tests for ``issueops.gh_adapters``.

Test ID coverage (per test-design.md L1 table):
- T-71: ``classify_gh_failure`` distinguishes the four failure kinds
  (network / auth / rate-limit / unknown) using stderr fragments and
  exit codes. Case-insensitive matching is required (design § Component
  8 explicitly notes ``Authentication failed`` must match alongside
  the lowercase ``authentication``).
- T-72: ``auth`` classification carries a non-empty ``hint`` so SKILL.md
  can surface ``gh auth status を実行してください`` in the 3-choice
  failure dialog (R-9.2). Non-auth kinds leave ``hint`` as ``None``.

Per Tasks § 7 prompt: subprocess wrappers (gh_view_comments,
gh_post_comment, gh_list_in_progress, git_branch) are *not* unit-tested
here — their behaviour is covered in L3 verification (V-1〜V-15) where
real ``gh`` / ``git`` are invoked. Tests in this module focus solely on
the pure ``classify_gh_failure`` logic.
"""

from __future__ import annotations

import pytest

from issueops.gh_adapters import GhFailure, GhFailureKind, classify_gh_failure


# ---------------------------------------------------------------------------
# T-71: classify_gh_failure 4-way branching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stderr, exit_code, expected_kind",
    [
        # AUTH — lowercase
        ("authentication required", 1, GhFailureKind.AUTH),
        # AUTH — case-insensitive (design Component 8 callout)
        ("Authentication failed", 1, GhFailureKind.AUTH),
        # AUTH — gh's typical "auth status" pointer
        ("gh: To authenticate, please run gh auth status", 4, GhFailureKind.AUTH),
        # AUTH — HTTP 401
        ("HTTP 401: Bad credentials", 1, GhFailureKind.AUTH),
        # RATE_LIMIT
        ("API rate limit exceeded", 1, GhFailureKind.RATE_LIMIT),
        ("HTTP 429: Too Many Requests", 1, GhFailureKind.RATE_LIMIT),
        # NETWORK
        ("Could not resolve host: api.github.com", 1, GhFailureKind.NETWORK),
        ("connection refused", 1, GhFailureKind.NETWORK),
        ("dial tcp: i/o timeout", 1, GhFailureKind.NETWORK),
        # UNKNOWN — fall-through
        ("something else broke", 1, GhFailureKind.UNKNOWN),
        ("", 2, GhFailureKind.UNKNOWN),
    ],
)
def test_classify_gh_failure_4_kinds(
    stderr: str, exit_code: int, expected_kind: GhFailureKind
) -> None:
    failure = classify_gh_failure(stderr, exit_code)

    assert isinstance(failure, GhFailure)
    assert failure.kind == expected_kind
    assert failure.stderr == stderr
    assert failure.exit_code == exit_code


# ---------------------------------------------------------------------------
# T-72: auth → hint populated; non-auth → hint is None
# ---------------------------------------------------------------------------


def test_classify_gh_failure_auth_hint() -> None:
    """AUTH classification must include a non-empty hint string.

    The hint is what SKILL.md surfaces verbatim in the 3-choice dialog.
    Other kinds intentionally leave ``hint`` as ``None`` (per design
    Component 8 — only AUTH carries a hint).
    """
    auth = classify_gh_failure("HTTP 401: Bad credentials", 1)
    assert auth.kind == GhFailureKind.AUTH
    assert auth.hint is not None
    assert "gh auth" in auth.hint.lower()

    # Non-auth kinds carry no hint
    network = classify_gh_failure("Could not resolve host", 1)
    assert network.hint is None

    rate = classify_gh_failure("API rate limit exceeded", 1)
    assert rate.hint is None

    unknown = classify_gh_failure("???", 7)
    assert unknown.hint is None


# ---------------------------------------------------------------------------
# #32: stderr redaction (Authorization / token / bearer + truncation)
# ---------------------------------------------------------------------------


def test_classify_gh_failure_redacts_authorization_header() -> None:
    """An ``Authorization:`` line in stderr must not survive into GhFailure.stderr.

    gh / its underlying HTTP client occasionally surfaces request headers
    on verbose-error paths. Storing them raw lets the bin response
    (and any logs the skill writes) republish credentials.
    """
    raw = (
        "HTTP 401: Bad credentials\n"
        "Authorization: Bearer ghp_supersecret_tokenvalue123\n"
        "request id: abc123"
    )
    failure = classify_gh_failure(raw, 1)

    # Classification still works — uses raw text for matching, not the
    # stored field — so AUTH wins.
    assert failure.kind == GhFailureKind.AUTH
    # The sensitive line is gone.
    assert "ghp_supersecret_tokenvalue123" not in failure.stderr
    assert "Authorization:" not in failure.stderr
    # Replaced with the placeholder rather than silently dropped.
    assert "[redacted]" in failure.stderr
    # Surrounding context is preserved.
    assert "Bad credentials" in failure.stderr
    assert "request id" in failure.stderr


def test_classify_gh_failure_redacts_bearer_and_token_query() -> None:
    """``bearer <…>`` and ``token=<…>`` patterns are also stripped."""
    bearer = classify_gh_failure("warning: bearer ghs_xxx will expire", 1)
    assert "ghs_xxx" not in bearer.stderr
    assert "[redacted]" in bearer.stderr

    token_query = classify_gh_failure("called https://api.github.com/x?token=abc&y=1", 1)
    assert "abc" not in token_query.stderr
    assert "[redacted]" in token_query.stderr


def test_classify_gh_failure_truncates_long_stderr() -> None:
    """Very long stderr is truncated so the JSON response stays bounded."""
    # 500-char benign stderr (no sensitive patterns).
    long_text = "x" * 500
    failure = classify_gh_failure(long_text, 1)

    assert failure.kind == GhFailureKind.UNKNOWN
    assert len(failure.stderr) <= 200
    # Truncation marker present so consumers can tell it was trimmed.
    assert failure.stderr.endswith("...")


def test_classify_gh_failure_passes_through_benign_stderr_unchanged() -> None:
    """Short, non-sensitive stderr round-trips verbatim (no over-redaction)."""
    raw = "Could not resolve host: api.github.com"
    failure = classify_gh_failure(raw, 1)
    assert failure.kind == GhFailureKind.NETWORK
    assert failure.stderr == raw
