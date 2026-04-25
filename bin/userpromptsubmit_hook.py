#!/usr/bin/env python3
"""UserPromptSubmit hook adapter (briefing + restore, D-2).

Reads the hook payload from stdin, runs the orchestrator, and emits
``additionalContext`` to stdout under the official hookSpecificOutput
schema. Always exits 0 — UserPromptSubmit failures must not block the
prompt.
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

from issueops.precompact import snapshot_current_issue  # noqa: E402
from issueops.user_prompt_submit import run_user_prompt_submit  # noqa: E402


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


def _gh_in_progress(cwd: Path, *, limit: int = 5) -> list[dict]:
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
            "number,title",
            "--limit",
            str(limit),
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(out.stdout) or []


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
        items = _gh_in_progress(cwd, limit=1)
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

    def current_issue_fn():
        return snapshot_current_issue(
            branch=branch,
            gh_fetch_fn=lambda n: _gh_fetch_issue(n, cwd=cwd),
            latest_in_progress_fn=lambda: _gh_latest_in_progress(cwd),
        )

    additional_context = run_user_prompt_submit(
        project_dir=project_dir,
        session_id=session_id,
        in_progress_fn=lambda: _gh_in_progress(cwd),
        current_issue_fn=current_issue_fn,
    )

    if additional_context:
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": additional_context,
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)
