"""Wipe (or inspect) the app's MongoDB database -- deliberately dry-run by default.

Every service in this app shares one Mongo database (`MONGO_URI`/`RUNNER_MONGO_URI`,
default db name "league_stats", resolved the same way `league_stats_common.infra.mongo`
does) -- JobStore, RawMatchStore, PeerSampleStore, peer_match_samples, the Career
store, watch/cron-watch state, and welcome-back cache all live there as separate
collections. There is no per-collection "reset" command anywhere in this codebase,
and dropping the wrong database by fat-fingering a URI is a one-way trip -- so this
script defaults to listing what it *would* delete and requires an explicit --yes to
actually do it, on top of printing the resolved database name/host so a pasted
production URI is visually obvious before you confirm.

Usage:
    # Dry run (default): lists every collection and its document count, deletes nothing.
    uv run python deploy/clear_mongodb.py

    # Actually drop every collection in the database:
    uv run python deploy/clear_mongodb.py --yes

    # Scope to specific collections instead of the whole database:
    uv run python deploy/clear_mongodb.py --yes --collections jobs raw_matches

    # Point at a non-default URI (defaults to $MONGO_URI, then the local
    # docker-compose default):
    uv run python deploy/clear_mongodb.py --mongo-uri mongodb://localhost:27017/league_stats
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pymongo

from league_stats_common.infra.mongo import db_name_from_uri

DEFAULT_MONGO_URI = "mongodb://localhost:27017/league_stats"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("MONGO_URI", DEFAULT_MONGO_URI),
        help="Mongo connection string (default: $MONGO_URI, else the local docker-compose default).",
    )
    parser.add_argument(
        "--collections",
        nargs="+",
        default=None,
        metavar="NAME",
        help="Only drop these collections, by name, instead of every collection in the database.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually drop the collection(s). Without this flag, only lists what would be dropped.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_name = db_name_from_uri(args.mongo_uri)

    client: pymongo.MongoClient = pymongo.MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except pymongo.errors.PyMongoError as exc:
        print(f"Could not reach {args.mongo_uri!r}: {exc}", file=sys.stderr)
        return 1

    db = client[db_name]
    target_names = args.collections if args.collections else sorted(db.list_collection_names())

    if not target_names:
        print(f"Database {db_name!r} at {args.mongo_uri!r} has no collections. Nothing to do.")
        return 0

    print(f"Target database: {db_name!r}  (URI: {args.mongo_uri!r})")
    print(f"{'Collection':<40} {'Documents':>10}")
    for name in target_names:
        if name not in db.list_collection_names():
            print(f"{name:<40} {'(missing)':>10}")
            continue
        count = db[name].estimated_document_count()
        print(f"{name:<40} {count:>10}")

    if not args.yes:
        print(
            "\nDry run only -- nothing was deleted. Re-run with --yes to actually "
            "drop the collection(s) listed above."
        )
        return 0

    print(f"\nDropping {len(target_names)} collection(s) from {db_name!r}...")
    for name in target_names:
        db.drop_collection(name)
        print(f"  dropped {name}")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
