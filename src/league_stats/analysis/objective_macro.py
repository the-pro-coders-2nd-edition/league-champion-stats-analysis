"""Macro role detection at epic objective spawns."""

from __future__ import annotations

import math
from typing import Any, Literal

import pandas as pd

from league_stats.analysis.buildings import nearest_own_tower_lane, split_push_allowed
from league_stats.analysis.timeline import TimelineContext, participant_states_at_ms
from league_stats.analysis.trades import (
    DEFEND_TOWER_WINDOW_AFTER_MS,
    DEFEND_TOWER_WINDOW_BEFORE_MS,
    OBJECTIVE_VALUES,
    evaluate_objective_trade,
)
from league_stats.core.models import BuildingRecord, ObjectiveKind, ObjectiveRecord, Position, Zone
from league_stats.utils import (
    BARON_PIT,
    DRAGON_PIT,
    MAP_SIZE,
    TOWER_RADIUS,
    classify_zone,
    distance,
    is_side_lane,
    near_enemy_lane_tower,
    near_own_lane_tower,
)

MacroRole = Literal[
    "present",
    "split_pushing",
    "defending_split",
    "dead",
    "roaming",
    "base",
    "absent",
]

SIEGE_RADIUS = 3_000.0
DEFEND_ENEMY_RADIUS = 4_500.0
SIEGE_WINDOW_MS = 90_000
TOWER_KILL_WINDOW_MS = 45_000
PIT_MANPOWER_RADIUS = 4_500.0
TP_RANGE = 8_000.0

_TELEPORT_SPELLS = frozenset({"Teleport", "Teleport to Two"})


def _pit_for_kind(kind: ObjectiveKind) -> tuple[float, float]:
    if kind in (ObjectiveKind.DRAGON, ObjectiveKind.ELDER):
        return DRAGON_PIT.x, DRAGON_PIT.y
    return BARON_PIT.x, BARON_PIT.y


def _opposite_map_halves(pit: tuple[float, float], pos: Position) -> bool:
    pit_sum = pit[0] + pit[1]
    pos_sum = pos.x + pos.y
    return (pit_sum < MAP_SIZE * 0.9 and pos_sum > MAP_SIZE * 1.1) or (
        pit_sum > MAP_SIZE * 1.1 and pos_sum < MAP_SIZE * 0.9
    )


def _ally_building_kill_near(
    buildings: list[BuildingRecord],
    ts: int,
    pos: Position,
    *,
    my_team_id: int,
) -> bool:
    for building in buildings:
        if not building.taken_by_team:
            continue
        if abs(building.timestamp_ms - ts) > SIEGE_WINDOW_MS:
            continue
        if distance(pos, building.position) <= SIEGE_RADIUS * 2:
            return True
    return False


def _enemy_tower_dies_near(
    buildings: list[BuildingRecord],
    ts: int,
    pos: Position,
) -> bool:
    for building in buildings:
        if building.taken_by_team:
            continue
        if abs(building.timestamp_ms - ts) > TOWER_KILL_WINDOW_MS:
            continue
        if distance(pos, building.position) <= SIEGE_RADIUS:
            return True
    return False


def _player_killed_building_near(
    buildings: list[BuildingRecord],
    ts: int,
    pos: Position,
) -> bool:
    for building in buildings:
        if not building.player_killer:
            continue
        if abs(building.timestamp_ms - ts) <= TOWER_KILL_WINDOW_MS:
            if distance(pos, building.position) <= SIEGE_RADIUS * 1.5:
                return True
    return False


def _offensive_sidelane_pressure(
    ctx: TimelineContext,
    *,
    ts: int,
    minute: float,
    pos: Position,
    buildings: list[BuildingRecord],
) -> bool:
    zone = classify_zone(pos)
    if not (is_side_lane(zone) or near_enemy_lane_tower(pos, ctx.blue_side)):
        return False
    if not split_push_allowed(minute, pos, buildings, ts):
        return False
    return (
        _ally_building_kill_near(buildings, ts, pos, my_team_id=100 if ctx.blue_side else 200)
        or _enemy_tower_dies_near(buildings, ts, pos)
        or _player_killed_building_near(buildings, ts, pos)
    )


def _count_enemies_near(
    ctx: TimelineContext,
    ts: int,
    anchor: Position,
    *,
    radius: float,
) -> int:
    states = participant_states_at_ms(ctx, ts)
    count = 0
    for pid, (pos, dead) in states.items():
        if dead or pid in ctx.team_ids:
            continue
        if distance(anchor, pos) <= radius:
            count += 1
    return count


