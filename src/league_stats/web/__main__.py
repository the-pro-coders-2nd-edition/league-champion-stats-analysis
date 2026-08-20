"""Transitional re-export: this module moved to ``league_stats_api_ui.__main__`` (Phase 7, Task 6).

Nothing in `tests/` monkeypatches this module's globals by attribute or string
path (grepped explicitly), and nothing still runs it as `python -m
league_stats.web` (only `main.py`, updated in this same task to import from
the new location directly). A plain re-export is sufficient.
"""

from league_stats_api_ui.__main__ import *  # noqa: F401,F403

if __name__ == "__main__":
    main()
