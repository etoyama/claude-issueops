"""Byte-level slicer for ``transcript.jsonl`` files.

The session-closer skill must read transcripts incrementally so that
each invocation only sees the bytes appended since the previous run
(``state.last_processed_offset``). Reading at byte-level — rather than
parsing JSONL line-by-line — guarantees ``end_offset`` is exactly the
file size, free from line-boundary drift.

``FileNotFoundError`` (and other ``OSError`` subclasses) are intentionally
*not* caught here. The orchestrator decides whether to abort cleanly or
escalate, per Test Design R-3.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# 64 KiB chunk — small enough to keep RSS bounded for very large
# transcripts, large enough to avoid syscall overhead on typical sizes.
_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class TranscriptSlice:
    """Result of a transcript read.

    Attributes:
        content: UTF-8 decoded bytes from ``offset`` to EOF.
        end_offset: Total file size in bytes after the read. Persist this
            value to ``state.last_processed_offset`` to make the next
            read pick up exactly where this one ended.
    """

    content: str
    end_offset: int


def read_transcript_since(
    transcript_path: Path, *, offset: int = 0
) -> TranscriptSlice:
    """Return transcript content from ``offset`` to EOF.

    Args:
        transcript_path: Path to ``transcript.jsonl``.
        offset: Byte position to seek to before reading. ``0`` reads the
            entire file.

    Returns:
        ``TranscriptSlice`` with the decoded content and the new
        ``end_offset`` (= file size after the read).

    Raises:
        FileNotFoundError: If ``transcript_path`` does not exist. The
            orchestrator catches this and aborts without touching state
            (R-3.4).
        OSError: For other I/O failures (also propagated).
    """
    chunks: list[bytes] = []
    bytes_read = 0
    with open(transcript_path, "rb") as fh:
        if offset:
            fh.seek(offset)
        while True:
            buf = fh.read(_CHUNK_SIZE)
            if not buf:
                break
            chunks.append(buf)
            bytes_read += len(buf)

    content = b"".join(chunks).decode("utf-8")
    return TranscriptSlice(content=content, end_offset=offset + bytes_read)
