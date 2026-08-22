"""Wipe every computed/generated layer while keeping raw Riot downloads and peer
data -- deliberately dry-run by default, same convention as clear_mongodb.py.

Dropping the collections below and re-enqueuing the same player/group forces a
full recompute of reports and Career from scratch, WITHOUT re-hitting the Riot
API for anything already downloaded: `matches`/`timelines` (raw match/timeline
bodies, `RawMatchStore`) and `peer_games`/`peer_match_samples`/
`live_benchmark_cache` (PEERS' peer data) are deliberately left untouched, since
those are the expensive, rate-limited (100 requests/2min) part of a re-run --
the whole point of this script. `jobs`/`players`/`counters` are also left
untouched, so watch settings and tracked-account identity survive.

Dropped, in order:
- `report_builds`, `report_bodies`, `report_view_slices`, `report_game_review`
  (`ReportStore`) -- the reports themselves.
- `derived` (`DerivedStore`) -- the per-game/per-slice computed-artifact cache.
- `career_goals`, `career_used_tracks`, `career_flags` (`CareerStore`).

**Career state is NOT a cache** -- unlike the report/derived collections above,
Career state cannot be re-derived from match data alone (which track a player
chose, which goals they already cleared, frozen rung targets, pending congrats
banners). Dropping it resets every player's Career progress to a fresh ladder,
indistinguishable from starting over. This script drops it anyway, per an
explicit choice to accept that loss in exchange for a full clean-slate report
regenerate -- it is not a side effect anyone should be surprised by.

Usage:
    # Dry run (default): lists what would be dropped, drops nothing.
    uv run python deploy/reset_computed_data.py

    # Actually drop:
    uv run python deploy/reset_computed_data.py --yes

    # Point at a non-default Mongo URI (default: $MONGO_URI, else the local
    # docker-compose default):
    uv run python deploy/reset_computed_data.py --yes --mongo-uri mongodb://localhost:27017/league_stats
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))

from clear_mongodb import DEFAULT_MONGO_URI, _clear_mongo  # noqa: E402 -- see sys.path insert above

COMPUTED_COLLECTIONS = [
    "report_builds",
    "report_bodies",
    "report_view_slices",
    "report_game_review",
    "derived",
    "career_goals",
    "career_used_tracks",
    "career_flags",
]

# Explicitly never touched -- listed here so the intent is visible in a diff,
# not just in the module docstring. Never passed to _clear_mongo.
PRESERVED_COLLECTIONS = [
    "matches",
    "timelines",
    "peer_games",
    "peer_match_samples",
    "live_benchmark_cache",
    "jobs",
    "players",
    "counters",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("MONGO_URI", DEFAULT_MONGO_URI),
        help="Mongo connection string (default: $MONGO_URI, else the local docker-compose default).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete. Without this flag, only lists what would be deleted.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.yes:
        print("Dry run (pass --yes to actually delete anything):\n")
    print("Preserved (never touched by this script): " + ", ".join(PRESERVED_COLLECTIONS))
    print()
    exit_code = _clear_mongo(args.mongo_uri, COMPUTED_COLLECTIONS, apply=args.yes)
    if not args.yes:
        print("\nDry run only -- nothing was deleted. Re-run with --yes to actually delete the above.")
    else:
        print("\nDone. Re-enqueue the player/group to fully regenerate from cached matches/peer data.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
