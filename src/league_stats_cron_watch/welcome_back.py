"""Lightweight "welcome back" summary: win/loss, K/D/A, CS/min, damage share.

Computed directly from a single Match-V5 document -- no timeline, no
analysis pipeline. This is deliberately independent from
``league_stats.ingest.parser.MatchParser`` (which needs a timeline document
plus the full analysis context to build a ``MatchRecord``) and from the
career/recap engine (which needs the full analyzed pipeline output). Both of
those answer "how did this game fit into my history"; this answers "what
just happened", cheaply enough to compute on every new-game detection tick.

The Match-V5 field names below are not guessed -- they match the ones this
codebase already reads in ``league_stats.ingest.parser.MatchParser._combat``
and ``._economy`` (kills/deaths/assists, ``totalDamageDealtToChampions``,
``totalMinionsKilled``/``neutralMinionsKilled``, ``gameDuration``, ``win``,
``teamId``, ``puuid``).
"""

from __future__ import annotations

from typing import Any

from league_stats_common.utils import safe_div


def compute_welcome_back_summary(match: dict[str, Any], puuid: str) -> dict[str, Any]:
    """Build the lightweight summary for one tracked player in one match.

    Args:
        match: Raw Match-V5 match document (as returned by
            ``RiotApiClient.fetch_match``).
        puuid: The tracked player's PUUID.

    Returns:
        A JSON-serialisable dict with win/loss, K/D/A, CS/min, and damage
        share.

    Raises:
        StopIteration: If ``puuid`` is not a participant in ``match``.
    """
    info = match["info"]
    participants: list[dict[str, Any]] = info["participants"]
    me = next(p for p in participants if p["puuid"] == puuid)
    allies = [p for p in participants if p["teamId"] == me["teamId"]]

    duration_s = int(info.get("gameDuration", 0))
    if duration_s > 100_000:  # legacy matches report milliseconds, not seconds
        duration_s //= 1000
    minutes = max(1.0, duration_s / 60.0)
    kills = int(me.get("kills", 0))
    deaths = int(me.get("deaths", 0))
    assists = int(me.get("assists", 0))
    cs = int(me.get("totalMinionsKilled", 0)) + int(me.get("neutralMinionsKilled", 0))
    damage = int(me.get("totalDamageDealtToChampions", 0))
    team_damage = sum(int(p.get("totalDamageDealtToChampions", 0)) for p in allies)

    return {
        "match_id": str(match.get("metadata", {}).get("matchId", "")),
        "champion": str(me.get("championName", "")),
        "win": bool(me.get("win", False)),
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kda": (kills + assists) / max(1, deaths),
        "cs": cs,
        "cs_per_min": cs / minutes,
        "damage_to_champions": damage,
        "damage_share": safe_div(damage, team_damage),
    }
