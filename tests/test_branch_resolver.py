"""Tests for branch -> issue number extraction and current-issue resolution.

The default branch naming convention is documented in CONTRIBUTING.md.
The pattern is configurable via plugin settings; these tests pin the
default behavior so downstream tooling can rely on it.
"""

from __future__ import annotations

import pytest

from issueops.branch_resolver import (
    DEFAULT_BRANCH_PATTERN,
    extract_issue_number,
    resolve_current_issue,
)


@pytest.mark.parametrize(
    "branch, expected",
    [
        ("feat/132-session-continuity", 132),
        ("fix/45-typo-in-readme", 45),
        ("chore/7-update-deps", 7),
        ("refactor/100-rename-module", 100),
    ],
)
def test_default_pattern_extracts_issue_number(branch: str, expected: int):
    assert extract_issue_number(branch, DEFAULT_BRANCH_PATTERN) == expected


@pytest.mark.parametrize(
    "branch",
    [
        "main",
        "master",
        "develop",
        "feat/no-number-here",
        "wip",
        "",
    ],
)
def test_default_pattern_returns_none_when_no_match(branch: str):
    assert extract_issue_number(branch, DEFAULT_BRANCH_PATTERN) is None


def test_resolve_returns_extracted_number_without_calling_fallback():
    calls = []

    def fallback_fn() -> int | None:
        calls.append("called")
        return 999

    result = resolve_current_issue(
        "feat/132-session-continuity",
        fallback_fn=fallback_fn,
    )
    assert result == 132
    assert calls == []  # fallback must not run when extract succeeds


def test_resolve_with_fallback_none_returns_none_on_no_match():
    def fallback_fn() -> int | None:
        return 999

    result = resolve_current_issue(
        "main",
        fallback="none",
        fallback_fn=fallback_fn,
    )
    assert result is None  # fallback="none" overrides any fallback_fn


def test_resolve_with_latest_in_progress_calls_fallback_when_no_match():
    def fallback_fn() -> int | None:
        return 42

    result = resolve_current_issue(
        "main",
        fallback="latest-in-progress",
        fallback_fn=fallback_fn,
    )
    assert result == 42


def test_resolve_returns_none_when_fallback_fn_returns_none():
    def fallback_fn() -> int | None:
        return None

    result = resolve_current_issue(
        "main",
        fallback="latest-in-progress",
        fallback_fn=fallback_fn,
    )
    assert result is None


def test_resolve_raises_on_unknown_fallback_strategy():
    with pytest.raises(ValueError, match="unknown fallback"):
        resolve_current_issue(
            "main",
            fallback="invalid-strategy",
            fallback_fn=lambda: 42,
        )


def test_resolve_raises_when_latest_in_progress_without_fallback_fn():
    with pytest.raises(ValueError, match="fallback_fn"):
        resolve_current_issue(
            "main",
            fallback="latest-in-progress",
            fallback_fn=None,
        )


@pytest.mark.parametrize(
    "branch, pattern, expected",
    [
        # custom prefix
        ("issue-132/refactor", r"issue-(\d+)/", 132),
        # bare-number style
        ("132-session-continuity", r"^(\d+)-", 132),
        # multi-digit issue numbers
        ("feat/1234567-very-old-issue", DEFAULT_BRANCH_PATTERN, 1234567),
    ],
)
def test_extract_with_custom_patterns(branch: str, pattern: str, expected: int):
    assert extract_issue_number(branch, pattern) == expected


def test_first_match_wins_when_branch_contains_multiple_candidates():
    # The branch coincidentally contains a second number at the tail.
    # Default pattern's anchor (`<type>/<num>-`) ensures we capture the leading one.
    assert extract_issue_number("feat/132-fixes-issue-456", DEFAULT_BRANCH_PATTERN) == 132
