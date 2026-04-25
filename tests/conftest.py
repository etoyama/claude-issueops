"""Shared pytest fixtures for the session-closer skill test suite.

These fixtures centralise the dependency-injection stubs used by the
L1 / L2 tests for the session-closer skill (see Test Design § "DI スタブ仕様").
They are intentionally callable factories so each test can program its
own return values / call history without sharing state across tests.

Time injection uses ``freeze_now`` (no ``freezegun`` dependency) — pure
modules accept ``now=`` callable / datetime so we never need to monkey-patch
``datetime.now``.

Test ID coverage (per Test Design):
- ``project_dir``      : T-21, T-101〜T-136, T-61〜T-67, T-81〜T-82
- ``freeze_now``       : T-62, T-104, T-105, T-129, T-132, T-133
- ``gh_post_fn_factory``        : T-21, T-101, T-103, T-108, T-128, T-129, T-130, T-131, T-132, T-133, T-136
- ``gh_view_comments_fn_factory`` : T-31, T-42, T-113, T-120, T-121
- ``gh_list_in_progress_fn_factory`` : T-51〜T-55, T-122
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Return a temporary ``project_dir`` with ``session-state/`` pre-created.

    Used by every state-writing test (T-61〜T-67, T-81〜T-82, T-101〜T-136
    and the refactor-compat tests T-132/T-133). The ``session-state``
    subdirectory is created eagerly so callers can drop pre-existing
    state files in the same place that ``state_writer`` would write.
    """
    (tmp_path / "session-state").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def freeze_now() -> Callable[..., datetime]:
    """Return a callable producing a fixed ``datetime`` for ``now=`` injection.

    Used by tests that need deterministic ``skill_ran_at`` / ``saved_at``
    timestamps (T-62, T-104, T-105, T-129, T-132, T-133). Default value
    is ``2026-04-25T12:00:00+00:00``; callers can override per-call.

    Usage::

        def test_x(freeze_now):
            fixed = freeze_now()                       # default
            other = freeze_now(year=2030, month=1)     # override fields
    """

    def _factory(
        year: int = 2026,
        month: int = 4,
        day: int = 25,
        hour: int = 12,
        minute: int = 0,
        second: int = 0,
        microsecond: int = 0,
        tz: timezone = timezone.utc,
    ) -> datetime:
        return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=tz)

    return _factory


@pytest.fixture
def gh_post_fn_factory() -> Callable[..., Callable[..., Any]]:
    """Build a stub for ``gh_post_comment``-style callables.

    Used by T-21, T-101, T-103, T-108, T-128, T-129, T-130, T-131, T-132,
    T-133 and T-136 (subcommand-separation contract). The returned
    callable accepts ``(issue_number, body)`` (matching ``PostCommentFn``
    used by ``session_end``) **and** is also tolerant of the richer
    ``(issue_number, body, *, cwd=...)`` signature that ``gh_adapters``
    will use once Tasks 7+ land — extra kwargs are ignored.

    Each call pops the next entry from ``results`` (or raises if no
    results left). Pass plain values to be returned, or ``Exception``
    instances to raise. The factory exposes ``calls`` so tests can
    assert on call history.

    Example::

        gh_post = gh_post_fn_factory(results=[None, RuntimeError("boom")])
        gh_post(10, "body 1")           # returns None
        gh_post(10, "body 2")           # raises RuntimeError
        assert gh_post.calls == [(10, "body 1"), (10, "body 2")]
    """

    def _factory(*, results: list[Any] | None = None) -> Callable[..., Any]:
        queue = list(results or [])
        calls: list[tuple] = []

        def _fn(*args: Any, **kwargs: Any) -> Any:
            calls.append(args + (tuple(sorted(kwargs.items())),) if kwargs else args)
            if not queue:
                return None
            value = queue.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value

        _fn.calls = calls  # type: ignore[attr-defined]
        return _fn

    return _factory


@pytest.fixture
def gh_view_comments_fn_factory() -> Callable[..., Callable[..., list[dict]]]:
    """Build a stub returning canned ``gh issue view --json comments`` payloads.

    Used by T-31, T-42, T-113, T-120 (Tier 2 dedup), T-121 (gh failure
    fallback). Each call pops the next entry from ``results``; if the
    entry is an ``Exception`` instance it is raised (lets callers verify
    the gh-failure fallback path).

    The stub accepts both ``(issue_number)`` and ``(issue_number, *, cwd=...)``
    signatures (gh_adapters wrapper) — extra kwargs are ignored.
    """

    def _factory(
        *, results: list[list[dict] | BaseException] | None = None
    ) -> Callable[..., list[dict]]:
        queue = list(results or [])
        calls: list = []

        def _fn(*args: Any, **kwargs: Any) -> list[dict]:
            calls.append((args, kwargs))
            if not queue:
                return []
            value = queue.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value  # type: ignore[return-value]

        _fn.calls = calls  # type: ignore[attr-defined]
        return _fn

    return _factory


@pytest.fixture
def gh_list_in_progress_fn_factory() -> Callable[..., Callable[..., list[int]]]:
    """Build a stub for ``gh_list_in_progress`` returning canned issue lists.

    Used by T-51〜T-55 (issue_resolver state-transition table) and T-122
    (resolution-error orchestration path).

    Each call returns the same ``issues`` list by default. Pass
    ``results=[...]`` to vary across multiple invocations (rare).
    """

    def _factory(
        *,
        issues: list[int] | None = None,
        results: list[list[int] | BaseException] | None = None,
    ) -> Callable[..., list[int]]:
        queue = list(results) if results is not None else None
        calls: list = []

        def _fn(*args: Any, **kwargs: Any) -> list[int]:
            calls.append((args, kwargs))
            if queue is None:
                return list(issues or [])
            if not queue:
                return []
            value = queue.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value  # type: ignore[return-value]

        _fn.calls = calls  # type: ignore[attr-defined]
        return _fn

    return _factory
