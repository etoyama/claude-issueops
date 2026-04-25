"""Parse decision markers from GitHub issue comments.

The marker protocol is frozen. See README.md and CONTRIBUTING.md for the
human-facing spec. This module is the canonical implementation that all
downstream tooling depends on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    """One decision recorded under the frozen marker protocol.

    Fields correspond 1:1 to the four required sections of the marker:
    ``**What:**``, ``**Why:**``, ``**Alternatives considered:**``, and
    ``**Consequences:**``. ``slug`` is the kebab-case identifier from
    the heading line ``## Decision: <slug>`` and is unique within an
    issue (the parser does not enforce uniqueness; the capture flow
    does).
    """

    slug: str
    what: str
    why: str
    alternatives: str
    consequences: str


_HEADING_RE = re.compile(r"^## Decision: (?P<slug>[a-z0-9-]+)\s*$", re.MULTILINE)

_CODE_BLOCK_RE = re.compile(
    r"^[ ]{0,3}```.*?^[ ]{0,3}```\s*$",
    re.MULTILINE | re.DOTALL,
)


def _strip_code_blocks(text: str) -> str:
    return _CODE_BLOCK_RE.sub("", text)


def _extract_field(block: str, name: str) -> str:
    pattern = re.compile(
        r"^\*\*" + re.escape(name) + r":\*\*\s*(.+?)(?=^\*\*[A-Z][^*]*:\*\*|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    m = pattern.search(block)
    return m.group(1).strip() if m else ""


def parse_decisions(text: str) -> list[Decision]:
    """Extract every valid decision marker from ``text``.

    Markers inside fenced code blocks are stripped before matching so
    documentation examples and snippets do not produce false positives.
    Markers preceded by a blockquote ``>`` prefix are naturally rejected
    because the heading regex requires the line to start with ``##``.
    Slugs that are not kebab-case are skipped at the heading-match step.
    Decisions missing any of the four required fields are skipped.
    """
    text = _strip_code_blocks(text)
    matches = list(_HEADING_RE.finditer(text))
    decisions: list[Decision] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]

        what = _extract_field(block, "What")
        why = _extract_field(block, "Why")
        alternatives = _extract_field(block, "Alternatives considered")
        consequences = _extract_field(block, "Consequences")

        if not all([what, why, alternatives, consequences]):
            continue

        decisions.append(
            Decision(
                slug=m.group("slug"),
                what=what,
                why=why,
                alternatives=alternatives,
                consequences=consequences,
            )
        )
    return decisions
