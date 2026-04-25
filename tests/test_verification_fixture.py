"""Tests for the verification fixture loader (T-95〜T-98).

The loader is the AskUserQuestion bypass mechanism used in L3 verification.
It MUST be guarded by two environment variables to avoid accidental activation:

  - CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE: path to a JSON fixture file.
    Must be a path under ``verification-fixtures/`` (path traversal rejected).
  - CLAUDE_ISSUEOPS_VERIFICATION_MODE=1: explicit opt-in flag.

Both must be present and valid; otherwise the loader returns ``None``.
A clear stderr warning is emitted when one variable is set but the other
is missing/invalid (so misconfigurations are visible).

See: design.md "AskUserQuestion フィクスチャ注入" and test-design.md
Key Decision #7.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from issueops.verification_fixture import load_fixture_or_none


def _write_fixture(fixtures_dir: Path, name: str, payload: dict) -> Path:
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    p = fixtures_dir / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# T-95: Both env vars set correctly -> fixture is loaded.
def test_verification_fixture_loads_when_both_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    fixtures_dir = tmp_path / "verification-fixtures"
    payload = {
        "schema_version": 1,
        "responses": [
            {"question_id": "approve-decisions", "selections": ["a", "b"]},
        ],
    }
    fixture = _write_fixture(fixtures_dir, "v1-approve-all.json", payload)

    monkeypatch.setenv(
        "CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE",
        str(fixture.relative_to(tmp_path)),
    )
    monkeypatch.setenv("CLAUDE_ISSUEOPS_VERIFICATION_MODE", "1")

    result = load_fixture_or_none()

    assert result == payload
    captured = capsys.readouterr()
    # No warning when both vars are correctly set.
    assert captured.err == ""


# T-96: Path is outside verification-fixtures/ -> ignored + stderr warning.
def test_verification_fixture_rejects_path_outside_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    # Place a fixture at an unsafe location (NOT under verification-fixtures/).
    bogus = tmp_path / "evil-dir" / "leak.json"
    bogus.parent.mkdir(parents=True, exist_ok=True)
    bogus.write_text(json.dumps({"schema_version": 1, "responses": []}))

    monkeypatch.setenv("CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE", str(bogus))
    monkeypatch.setenv("CLAUDE_ISSUEOPS_VERIFICATION_MODE", "1")

    result = load_fixture_or_none()

    assert result is None
    captured = capsys.readouterr()
    assert captured.err != ""
    # Warning should mention the path safety reason.
    assert "verification-fixtures" in captured.err.lower()


# T-97: MODE not set or wrong value -> ignored + stderr warning.
def test_verification_fixture_rejects_when_mode_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    fixtures_dir = tmp_path / "verification-fixtures"
    payload = {"schema_version": 1, "responses": []}
    fixture = _write_fixture(fixtures_dir, "v1.json", payload)

    monkeypatch.setenv(
        "CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE",
        str(fixture.relative_to(tmp_path)),
    )
    # MODE is missing entirely.
    monkeypatch.delenv("CLAUDE_ISSUEOPS_VERIFICATION_MODE", raising=False)

    result = load_fixture_or_none()

    assert result is None
    captured = capsys.readouterr()
    assert captured.err != ""
    assert "CLAUDE_ISSUEOPS_VERIFICATION_MODE" in captured.err


# T-98: Neither env var is set -> silent None (no warning).
def test_verification_fixture_silent_when_neither_set(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("CLAUDE_ISSUEOPS_VERIFICATION_FIXTURE", raising=False)
    monkeypatch.delenv("CLAUDE_ISSUEOPS_VERIFICATION_MODE", raising=False)

    result = load_fixture_or_none()

    assert result is None
    captured = capsys.readouterr()
    # In normal operation (neither var set), the loader must be silent.
    assert captured.err == ""
