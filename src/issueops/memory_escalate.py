"""Escalate cross-issue Decisions to Claude's standard memory directory.

The caller is responsible for deciding *which* decisions escalate (the
``cross-issue`` scope filter lives in the session-closer skill, not
here). This module purely renders and writes reference-type memory
entries against a given ``memory_dir`` Path.
"""

from __future__ import annotations

from pathlib import Path

from issueops.marker_parser import Decision


def _description(decision: Decision) -> str:
    """Render a one-line YAML-safe description from the decision's What."""
    return " ".join(decision.what.split())


def render_reference_memory(decision: Decision) -> str:
    """Render a Decision as the contents of a reference-type memory file.

    The format follows the memory schema documented in CLAUDE.md:
    YAML frontmatter (``name``, ``description``, ``type``) followed by a
    Markdown body that preserves all four decision fields verbatim.
    """
    description = _description(decision)
    return (
        "---\n"
        f"name: {decision.slug}\n"
        f"description: {description}\n"
        "type: reference\n"
        "---\n"
        "\n"
        f"# {decision.slug}\n"
        "\n"
        f"**What:** {decision.what}\n"
        "\n"
        f"**Why:** {decision.why}\n"
        "\n"
        "**Alternatives considered:**\n"
        f"{decision.alternatives}\n"
        "\n"
        f"**Consequences:** {decision.consequences}\n"
    )


def write_memory_file(decision: Decision, memory_dir: Path) -> Path:
    """Write the reference memory file for ``decision`` under ``memory_dir``.

    The file is named ``reference_<slug>.md`` and overwritten if it
    already exists; we treat the slug as the canonical identity, so a
    re-write reflects the current decision body.
    """
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / f"reference_{decision.slug}.md"
    target.write_text(render_reference_memory(decision))
    return target


def update_memory_index(memory_dir: Path, decision: Decision) -> None:
    """Append a one-line index entry to ``MEMORY.md``, idempotent on slug.

    Creates ``MEMORY.md`` if absent. If a line referencing the same
    ``reference_<slug>.md`` filename already exists, leaves the file
    untouched (slug-based identity, not text-equality based).
    """
    memory_dir.mkdir(parents=True, exist_ok=True)
    index_path = memory_dir / "MEMORY.md"
    filename = f"reference_{decision.slug}.md"
    line = f"- [{decision.slug}]({filename}) — {_description(decision)}\n"

    if index_path.exists():
        existing = index_path.read_text()
        if filename in existing:
            return
        if not existing.endswith("\n"):
            existing += "\n"
        index_path.write_text(existing + line)
    else:
        index_path.write_text(line)
