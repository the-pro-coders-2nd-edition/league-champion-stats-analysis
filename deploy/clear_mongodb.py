"""Wipe (or inspect) the app's MongoDB database and generated output files --
deliberately dry-run by default.

Every service in this app shares one Mongo database (`MONGO_URI`/`RUNNER_MONGO_URI`,
default db name "league_stats", resolved the same way `league_stats_common.infra.mongo`
does) -- JobStore, RawMatchStore, PeerSampleStore, peer_match_samples, the Career
store, watch/cron-watch state, and welcome-back cache all live there as separate
collections. There is no per-collection "reset" command anywhere in this codebase,
and dropping the wrong database by fat-fingering a URI is a one-way trip -- so this
script defaults to listing what it *would* delete and requires an explicit --yes to
actually do it, on top of printing the resolved database name/host so a pasted
production URI is visually obvious before you confirm.

Also clears `output_dir` (`AppConfig`/`WebConfig`'s per-job report tree -- generated
report.json/meta.json/manifest.json blobs and anything else RUNNER writes per job).
Deliberately does NOT touch `assets_dir` (the Data Dragon icon cache): since the
"give DDragon assets their own volume" change, `assets_dir` is a fully separate
directory tree from `output_dir`, not nested under it, so a plain `output_dir` wipe
already can't reach it -- this script asserts that separation explicitly rather than
relying on it silently, so a future config change that renests them fails loudly
here instead of quietly deleting the icon cache.

Usage:
    # Dry run (default): lists Mongo collections + doc counts and what's under
    # output_dir, deletes nothing.
    uv run python deploy/clear_mongodb.py

    # Actually drop every Mongo collection AND delete output_dir's contents:
    uv run python deploy/clear_mongodb.py --yes

    # Mongo only, skip the filesystem wipe:
    uv run python deploy/clear_mongodb.py --yes --skip-output

    # Filesystem only, skip Mongo:
    uv run python deploy/clear_mongodb.py --yes --skip-mongo

    # Scope Mongo to specific collections instead of the whole database:
    uv run python deploy/clear_mongodb.py --yes --collections jobs raw_matches

    # Point at non-default locations (default Mongo URI: $MONGO_URI, else the
    # local docker-compose default; default output dir: $OUTPUT_DIR, else "output"):
    uv run python deploy/clear_mongodb.py --mongo-uri mongodb://localhost:27017/league_stats --output-dir ./output
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pymongo

from league_stats_common.infra.mongo import db_name_from_uri

DEFAULT_MONGO_URI = "mongodb://localhost:27017/league_stats"
DEFAULT_OUTPUT_DIR = "output"
DEFAULT_ASSETS_DIR = "assets"


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
        help="Only drop these Mongo collections, by name, instead of every collection in the database.",
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("OUTPUT_DIR", DEFAULT_OUTPUT_DIR),
        help="Report/job output directory to clear (default: $OUTPUT_DIR, else 'output').",
    )
    parser.add_argument(
        "--assets-dir",
        default=os.environ.get("ASSETS_DIR", DEFAULT_ASSETS_DIR),
        help=(
            "Data Dragon icon cache directory -- NEVER deleted, only used to refuse "
            "to run if --output-dir resolves to it or a parent/child of it "
            "(default: $ASSETS_DIR, else 'assets')."
        ),
    )
    parser.add_argument("--skip-mongo", action="store_true", help="Don't touch Mongo, only clear output_dir.")
    parser.add_argument("--skip-output", action="store_true", help="Don't touch output_dir, only clear Mongo.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete. Without this flag, only lists what would be deleted.",
    )
    return parser.parse_args()


def _assert_output_dir_is_not_assets_dir(output_dir: Path, assets_dir: Path) -> None:
    """Refuse to proceed if output_dir and assets_dir overlap in any way.

    They're separate top-level directories by design (see module docstring) --
    this is a loud, explicit guard against that ever silently changing, not
    something expected to actually trigger.
    """
    output_resolved = output_dir.resolve()
    assets_resolved = assets_dir.resolve()
    if output_resolved == assets_resolved:
        raise SystemExit(
            f"Refusing to run: --output-dir ({output_resolved}) is the same as "
            f"--assets-dir ({assets_resolved}). This would delete the Data Dragon "
            "icon cache, which this script must never touch."
        )
    if assets_resolved in output_resolved.parents or output_resolved in assets_resolved.parents:
        raise SystemExit(
            f"Refusing to run: --output-dir ({output_resolved}) and --assets-dir "
            f"({assets_resolved}) are nested inside one another. This script only "
            "supports them as fully separate trees."
        )


def _clear_mongo(mongo_uri: str, collections: list[str] | None, apply: bool) -> int:
    db_name = db_name_from_uri(mongo_uri)
    client: pymongo.MongoClient = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except pymongo.errors.PyMongoError as exc:
        print(f"Could not reach {mongo_uri!r}: {exc}", file=sys.stderr)
        return 1

    db = client[db_name]
    existing = set(db.list_collection_names())
    target_names = collections if collections else sorted(existing)

    print(f"Mongo database: {db_name!r}  (URI: {mongo_uri!r})")
    if not target_names:
        print("  (no collections)")
    else:
        print(f"  {'Collection':<38} {'Documents':>10}")
        for name in target_names:
            if name not in existing:
                print(f"  {name:<38} {'(missing)':>10}")
                continue
            count = db[name].estimated_document_count()
            print(f"  {name:<38} {count:>10}")

    if not apply:
        return 0

    print(f"Dropping {len(target_names)} Mongo collection(s)...")
    for name in target_names:
        db.drop_collection(name)
        print(f"  dropped {name}")
    return 0


def _clear_output_dir(output_dir: Path, apply: bool) -> int:
    print(f"Output directory: {output_dir.resolve()}")
    if not output_dir.is_dir():
        print("  (does not exist)")
        return 0

    entries = sorted(output_dir.iterdir())
    if not entries:
        print("  (empty)")
        return 0

    for entry in entries:
        kind = "dir " if entry.is_dir() else "file"
        print(f"  [{kind}] {entry.relative_to(output_dir)}")

    if not apply:
        return 0

    print(f"Deleting {len(entries)} entr(y/ies) under {output_dir.resolve()}...")
    for entry in entries:
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
        print(f"  deleted {entry.relative_to(output_dir)}")
    return 0


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    assets_dir = Path(args.assets_dir)

    if not args.skip_output:
        _assert_output_dir_is_not_assets_dir(output_dir, assets_dir)

    if not args.yes:
        print("Dry run (pass --yes to actually delete anything):\n")

    exit_code = 0
    if not args.skip_mongo:
        exit_code = _clear_mongo(args.mongo_uri, args.collections, apply=args.yes) or exit_code
        print()
    if not args.skip_output:
        exit_code = _clear_output_dir(output_dir, apply=args.yes) or exit_code

    if not args.yes:
        print("\nDry run only -- nothing was deleted. Re-run with --yes to actually delete the above.")
    else:
        print("\nDone.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
