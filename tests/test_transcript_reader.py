"""L1 unit tests for ``transcript_reader``.

Covers Test Design IDs:

- T-01: ``test_read_transcript_since_default_offset`` (R-3.1)
- T-02: ``test_read_transcript_missing_raises`` (R-3.4)
- T-03: ``test_read_transcript_partial_offset`` (R-3.1)

The module reads ``transcript.jsonl`` files at byte-level so partial
reads from ``last_processed_offset`` are deterministic across hooks.
``FileNotFoundError`` propagates so the orchestrator can decide to
abort cleanly without touching state.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_read_transcript_since_default_offset(tmp_path: Path) -> None:
    """T-01: ``offset=0`` returns full content and ``end_offset == len(bytes)``."""
    from issueops.transcript_reader import TranscriptSlice, read_transcript_since

    transcript = tmp_path / "transcript.jsonl"
    payload = (
        '{"role":"user","content":"hello"}\n'
        '{"role":"assistant","content":"world"}\n'
    )
    transcript.write_text(payload, encoding="utf-8")

    sliced = read_transcript_since(transcript)

    assert isinstance(sliced, TranscriptSlice)
    assert sliced.content == payload
    assert sliced.end_offset == len(payload.encode("utf-8"))


def test_read_transcript_missing_raises(tmp_path: Path) -> None:
    """T-02: missing transcript propagates ``FileNotFoundError`` to caller."""
    from issueops.transcript_reader import read_transcript_since

    missing = tmp_path / "no-such-transcript.jsonl"

    with pytest.raises(FileNotFoundError):
        read_transcript_since(missing)


def test_read_transcript_partial_offset(tmp_path: Path) -> None:
    """T-03: ``offset>0`` skips the prefix and ``end_offset`` matches file size."""
    from issueops.transcript_reader import read_transcript_since

    transcript = tmp_path / "transcript.jsonl"
    line1 = '{"role":"user","content":"first"}\n'
    line2 = '{"role":"assistant","content":"second"}\n'
    payload = line1 + line2
    transcript.write_text(payload, encoding="utf-8")

    line1_bytes = len(line1.encode("utf-8"))
    sliced = read_transcript_since(transcript, offset=line1_bytes)

    assert sliced.content == line2
    assert sliced.end_offset == len(payload.encode("utf-8"))


def test_read_transcript_offset_at_eof_returns_empty(tmp_path: Path) -> None:
    """Boundary: offset == file size returns empty content with end_offset stable."""
    from issueops.transcript_reader import read_transcript_since

    transcript = tmp_path / "transcript.jsonl"
    payload = "abc\n"
    transcript.write_text(payload, encoding="utf-8")

    size = len(payload.encode("utf-8"))
    sliced = read_transcript_since(transcript, offset=size)

    assert sliced.content == ""
    assert sliced.end_offset == size