def _defending_split_context(
    ctx: TimelineContext,
    *,
    ts: int,
    pos: Position,
    buildings: list[BuildingRecord],
    my_team_id: int,
) -> tuple[bool, str | None, int]:
    if not near_own_lane_tower(pos, ctx.blue_side):
        return False, None, 0
    lane = nearest_own_tower_lane(pos, ctx.blue_side)
    enemy_near_player = _count_enemies_near(ctx, ts, pos, radius=DEFEND_ENEMY_RADIUS)
    if enemy_near_player == 0:
        return False, lane, 0
    threatened = any(
        building.destroyed_team_id == my_team_id
        and ts - DEFEND_TOWER_WINDOW_BEFORE_MS <= building.timestamp_ms <= ts + DEFEND_TOWER_WINDOW_AFTER_MS
        and (lane is None or building.lane == lane)
        for building in buildings
    )
    if enemy_near_player > 0 or threatened:
        return True, lane, enemy_near_player
    return False, lane, enemy_near_player


def _manpower_at_pit(ctx: TimelineContext, ts: int, pit: tuple[float, float]) -> str | None:
    manpower, _, _ = _pit_participants(ctx, ts, pit)
    return manpower


def _pit_participants(
    ctx: TimelineContext, ts: int, pit: tuple[float, float]
) -> tuple[str | None, list[str], list[str]]:
    states = participant_states_at_ms(ctx, ts)
    pit_pos = Position(x=pit[0], y=pit[1])
    ally_names: list[str] = []
    enemy_names: list[str] = []
    for pid, (pos, dead) in states.items():
        if dead or distance(pos, pit_pos) > PIT_MANPOWER_RADIUS:
            continue
        name = ctx.id_to_champion.get(pid)
        if not name:
            continue
        if pid in ctx.team_ids:
            ally_names.append(name)
        else:
            enemy_names.append(name)
    if not ally_names and not enemy_names:
        return None, [], []
    return f"{len(ally_names)}v{len(enemy_names)}", ally_names, enemy_names


def _has_teleport(summoners: list[str]) -> bool:
    return any(spell in _TELEPORT_SPELLS for spell in summoners)


def _tp_available(ctx: TimelineContext, ts: int, pit: tuple[float, float], pos: Position) -> bool | None:
    return None


def detect_macro_role(
    ctx: TimelineContext,
    objective: ObjectiveRecord,
    buildings: list[BuildingRecord],
    *,
    summoners: list[str],
) -> dict[str, Any]:
    """Classify player macro at one objective timestamp."""
    ts = int(round(objective.minute * 60_000))
    pit = _pit_for_kind(objective.kind)
    my_team_id = 100 if ctx.blue_side else 200
    pos = ctx.position_at_ms(ctx.participant_id, ts)

    if objective.present:
        role: MacroRole = "present"
        justified = True
        absence_reason = None
        sidelane_pressure = False
        defending_lane = None
        nearby_enemies = None
    elif objective.dead_before:
        role = "dead"
        justified = False
        absence_reason = None
        sidelane_pressure = False
        defending_lane = None
        nearby_enemies = None
    elif pos is None:
        role = "absent"
        justified = False
        absence_reason = None
        sidelane_pressure = False
        defending_lane = None
        nearby_enemies = None
    else:
        zone = classify_zone(pos)
        defending, defending_lane, nearby_enemies = _defending_split_context(
            ctx, ts=ts, pos=pos, buildings=buildings, my_team_id=my_team_id
        )
        offensive = _offensive_sidelane_pressure(
            ctx, ts=ts, minute=objective.minute, pos=pos, buildings=buildings
        )
        if defending:
            role = "defending_split"
            justified = True
            absence_reason = "defending_split"
            sidelane_pressure = False
        elif offensive:
            role = "split_pushing"
            justified = True
            absence_reason = "offensive_pressure"
            sidelane_pressure = True
        elif zone == Zone.BASE:
            role = "base"
            justified = False
            absence_reason = None
            sidelane_pressure = False
        elif zone in (Zone.RIVER, Zone.JUNGLE, Zone.MID_LANE):
            role = "roaming"
            justified = False
            absence_reason = None
            sidelane_pressure = False
        else:
            role = "absent"
            justified = False
            absence_reason = None
            sidelane_pressure = False

    cross_map = pos is not None and _opposite_map_halves(pit, pos)
    manpower, pit_allies, pit_enemies = _pit_participants(ctx, ts, pit)
    tp_flag: bool | None = None
    if pos is not None and not objective.present and _has_teleport(summoners):
        pit_pos = Position(x=pit[0], y=pit[1])
        tp_flag = distance(pos, pit_pos) <= TP_RANGE

    trade = evaluate_objective_trade(
        ctx,
        objective,
        buildings,
        macro_role=role,
        defending_lane=defending_lane,
    )

    return {
        "macro_role": role,
        "justified_absence": justified,
        "absence_reason": absence_reason,
        "sidelane_pressure": sidelane_pressure,
        "defending_lane": defending_lane,
        "nearby_enemy_count": nearby_enemies,
        "manpower_at_pit": manpower,
        "pit_ally_champions": pit_allies,
        "pit_enemy_champions": pit_enemies,
        "tp_available": tp_flag,
        "cross_map": cross_map,
        **trade,
    }


