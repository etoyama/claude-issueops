"""T-91: SKILL.md frontmatter parse + structural validation.

Doc-as-data validation only. Skill trigger behavior is covered by L3 V-14.

The repo intentionally avoids adding pyyaml as a dependency, so this test
implements a minimal stdlib-only YAML frontmatter parser sufficient for the
SKILL.md schema (scalar `name`, multi-line `description`, list `triggers`).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = REPO_ROOT / "skills" / "session-closer" / "SKILL.md"


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split a markdown file into (frontmatter, body).

    Frontmatter is delimited by `---` on its own line at start and end.
    Returns ("", text) if no frontmatter is present.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            fm = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1 :])
            return fm, body
    raise ValueError("frontmatter opening `---` found but no closing `---`")


def _parse_minimal_yaml(fm: str) -> dict:
    """Parse a minimal YAML subset: scalar values, block scalars (`|`), and
    list-of-strings (lines starting with `  - `).

    Sufficient for SKILL.md frontmatter; not a general YAML parser.
    """
    result: dict = {}
    current_key: str | None = None
    current_mode: str | None = None  # "scalar" | "block" | "list"
    block_lines: list[str] = []
    list_items: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_mode, block_lines, list_items
        if current_key is None:
            return
        if current_mode == "block":
            result[current_key] = "\n".join(block_lines).rstrip("\n")
        elif current_mode == "list":
            result[current_key] = list(list_items)
        block_lines = []
        list_items = []
        current_key = None
        current_mode = None

    for raw in fm.splitlines():
        # List item under the active key
        list_match = re.match(r"^\s*-\s+(.*)$", raw)
        if current_mode == "list" and list_match:
            item = list_match.group(1).strip().strip('"').strip("'")
            list_items.append(item)
            continue

        # Block scalar continuation (indented non-empty line under `key: |`)
        if current_mode == "block" and (raw.startswith("  ") or raw == ""):
            # strip the 2-space block indent if present
            block_lines.append(raw[2:] if raw.startswith("  ") else raw)
            continue

        # New key at top level
        kv = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", raw)
        if kv:
            flush()
            key, val = kv.group(1), kv.group(2)
            if val == "|" or val == ">":
                current_key = key
                current_mode = "block"
            elif val == "":
                current_key = key
                current_mode = "list"
            else:
                # scalar value (strip optional surrounding quotes)
                stripped = val.strip()
                if (stripped.startswith('"') and stripped.endswith('"')) or (
                    stripped.startswith("'") and stripped.endswith("'")
                ):
                    stripped = stripped[1:-1]
                result[key] = stripped
                current_key = None
                current_mode = None
            continue

        # Otherwise ignore (blank line, comment, etc.)

    flush()
    return result


def test_skill_md_frontmatter_parse() -> None:
    """T-91: Validate SKILL.md frontmatter and orchestration body.

    Asserts:
      1. Frontmatter parses successfully as YAML.
      2. `name == "session-closer"`.
      3. `description` contains both "capture" and "close" (case-insensitive).
      4. `triggers` is a list with >= 5 items.
      5. Body contains the literal strings `schema_version` and `AskUserQuestion`.
    """
    text = SKILL_PATH.read_text(encoding="utf-8")
    fm_text, body = _split_frontmatter(text)

    # (1) frontmatter parses
    assert fm_text, "SKILL.md must have a YAML frontmatter block delimited by ---"
    fm = _parse_minimal_yaml(fm_text)
    assert isinstance(fm, dict) and fm, "frontmatter must parse to a non-empty dict"

    # (2) name
    assert fm.get("name") == "session-closer", f"unexpected name: {fm.get('name')!r}"

    # (3) description has both keywords (case-insensitive)
    description = fm.get("description", "")
    assert isinstance(description, str) and description, "description must be a non-empty string"
    desc_lower = description.lower()
    assert "capture" in desc_lower, "description must mention 'capture' mode"
    assert "close" in desc_lower, "description must mention 'close' mode"

    # (4) triggers list >= 5
    triggers = fm.get("triggers")
    assert isinstance(triggers, list), f"triggers must be a list, got {type(triggers).__name__}"
    assert len(triggers) >= 5, f"triggers must have >= 5 entries, got {len(triggers)}"
    assert all(isinstance(t, str) and t.strip() for t in triggers), "all triggers must be non-empty strings"

    # (5) body must reference schema_version and AskUserQuestion
    assert "schema_version" in body, "body must reference 'schema_version' (skill ↔ bin contract)"
    assert "AskUserQuestion" in body, "body must reference 'AskUserQuestion' (skill owns user dialog)"
