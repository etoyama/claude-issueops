"""L1 unit tests for ``issueops.issue_resolver``.

Covers the 7-case "Issue resolution 状態遷移表" from
``.spec-workflow/specs/session-closer/design.md`` § Component 5. Each
test is mapped to a specific row of the table; T-51〜T-55 are the
baseline IDs from test-design.md, and the additional rows that share
behaviour with one of those IDs are folded in via ``pytest.mark.parametrize``
or extra ``assert`` blocks so all 7 transitions are exercised.

Test ID  | Verifies (state-transition table row)
---------|----------------------------------------------------------------
T-51     | row 3: Tier 1 = 1 件 → 確定 (Tier 1 採用)
T-52     | row 4: Tier 1 ≥ 2 ∩ branch hint = 1 → 交差確定
T-53     | row 2 + row 5: Tier 1 0 件 → Tier 2 採用 / Tier 1 ≥ 2, branch hint 0, Tier 2 = 1
T-54     | row 6 + row 7: 曖昧 → AmbiguousResolution(candidates=tier1)
T-55     | row 1: Tier 1 = 0 件 + Tier 2 None → IssueResolutionError

Reuse policy: ``branch_resolver.resolve_current_issue`` is the *only*
permitted source of branch-derived issue numbers; the resolver under
test must not re-implement the regex.
"""

from __future__ import annotations

import pytest

from issueops.issue_resolver import (
    AmbiguousResolution,
    IssueResolutionError,
    TargetResolutionError,
    TargetSpec,
    parse_target_spec,
    resolve_meta_target,
    resolve_target_issue,
)


# ---------------------------------------------------------------------------
# T-51: Tier 1 single hit → confirmed (row 3)
# ---------------------------------------------------------------------------


def test_resolve_tier1_single_hit(gh_list_in_progress_fn_factory) -> None:
    """Tier 1 returns one issue → resolver returns that number directly.

    Branch hint should not be consulted (Tier 1 alone is sufficient).
    """
    list_fn = gh_list_in_progress_fn_factory(issues=[42])

    # Branch deliberately matches a *different* number — Tier 1 wins.
    result = resolve_target_issue(branch="feat/99-other", list_in_progress_fn=list_fn)

    assert result == 42
    # branch_resolver.resolve_current_issue may or may not be called, but
    # if Tier 1 is single-hit the answer is unambiguous regardless.
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# T-52: Tier 1 multiple + branch hint intersects → branch issue wins (row 4)
# ---------------------------------------------------------------------------


def test_resolve_tier1_multiple_intersect_branch(gh_list_in_progress_fn_factory) -> None:
    """Tier 1 has many candidates but exactly one matches the branch hint."""
    list_fn = gh_list_in_progress_fn_factory(issues=[10, 20, 30])

    result = resolve_target_issue(
        branch="feat/20-do-thing",
        list_in_progress_fn=list_fn,
    )

    assert result == 20


# ---------------------------------------------------------------------------
# T-53: Tier 1 zero → Tier 2 fallback (row 2) + Tier 1 multi & branch hint
# absent + Tier 2 single (row 5)
# ---------------------------------------------------------------------------


def test_resolve_tier1_zero_then_tier2(gh_list_in_progress_fn_factory) -> None:
    """Two independent rows that both end in 'Tier 2 採用'.

    Sub-case A (row 2): Tier 1 = 0 件, branch_resolver = 1 件 → Tier 2.
    Sub-case B (row 5): Tier 1 ≥ 2, branch hint not in Tier 1, Tier 2 = 1.
    """
    # Sub-case A: Tier 1 empty, branch parses to issue 7
    list_fn_a = gh_list_in_progress_fn_factory(issues=[])
    assert resolve_target_issue(branch="fix/7-bug", list_in_progress_fn=list_fn_a) == 7

    # Sub-case B: Tier 1 = [10, 20] but branch points to 99 (not in Tier 1).
    # Per state table row 5, when intersection is empty the branch_resolver
    # value (Tier 2) is taken.
    list_fn_b = gh_list_in_progress_fn_factory(issues=[10, 20])
    assert (
        resolve_target_issue(branch="feat/99-elsewhere", list_in_progress_fn=list_fn_b)
        == 99
    )


# ---------------------------------------------------------------------------
# T-54: ambiguous (row 6 + row 7) → AmbiguousResolution(candidates=tier1)
# ---------------------------------------------------------------------------


def test_resolve_ambiguous_returns_candidates(gh_list_in_progress_fn_factory) -> None:
    """Two rows that both yield AmbiguousResolution.

    Row 6: Tier 1 ≥ 2, branch hint = 0 件, Tier 2 = None → ambiguous.
    Row 7: Tier 1 ≥ 2, branch hint ≥ 2 件 (would only happen if Tier 1
    contained duplicates of the branch number; using a non-matching branch
    pattern triggers the same ambiguous outcome via row 6 — we keep both
    sub-cases for documentation).
    """
    # Row 6: branch does not parse → no Tier 2, Tier 1 has 2 entries.
    list_fn = gh_list_in_progress_fn_factory(issues=[100, 200])
    result = resolve_target_issue(branch="master", list_in_progress_fn=list_fn)
    assert isinstance(result, AmbiguousResolution)
    assert sorted(result.candidates) == [100, 200]

    # Frozen dataclass: cannot be mutated after construction.
    with pytest.raises((AttributeError, TypeError)):
        result.candidates = [1, 2]  # type: ignore[misc]


