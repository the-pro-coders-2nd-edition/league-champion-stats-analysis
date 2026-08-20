"""Transitional re-export shim -- real code now lives in `league_stats_peers.__main__`.

`docker-compose.yml` still runs `python -m league_stats.peers` until Task 8
of Phase 7 renames it to `python -m league_stats_peers`, so this module must
stay runnable as `python -m league_stats.peers`, not just importable.
"""

from league_stats_peers.__main__ import main, serve

if __name__ == "__main__":
    main()
