"""Snapshot every registered group (the ``players`` Mongo collection) to a JSON
file -- deliberately dry-run by default, matching ``deploy/clear_mongodb.py``'s
convention.

Run this *before* wiping Mongo (e.g. before ``docker-compose down -v`` or
``deploy/clear_mongodb.py --yes``) so every user-registered group (a tracked
riot-id/tagline account, or several pooled together) can be recreated
afterward with ``deploy/restore_groups.py`` via a real ``POST /api/analyses``
request -- the same enqueue/upsert code path production traffic uses, not a
raw DB write.

Captures, per group, exactly what ``restore_groups.py`` needs to recreate it:
``slug``, ``riot_id``, ``tagline``, ``region``, the full ordered ``players``
list (as stored -- order matters, see ``JobStore.upsert_player``'s
``encode_players``/``decode_players``), ``watch_enabled``, and
``watch_interval_s``.

Usage:
    # Dry run (default): lists every group found, writes nothing.
    uv run python deploy/snapshot_groups.py

    # Actually write the snapshot file:
    uv run python deploy/snapshot_groups.py --yes

    # Point at a non-default Mongo URI or output path:
    uv run python deploy/snapshot_groups.py --yes \
        --mongo-uri mongodb://localhost:27017/league_stats \
        --output /tmp/league_stats_groups_snapshot.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pymongo

from league_stats_common.infra.mongo import db_name_from_uri

DEFAULT_MONGO_URI = "mongodb://localhost:27017/league_stats"
DEFAULT_SNAPSHOT_PATH = "/tmp/league_stats_groups_snapshot.json"


def _resolve_mongo_uri(cli_value: str | None) -> str:
    """Same resolution order as ``JobStore.open_jobs_store``'s ``_resolve_mongo_uri``."""
    return (
        cli_value
        or os.environ.get("RUNNER_MONGO_URI")
        or os.environ.get("MONGO_URI")
        or DEFAULT_MONGO_URI
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--mongo-uri",
        default=None,
        help="Mongo connection string (default: $RUNNER_MONGO_URI, else $MONGO_URI, else the local docker-compose default).",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_SNAPSHOT_PATH,
        help=f"Path to write the JSON snapshot to (default: {DEFAULT_SNAPSHOT_PATH}).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually write the snapshot file. Without this flag, only lists what would be captured.",
    )
    return parser.parse_args()


def _decode_players(raw: str | None, *, riot_id: str, tagline: str) -> list[dict[str, str]]:
    """Local re-implementation of ``JobStore.decode_players`` to avoid importing app wiring."""
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list) and parsed:
            players: list[dict[str, str]] = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("riot_id", "")).strip()
                tag = str(item.get("tagline", "")).strip()
                if not name or not tag:
                    continue
                players.append({"riot_id": name, "tagline": tag})
            if players:
                return players
    if riot_id and tagline:
        return [{"riot_id": riot_id, "tagline": tagline}]
    return []


def _collect_groups(mongo_uri: str) -> list[dict[str, Any]]:
    db_name = db_name_from_uri(mongo_uri)
    client: pymongo.MongoClient = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except pymongo.errors.PyMongoError as exc:
        print(f"Could not reach {mongo_uri!r}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    db = client[db_name]
    print(f"Mongo database: {db_name!r}  (URI: {mongo_uri!r})")

    groups: list[dict[str, Any]] = []
    for doc in db["players"].find().sort("_id", 1):
        slug = str(doc["_id"])
        riot_id = str(doc.get("riot_id", ""))
        tagline = str(doc.get("tagline", ""))
        players = _decode_players(doc.get("players_json"), riot_id=riot_id, tagline=tagline)
        groups.append(
            {
                "slug": slug,
                "riot_id": riot_id,
                "tagline": tagline,
                "region": str(doc.get("region", "")),
                "players": players,
                "watch_enabled": bool(doc.get("watch_enabled", 0)),
                "watch_interval_s": doc.get("watch_interval_s"),
            }
        )
    return groups


def main() -> int:
    args = parse_args()
    mongo_uri = _resolve_mongo_uri(args.mongo_uri)

    groups = _collect_groups(mongo_uri)

    if not args.yes:
        print("Dry run (pass --yes to actually write the snapshot file):\n")

    print(f"Found {len(groups)} group(s):")
    for group in groups:
        watch = (
            f"watch every {group['watch_interval_s']}s"
            if group["watch_enabled"]
            else "not watched"
        )
        print(f"  {group['slug']:<40} {players_summary(group['players'])}  ({watch})")

    if not args.yes:
        print("\nDry run only -- nothing was written. Re-run with --yes to write the snapshot.")
        return 0

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(groups, indent=2), encoding="utf-8")
    print(f"\nWrote {len(groups)} group(s) to {output_path}")
    return 0


def players_summary(players: list[dict[str, str]]) -> str:
    """Comma-separated ``Name#Tag`` label, mirroring ``players_label``."""
    return ", ".join(f"{p['riot_id']}#{p['tagline']}" for p in players)


if __name__ == "__main__":
    raise SystemExit(main())
