#!/usr/bin/env python3
"""SessionEnd hook adapter.

Reads the hook payload from stdin, looks up the per-session state
file, and posts a fallback summary when the session-closer skill did
not run in this session. Always exits 0 — SessionEnd is best-effort.
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

from issueops.session_end import run_session_end  # noqa: E402


def _gh_post_comment(issue_number: int, body: str, *, cwd: Path) -> None:
    subprocess.run(
        ["gh", "issue", "comment", str(issue_number), "--body", body],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    session_id = payload.get("session_id") or "unknown"
    cwd = Path(payload.get("cwd") or os.getcwd())
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or cwd)

    run_session_end(
        project_dir=project_dir,
        session_id=session_id,
        post_comment_fn=lambda n, b: _gh_post_comment(n, b, cwd=cwd),
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)
