"""Reset every Career ladder in the local cache.

Career progress lives in ``{cache_dir}/career.sqlite``, keyed per player +
champion + role. Reports embed career JSON at generation time, so after clearing
the store you still need to regenerate each player (Regenerate on the player
page, or re-queue a job) to refresh on-disk HTML/JSON.

Usage::

    uv run python scripts/reset_career.py
    uv run python scripts/reset_career.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from league_stats_common.core.config import load_paths_config
from league_stats_common.infra.career_store import CareerStore
from league_stats_runner.presentation.report import discover_reports


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clear every Career ladder from the local SQLite store.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.toml (default: ./config.toml when present).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Override the cache directory from config (default: .cache).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be cleared without writing.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    overrides: dict[str, Path] = {}
    if args.cache_dir is not None:
        overrides["cache_dir"] = args.cache_dir
    config = load_paths_config(config_file=args.config, **overrides)
    db_path = config.career_db_path
    reports = discover_reports(config.output_dir)

    if not db_path.is_file():
        print(f"No career store at {db_path} — nothing to reset.")
        if reports:
            print(
                f"Found {len(reports)} on-disk report(s); regenerate them after "
                "career logic changes to refresh embedded career JSON."
            )
        return 0

    with CareerStore(db_path) as store:
        counts = store.row_counts() if args.dry_run else store.clear_all()

    prefix = "Would clear" if args.dry_run else "Cleared"
    print(f"{prefix} career store at {db_path}")
    print(f"  goals: {counts['career_goals']}")
    print(f"  retired tracks: {counts['career_used_tracks']}")
    print(f"  flags: {counts['career_flags']}")

    if sum(counts.values()) == 0:
        print("\nStore was already empty.")
    elif reports:
        print(
            f"\n{len(reports)} report(s) on disk still embed the old career JSON. "
            "Regenerate each player from the web UI to rebuild ladders."
        )
    else:
        print("\nRegenerate reports after career logic changes to refresh on-disk output.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
