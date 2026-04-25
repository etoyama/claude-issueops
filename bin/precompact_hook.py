#!/usr/bin/env python3
"""PreCompact hook adapter.

Reads the hook payload from stdin, derives the current branch via
``git``, fetches the issue via ``gh``, and delegates to
:func:`issueops.precompact.run_precompact` to write the per-session
state file. Always exits 0 — PreCompact failures must not block
compaction (D-2 design, see project memory ``project_hook_constraints``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))

from issueops.precompact import run_precompact  # noqa: E402


def _git_branch(cwd: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def _gh_fetch_issue(number: int, *, cwd: Path) -> dict:
    out = subprocess.run(
        ["gh", "issue", "view", str(number), "--json", "title,body,comments"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(out.stdout)


def _gh_latest_in_progress(cwd: Path) -> int | None:
    try:
        out = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "open",
                "--label",
                "status:in-progress",
                "--json",
                "number",
                "--limit",
                "1",
            ],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        items = json.loads(out.stdout) or []
        return items[0]["number"] if items else None
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, KeyError):
        return None


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    session_id = payload.get("session_id") or "unknown"
    cwd = Path(payload.get("cwd") or os.getcwd())
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or cwd)

    branch = _git_branch(cwd)

    run_precompact(
        project_dir=project_dir,
        session_id=session_id,
        branch=branch,
        gh_fetch_fn=lambda n: _gh_fetch_issue(n, cwd=cwd),
        latest_in_progress_fn=lambda: _gh_latest_in_progress(cwd),
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)
