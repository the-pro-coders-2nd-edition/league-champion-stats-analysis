"""Recreate groups from a ``deploy/snapshot_groups.py`` snapshot -- deliberately
dry-run by default, matching ``deploy/clear_mongodb.py``'s convention.

Run this *after* a Mongo wipe to bring every previously-registered group back,
via real HTTP requests to a running ``api-ui`` (``POST /api/analyses``, and
``POST /api/players/{slug}/watch`` for groups that had watching enabled) --
the exact same enqueue/upsert code path production traffic uses, not a raw DB
write. Groups are recreated **in snapshot order**, one at a time, with a short
delay between each: several groups re-verifying accounts against the Riot API
back-to-back is a rate-limit concern (not a correctness one), so this
deliberately does not parallelize.

Usage:
    # Dry run (default): lists what would be sent, sends nothing.
    uv run python deploy/restore_groups.py

    # Actually send the requests:
    uv run python deploy/restore_groups.py --yes

    # Point at a non-default snapshot file or api-ui base URL:
    uv run python deploy/restore_groups.py --yes \
        --snapshot /tmp/league_stats_groups_snapshot.json \
        --api-base-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

DEFAULT_SNAPSHOT_PATH = "/tmp/league_stats_groups_snapshot.json"
DEFAULT_API_BASE_URL = "http://localhost:8000"

# Gap between groups: this is a Riot API rate-limit courtesy (avoid every
# restored group's account-verification precheck landing on Riot at once),
# not a correctness requirement -- restoring groups is not otherwise racy.
DELAY_BETWEEN_GROUPS_S = 5.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--snapshot",
        default=DEFAULT_SNAPSHOT_PATH,
        help=f"Path to the JSON snapshot written by snapshot_groups.py (default: {DEFAULT_SNAPSHOT_PATH}).",
    )
    parser.add_argument(
        "--api-base-url",
        default=DEFAULT_API_BASE_URL,
        help=f"Base URL of a running api-ui instance (default: {DEFAULT_API_BASE_URL}).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DELAY_BETWEEN_GROUPS_S,
        help=f"Seconds to wait between groups (default: {DELAY_BETWEEN_GROUPS_S}).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually send the requests. Without this flag, only lists what would be sent.",
    )
    return parser.parse_args()


def _load_snapshot(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        print(f"Snapshot file not found: {path}", file=sys.stderr)
        raise SystemExit(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        print(f"Snapshot file {path} does not contain a JSON list.", file=sys.stderr)
        raise SystemExit(1)
    return data


def _players_summary(players: list[dict[str, str]]) -> str:
    return ", ".join(f"{p['riot_id']}#{p['tagline']}" for p in players)


def _analyses_body(group: dict[str, Any]) -> dict[str, Any]:
    """Build the ``AnalysisRequest`` body, preserving player order.

    ``players`` (a list of ``Name#Tag`` strings) is the only field carrying
    order -- leaving ``riot_id``/``tagline`` empty avoids
    ``_resolve_players`` appending a duplicate/reordered entry.
    """
    players: list[dict[str, str]] = group.get("players") or []
    return {
        "players": [f"{p['riot_id']}#{p['tagline']}" for p in players],
        "region": group.get("region") or "euw1",
    }


def _restore_group(
    session: requests.Session, api_base_url: str, group: dict[str, Any]
) -> tuple[bool, str]:
    """POST the group's analysis request (and watch setting, if any).

    Returns:
        ``(ok, message)``.
    """
    slug = group.get("slug", "?")
    body = _analyses_body(group)
    try:
        response = session.post(f"{api_base_url}/api/analyses", json=body, timeout=60)
    except requests.RequestException as exc:
        return False, f"request to /api/analyses failed: {exc}"

    if response.status_code >= 400:
        return False, f"/api/analyses returned {response.status_code}: {response.text[:300]}"

    payload = response.json()
    created = payload.get("created")
    result_slug = payload.get("player_slug", slug)
    detail = "created" if created else "already existed (active job found)"

    if group.get("watch_enabled"):
        watch_body: dict[str, Any] = {}
        interval_s = group.get("watch_interval_s")
        if interval_s is not None:
            watch_body["interval_s"] = interval_s
        try:
            watch_response = session.post(
                f"{api_base_url}/api/players/{result_slug}/watch", json=watch_body, timeout=30
            )
        except requests.RequestException as exc:
            return False, f"{detail}, but watch request failed: {exc}"
        if watch_response.status_code >= 400:
            return False, (
                f"{detail}, but /api/players/{result_slug}/watch returned "
                f"{watch_response.status_code}: {watch_response.text[:300]}"
            )
        detail += ", watch enabled"

    return True, detail


def main() -> int:
    args = parse_args()
    snapshot_path = Path(args.snapshot)
    groups = _load_snapshot(snapshot_path)

    print(f"Snapshot: {snapshot_path}  ({len(groups)} group(s))")
    print(f"Target api-ui: {args.api_base_url}\n")

    if not args.yes:
        print("Dry run (pass --yes to actually send the requests):\n")
        for group in groups:
            watch = (
                f"watch every {group.get('watch_interval_s')}s"
                if group.get("watch_enabled")
                else "no watch"
            )
            print(
                f"  would POST /api/analyses for {group.get('slug', '?'):<40} "
                f"{_players_summary(group.get('players') or [])}  ({watch})"
            )
        print("\nDry run only -- nothing was sent. Re-run with --yes to actually restore groups.")
        return 0

    session = requests.Session()
    succeeded = 0
    failed = 0
    for index, group in enumerate(groups, start=1):
        slug = group.get("slug", "?")
        print(f"[{index}/{len(groups)}] Restoring {slug} ({_players_summary(group.get('players') or [])})...")
        ok, message = _restore_group(session, args.api_base_url, group)
        if ok:
            succeeded += 1
            print(f"  ok: {message}")
        else:
            failed += 1
            print(f"  FAILED: {message}", file=sys.stderr)
        if index < len(groups):
            time.sleep(args.delay)

    print(f"\nDone: {succeeded} restored, {failed} failed, out of {len(groups)} group(s).")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
