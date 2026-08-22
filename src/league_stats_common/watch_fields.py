"""Formatting for ``JobStore`` player rows' watch fields, shared by API-UI.

Extracted out of ``web/watch.py`` (Phase 7, Task 2) because it formats
``JobStore`` row dicts and travels with ``JobStore`` rather than with
``WatchPoller``/``MatchIdSource``, which stay CRON-watch-specific.
"""

from __future__ import annotations

from typing import Any


def watch_public_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Watch state for API responses."""
    return {
        "watch_enabled": bool(row.get("watch_enabled")),
        "watch_interval_s": int(row.get("watch_interval_s") or 0),
        "last_watch_at": row.get("last_watch_at"),
        "last_watch_error": str(row.get("last_watch_error") or ""),
    }
