"""Tests for memory escalation: writing cross-issue Decisions to Claude's
standard memory directory as reference-type entries.

The render function is pure and unit-testable. The write functions take
a memory_dir Path so we can test against tmp_path without touching the
real ~/.claude/projects/<hash>/memory/ tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from issueops.marker_parser import Decision
from issueops.memory_escalate import (
    render_reference_memory,
    update_memory_index,
    write_memory_file,
)


@pytest.fixture
def sample_decision() -> Decision:
    return Decision(
        slug="merge-strategy-no-ff",
        what="Merge PRs with --no-ff so each per-issue commit stays on master.",
        why="Issue-to-commit 1:1 mapping keeps git log and the issue tracker mutually queryable.",
        alternatives="- squash -> rejected, loses issue granularity.\n- rebase -> rejected, PR boundary disappears.",
        consequences="(+) bisect at issue granularity. (-) merge commits accumulate.",
    )


# ---- render_reference_memory ----


def test_render_emits_frontmatter_with_required_fields(sample_decision: Decision):
    text = render_reference_memory(sample_decision)
    # Frontmatter starts the file
    assert text.startswith("---\n")
    # All three frontmatter keys are present
    assert "\nname: merge-strategy-no-ff\n" in text
    assert "\ntype: reference\n" in text
    assert "\ndescription: " in text
    # Frontmatter closes
    assert "\n---\n" in text[4:]  # skip the leading "---\n"


def test_render_body_contains_all_four_fields(sample_decision: Decision):
    text = render_reference_memory(sample_decision)
    assert "Merge PRs with --no-ff" in text  # what
    assert "1:1 mapping" in text  # why
    assert "squash -> rejected" in text  # alternatives
    assert "bisect at issue granularity" in text  # consequences


def test_render_is_deterministic(sample_decision: Decision):
    a = render_reference_memory(sample_decision)
    b = render_reference_memory(sample_decision)
    assert a == b


# ---- write_memory_file ----


def test_write_creates_reference_file(tmp_path: Path, sample_decision: Decision):
    written = write_memory_file(sample_decision, tmp_path)
    assert written == tmp_path / "reference_merge-strategy-no-ff.md"
    assert written.exists()
    content = written.read_text()
    assert "type: reference" in content
    assert "Merge PRs with --no-ff" in content


def test_write_overwrites_existing_file_idempotently(
    tmp_path: Path, sample_decision: Decision
):
    write_memory_file(sample_decision, tmp_path)
    # Tweak the decision and re-write under the same slug
    revised = Decision(
        slug=sample_decision.slug,
        what="REVISED what.",
        why=sample_decision.why,
        alternatives=sample_decision.alternatives,
        consequences=sample_decision.consequences,
    )
    written = write_memory_file(revised, tmp_path)
    content = written.read_text()
    assert "REVISED what." in content
    assert "Merge PRs with --no-ff" not in content


# ---- update_memory_index ----


def test_update_index_creates_memory_md_when_absent(
    tmp_path: Path, sample_decision: Decision
):
    update_memory_index(tmp_path, sample_decision)
    index = tmp_path / "MEMORY.md"
    assert index.exists()
    line = index.read_text()
    assert "merge-strategy-no-ff" in line
    assert "reference_merge-strategy-no-ff.md" in line


def test_update_index_appends_to_existing_memory_md(
    tmp_path: Path, sample_decision: Decision
):
    index = tmp_path / "MEMORY.md"
    index.write_text("# Memory index\n\n- [pre-existing](reference_pre.md) — keep\n")
    update_memory_index(tmp_path, sample_decision)
    text = index.read_text()
    assert "pre-existing" in text  # kept
    assert "merge-strategy-no-ff" in text  # added


def test_update_index_is_idempotent_for_same_slug(
    tmp_path: Path, sample_decision: Decision
):
    update_memory_index(tmp_path, sample_decision)
    update_memory_index(tmp_path, sample_decision)
    text = (tmp_path / "MEMORY.md").read_text()
    # Slug appears exactly once
    assert text.count("reference_merge-strategy-no-ff.md") == 1
