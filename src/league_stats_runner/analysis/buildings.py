"""Lane structure (turret/inhibitor) extraction and classification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

from league_stats_runner.analysis.key_moments import _is_split_push
from league_stats_runner.analysis.timeline import TimelineContext
from league_stats_common.core.models import BuildingRecord, MatchRecord, Position, Zone
from league_stats_common.utils import (
    BLUE_LANE_TOWER_POSITIONS,
    LANING_PHASE_END_MIN,
    RED_LANE_TOWER_POSITIONS,
    TOWER_RADIUS,
    classify_zone,
    distance,
    ms_to_min,
)

Lane = Literal["top", "mid", "bot"]
Tier = Literal["outer", "inner", "inhib", "nexus"]
BuildingType = Literal["tower", "inhibitor", "nexus"]


@dataclass(frozen=True)
class _TowerSite:
    position: Position
    lane: Lane
    tier: Tier
    blue_owned: bool


def _tower_sites() -> tuple[_TowerSite, ...]:
  sites: list[_TowerSite] = []
  blue_lanes: tuple[Lane, ...] = ("bot", "bot", "bot", "mid", "mid", "mid", "top", "top", "top")
  blue_tiers: tuple[Tier, ...] = ("outer", "inner", "inhib", "outer", "inner", "inhib", "outer", "inner", "inhib")
  for pos, lane, tier in zip(BLUE_LANE_TOWER_POSITIONS, blue_lanes, blue_tiers, strict=True):
      sites.append(_TowerSite(position=pos, lane=lane, tier=tier, blue_owned=True))
  red_lanes: tuple[Lane, ...] = ("top", "top", "top", "mid", "mid", "mid", "bot", "bot", "bot")
  red_tiers: tuple[Tier, ...] = ("outer", "inner", "inhib", "outer", "inner", "inhib", "outer", "inner", "inhib")
  for pos, lane, tier in zip(RED_LANE_TOWER_POSITIONS, red_lanes, red_tiers, strict=True):
      sites.append(_TowerSite(position=pos, lane=lane, tier=tier, blue_owned=False))
  return tuple(sites)


_TOWER_SITES = _tower_sites()


def classify_structure_position(
    pos: Position,
    *,
    building_type: str,
) -> tuple[BuildingType, Lane, Tier]:
    """Map a structure kill position to lane/tier metadata."""
    if "NEXUS" in building_type.upper():
        return "nexus", "mid", "nexus"
    if "INHIBITOR" in building_type.upper():
        nearest = min(_TOWER_SITES, key=lambda site: distance(pos, site.position))
        return "inhibitor", nearest.lane, "inhib"
    nearest = min(_TOWER_SITES, key=lambda site: distance(pos, site.position))
    return "tower", nearest.lane, nearest.tier


def structure_label(lane: Lane, tier: Tier, *, building_type: BuildingType = "tower") -> str:
    """Human-readable structure id, e.g. ``bot_t2``."""
    if building_type == "nexus":
        return "nexus"
    if building_type == "inhibitor" or tier == "inhib":
        return f"{lane}_inhib"
    tier_num = {"outer": "t1", "inner": "t2", "inhib": "inhib", "nexus": "nexus"}[tier]
    return f"{lane}_{tier_num}"


def structure_display_label(lane: Lane, tier: Tier, *, building_type: BuildingType = "tower") -> str:
    """UI label, e.g. ``Bot T2``."""
    if building_type == "nexus":
        return "Nexus"
    if building_type == "inhibitor" or tier == "inhib":
        return f"{lane.title()} inhib"
    names = {"outer": "T1", "inner": "T2", "inhib": "Inhib", "nexus": "Nexus"}
    return f"{lane.title()} {names[tier]}"


def nearest_own_tower_lane(pos: Position, blue_side: bool) -> Lane | None:
    """Lane of the closest owned lane tower to ``pos``."""
    sites = [s for s in _TOWER_SITES if s.blue_owned == blue_side]
    if not sites:
        return None
    nearest = min(sites, key=lambda site: distance(pos, site.position))
    if distance(pos, nearest.position) > TOWER_RADIUS * 1.5:
        return None
    return nearest.lane


def player_sidelane(pos: Position) -> Lane | None:
    """Top or bot lane the player is pressuring, from nearest sidelane turret or zone."""
    sidelane_sites = [site for site in _TOWER_SITES if site.lane in ("top", "bot")]
    if sidelane_sites:
        nearest = min(sidelane_sites, key=lambda site: distance(pos, site.position))
        if distance(pos, nearest.position) <= TOWER_RADIUS * 2.5:
            return nearest.lane
    zone = classify_zone(pos)
    if zone == Zone.TOP_LANE:
        return "top"
    if zone == Zone.BOT_LANE:
        return "bot"
    return None


def lane_tower_fallen_before(
    buildings: list[BuildingRecord],
    lane: Lane,
    timestamp_ms: int,
) -> bool:
    """Whether any tower on ``lane`` was destroyed before ``timestamp_ms`` (either team)."""
    return any(
        building.lane == lane
        and building.building_type == "tower"
        and building.timestamp_ms < timestamp_ms
        for building in buildings
    )


def split_push_allowed(
    minute: float,
    pos: Position,
    buildings: list[BuildingRecord],
    timestamp_ms: int,
) -> bool:
    """Split-push macro only counts after laning or once a lane tower has fallen.

    A tower on the player's current sidelane (allied or enemy) must be down to
    label early-game pressure as split pushing; otherwise wait until 14 minutes.
    """
    if minute >= LANING_PHASE_END_MIN:
        return True
    lane = player_sidelane(pos)
    if lane is None:
        return False
    return lane_tower_fallen_before(buildings, lane, timestamp_ms)


def extract_buildings(ctx: TimelineContext) -> list[BuildingRecord]:
    """Extract every lane structure kill with player context."""
    my_team_id = 100 if ctx.blue_side else 200
    records: list[BuildingRecord] = []
    for event in ctx.events_of("BUILDING_KILL"):
        building = str(event.get("buildingType", ""))
        if building not in {"TOWER_BUILDING"} and "INHIBITOR" not in building and "NEXUS" not in building:
            continue
        ts = int(event["timestamp"])
        destroyed_team = int(event.get("teamId", 0))
        beneficiary = 200 if destroyed_team == 100 else 100
        pos = Position(**event.get("position", {"x": 0, "y": 0}))
        btype, lane, tier = classify_structure_position(pos, building_type=building)
        player_pos = ctx.position_at_ms(ctx.participant_id, ts)
        player_present = (
            player_pos is not None and distance(player_pos, pos) <= TOWER_RADIUS * 2.5
        )
        killer_id = int(event.get("killerId", 0))
        records.append(
            BuildingRecord(
                minute=ms_to_min(ts),
                timestamp_ms=ts,
                building_type=btype,
                lane=lane,
                tier=tier,
                taken_by_team=beneficiary == my_team_id,
                destroyed_team_id=destroyed_team,
                position=pos,
                player_present=player_present,
                player_killer=killer_id == ctx.participant_id,
                split_push_context=_is_split_push(ctx, ts, pos),
                structure_id=structure_label(lane, tier, building_type=btype),
            )
        )
    records.sort(key=lambda r: r.minute)
    return records


def buildings_dataframe(records: list[MatchRecord]) -> pd.DataFrame:
    """Flatten structure kills into a dataframe."""
    rows: list[dict[str, Any]] = []
    for record in records:
        for building in record.buildings:
            rows.append(
                {
                    "match_id": record.match_id,
                    "win": int(record.win),
                    "minute": round(building.minute, 2),
                    "building_type": building.building_type,
                    "lane": building.lane,
                    "tier": building.tier,
                    "taken_by_team": building.taken_by_team,
                    "player_present": building.player_present,
                    "player_killer": building.player_killer,
                    "split_push_context": building.split_push_context,
                    "structure_id": building.structure_id,
                }
            )
    return pd.DataFrame(rows)


def structure_pressure_buckets(
    record: MatchRecord,
    *,
    bucket_min: int = 5,
) -> list[dict[str, float | int]]:
    """Per-bucket structure pressure for the Game Review overview chart."""
    if record.duration_min <= 0:
        return []
    buckets = int(record.duration_min // bucket_min) + 1
    kills = record.buildings
    rows: list[dict[str, float | int]] = []
    for index in range(buckets):
        start = index * bucket_min
        end = start + bucket_min
        tower_kills = sum(
            1
            for b in kills
            if b.taken_by_team and start <= b.minute < end and b.building_type == "tower"
        )
        rows.append(
            {
                "minute": float(start + bucket_min / 2),
                "plates": 0,
                "towers": tower_kills,
                "tower_damage": 0.0,
            }
        )
    if record.combat.damage_to_turrets and buckets:
        per_bucket = record.combat.damage_to_turrets / max(1, buckets)
        for row in rows:
            row["tower_damage"] = round(per_bucket, 0)
    return rows