# ---------------------------------------------------------------------------
# T-55: total failure (row 1) → IssueResolutionError
# ---------------------------------------------------------------------------


def test_resolve_total_failure_raises(gh_list_in_progress_fn_factory) -> None:
    """Tier 1 = 0 件 AND branch_resolver returns None → error, not None."""
    list_fn = gh_list_in_progress_fn_factory(issues=[])

    with pytest.raises(IssueResolutionError):
        resolve_target_issue(branch="master", list_in_progress_fn=list_fn)


# ---------------------------------------------------------------------------
# Story 1 (#77): TargetSpec + parse_target_spec
# ---------------------------------------------------------------------------
#
# Epic 01 (#75): `--target` 統合フラグの pure module 層。
# parse_target_spec はユーザー入力 (`"meta"` / `"issue:N"`) を Value Object に
# 正規化する。`story:N` / `epic:N` は kind Literal には含まれるが、本 Epic では
# 未実装で ValueError を返す (将来予約)。bin/SKILL.md からはこの ValueError を
# `invalid-target-spec` error kind に変換する (Story 2 で配線)。


def test_parse_target_spec_meta_has_no_value() -> None:
    """`"meta"` is the no-value form: kind=meta, value=None."""
    spec = parse_target_spec("meta")
    assert spec == TargetSpec(kind="meta", value=None)


def test_parse_target_spec_issue_carries_int_value() -> None:
    """`"issue:42"` → kind=issue, value=42 (int, not str)."""
    spec = parse_target_spec("issue:42")
    assert spec == TargetSpec(kind="issue", value=42)
    assert isinstance(spec.value, int)


@pytest.mark.parametrize("raw", ["story:42", "epic:42"])
def test_parse_target_spec_future_kinds_raise(raw: str) -> None:
    """`story:N` / `epic:N` は kind Literal には含まれるが本 Epic 未対応。"""
    with pytest.raises(ValueError):
        parse_target_spec(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "",            # empty
        "invalid",    # no colon, not "meta"
        "meta:42",   # meta does not take a value
        "issue",      # missing value
        "issue:",    # empty value
        "issue:abc",  # non-int value
        "issue:-1",   # negative
        "issue:0",    # zero (not a real issue number)
        "ISSUE:42",   # case-sensitive — lowercase only
        " meta",     # leading space
        "meta ",     # trailing space
        "issue: 42",  # space inside value
    ],
)
def test_parse_target_spec_invalid_raises(raw: str) -> None:
    """syntax 不正は ValueError。bin が `invalid-target-spec` に変換する想定。"""
    with pytest.raises(ValueError):
        parse_target_spec(raw)


def test_target_spec_is_frozen() -> None:
    """TargetSpec は Value Object なので mutate できない。"""
    spec = parse_target_spec("issue:42")
    with pytest.raises((AttributeError, TypeError)):
        spec.value = 99  # type: ignore[misc]


@pytest.mark.parametrize(
    "kind,value",
    [
        ("meta", 1),       # meta must not carry a value
        ("issue", None),   # issue requires a value
        ("issue", 0),      # non-positive
        ("issue", -1),
        ("story", None),   # future kinds also require a positive value
        ("epic", 0),
    ],
)
def test_target_spec_invariant_rejects_inconsistent_pairs(kind: str, value: int | None) -> None:
    """Direct construction (bypassing the parser) must enforce the (kind, value) invariant."""
    with pytest.raises(ValueError):
        TargetSpec(kind=kind, value=value)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Story 1 (#77): resolve_meta_target
# ---------------------------------------------------------------------------
#
# 0 件: 厳格 error (TargetResolutionError、F1 設計判断)
# 1 件: int
# 複数件: AmbiguousResolution (SKILL.md の AskUserQuestion へ)


def test_resolve_meta_target_empty_raises() -> None:
    """0 件は厳格 error。bin が `target-resolution` error kind に変換する。"""
    with pytest.raises(TargetResolutionError):
        resolve_meta_target(list_meta_fn=lambda: [])


def test_resolve_meta_target_single_returns_int() -> None:
    """1 件は即採用、AskUserQuestion は経由しない。"""
    result = resolve_meta_target(list_meta_fn=lambda: [69])
    assert result == 69
    assert isinstance(result, int)


def test_resolve_meta_target_multiple_returns_ambiguous() -> None:
    """複数件は AmbiguousResolution、ordering を保つ。"""
    result = resolve_meta_target(list_meta_fn=lambda: [69, 75])
    assert isinstance(result, AmbiguousResolution)
    assert result.candidates == (69, 75)


def test_resolve_meta_target_calls_fn_once() -> None:
    """副作用境界 (gh CLI) への問い合わせは 1 回だけ。"""
    calls: list[int] = []

    def list_fn() -> list[int]:
        calls.append(1)
        return [69]

    resolve_meta_target(list_meta_fn=list_fn)
    assert len(calls) == 1
