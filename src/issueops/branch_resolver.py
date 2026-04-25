"""Resolve the current GitHub issue from a branch name.

The default convention (`<type>/<issue-number>-<slug>`) is documented in
CONTRIBUTING.md. Users can override the regex via plugin settings; this
module is the canonical implementation that hooks and skills depend on.
"""

from __future__ import annotations

import re
from collections.abc import Callable

DEFAULT_BRANCH_PATTERN = r"(?:feat|fix|chore|refactor)/(\d+)-"


def extract_issue_number(branch: str, pattern: str) -> int | None:
    """Return the issue number captured by ``pattern`` from ``branch``.

    ``pattern`` must contain exactly one capture group whose contents are
    all digits. Returns ``None`` when ``branch`` does not match.
    """
    if not branch:
        return None
    m = re.search(pattern, branch)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (IndexError, ValueError):
        return None


def resolve_current_issue(
    branch: str,
    *,
    pattern: str = DEFAULT_BRANCH_PATTERN,
    fallback: str = "latest-in-progress",
    fallback_fn: Callable[[], int | None] | None = None,
) -> int | None:
    """Resolve the current issue number, with a fallback strategy.

    Tries ``extract_issue_number`` first. If that returns ``None``, the
    fallback strategy decides what to do:

    - ``"none"`` returns ``None`` directly, ignoring ``fallback_fn``.
    - ``"latest-in-progress"`` calls ``fallback_fn`` and returns its
      result. ``fallback_fn`` is required (``ValueError`` otherwise) to
      keep the I/O concern out of this pure module: the caller injects
      whatever returns the latest in-progress issue (e.g., a wrapper
      around ``gh issue list``).

    Any other ``fallback`` value raises ``ValueError``.
    """
    n = extract_issue_number(branch, pattern)
    if n is not None:
        return n
    if fallback == "none":
        return None
    if fallback == "latest-in-progress":
        if fallback_fn is None:
            raise ValueError(
                "fallback='latest-in-progress' requires fallback_fn"
            )
        return fallback_fn()
    raise ValueError(f"unknown fallback strategy: {fallback!r}")