def enrich_objectives(
    ctx: TimelineContext,
    objectives: list[ObjectiveRecord],
    buildings: list[BuildingRecord],
    *,
    summoners: list[str],
) -> list[ObjectiveRecord]:
    """Apply macro + trade enrichment to parsed objectives."""
    enriched: list[ObjectiveRecord] = []
    for objective in objectives:
        fields = detect_macro_role(ctx, objective, buildings, summoners=summoners)
        enriched.append(objective.model_copy(update=fields))
    return enriched


def split_push_summary(matches_df: pd.DataFrame) -> dict[str, float | None]:
    """Aggregate split-push macro stats for dashboard cards (TOP-focused)."""
    if matches_df.empty:
        return {}

    def _mean(column: str) -> float | None:
        if column not in matches_df.columns:
            return None
        series = pd.to_numeric(matches_df[column], errors="coerce").dropna()
        if series.empty:
            return None
        value = float(series.mean())
        if not math.isfinite(value):
            return None
        return round(value, 3)

    split = _mean("objectives_split_push_rate")
    defend = _mean("objectives_defend_split_rate")
    balance = None
    if split is not None and defend is not None:
        diff = round(split - defend, 3)
        balance = diff if math.isfinite(diff) else None
    return {
        "avg_objectives_present_rate": _mean("objectives_present_rate"),
        "avg_objectives_split_push_rate": split,
        "avg_objectives_defend_split_rate": defend,
        "avg_unproductive_absence_rate": _mean("unproductive_absence_rate"),
        "avg_split_push_balance": balance,
        "avg_structure_tower_damage": _mean("structure_tower_damage"),
        "avg_towers_taken": _mean("towers_taken"),
    }


_OBJECTIVE_ASSET_IDS = frozenset(OBJECTIVE_VALUES)


def _structure_assets(obj: ObjectiveRecord) -> list[str]:
    assets = list(obj.trade_gain or []) + list(obj.trade_loss or [])
    return [asset for asset in assets if asset not in _OBJECTIVE_ASSET_IDS]


def _is_structure_trade_window(obj: ObjectiveRecord) -> bool:
    """True when this epic was paired with sidelane pressure or a structure swing."""
    return bool(
        obj.sidelane_pressure
        or obj.macro_role in {"split_pushing", "defending_split"}
        or _structure_assets(obj)
    )


def _structure_trade_converted(obj: ObjectiveRecord) -> bool:
    """Converted a real structure trade — not merely winning the epic monster."""
    if obj.trade_outcome in {"traded_for", "held"}:
        return True
    if obj.trade_outcome == "won" and _structure_assets(obj):
        return True
    return bool(
        obj.trade_value_delta is not None
        and obj.trade_value_delta >= 0
        and _structure_assets(obj)
    )


def objective_aggregate_rates(objectives: list[ObjectiveRecord]) -> dict[str, float | None]:
    """Per-game objective macro aggregates."""
    if not objectives:
        return {
            "objectives_accounted_for_rate": None,
            "unproductive_absence_rate": None,
            "objectives_split_push_rate": None,
            "objectives_defend_split_rate": None,
            "objective_trade_success_rate": None,
        }
    total = len(objectives)
    accounted = sum(1 for o in objectives if o.justified_absence)
    unproductive = sum(
        1
        for o in objectives
        if not o.present and not o.justified_absence and not o.dead_before
    )
    split_push = sum(1 for o in objectives if o.macro_role == "split_pushing")
    defend = sum(1 for o in objectives if o.macro_role == "defending_split")
    trade_attempts = [o for o in objectives if _is_structure_trade_window(o)]
    trade_wins = sum(1 for o in trade_attempts if _structure_trade_converted(o))
    return {
        "objectives_accounted_for_rate": round(accounted / total, 3),
        "unproductive_absence_rate": round(unproductive / total, 3),
        "objectives_split_push_rate": round(split_push / total, 3),
        "objectives_defend_split_rate": round(defend / total, 3),
        "objective_trade_success_rate": (
            round(trade_wins / len(trade_attempts), 3) if trade_attempts else None
        ),
    }
