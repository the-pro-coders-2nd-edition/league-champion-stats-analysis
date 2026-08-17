"""Objective (dragon/baron/herald/grubs/elder) setup analysis."""

from __future__ import annotations

from typing import Any

import pandas as pd

from league_stats.analysis.buildings import extract_buildings
from league_stats.analysis.deaths import (
    BEFORE_OBJECTIVE_EXCLUDE_MS,
    in_objective_setup_window,
)
from league_stats.analysis.objective_macro import enrich_objectives
from league_stats.analysis.timeline import TimelineContext
from league_stats.core.models import BuildingRecord, MatchRecord, ObjectiveKind, ObjectiveRecord
from league_stats.utils import BARON_PIT, DRAGON_PIT, distance, ms_to_min

PRESENCE_RADIUS: float = 4_500.0
EARLY_RADIUS: float = 5_000.0
EARLY_LOOKBACK_MS: int = 60_000
DEAD_BEFORE_WINDOW_MS: int = 45_000
VISION_WINDOW_MS: int = 120_000


def _kind_of(event: dict[str, Any]) -> ObjectiveKind | None:
    """Map an ``ELITE_MONSTER_KILL`` event to an :class:`ObjectiveKind`.

    Args:
        event: The raw timeline event.

    Returns:
        The objective kind, or ``None`` for untracked monsters.
    """
    monster = str(event.get("monsterType", ""))
    subtype = str(event.get("monsterSubType", ""))
    if monster == "DRAGON":
        return ObjectiveKind.ELDER if subtype == "ELDER_DRAGON" else ObjectiveKind.DRAGON
    if monster == "BARON_NASHOR":
        return ObjectiveKind.BARON
    if monster == "RIFTHERALD":
        return ObjectiveKind.HERALD
    if monster == "HORDE":
        return ObjectiveKind.GRUBS
    return None


def _setup_at(
    ctx: TimelineContext,
    *,
    ts: int,
    pit: tuple[float, float],
    my_death_ts: list[int],
    wards: list[dict[str, Any]],
) -> dict[str, Any]:
    """Player setup context relative to an objective take timestamp."""
    pos_now = ctx.position_at_ms(ctx.participant_id, ts)
    pos_before = ctx.position_at_ms(ctx.participant_id, max(0, ts - EARLY_LOOKBACK_MS))
    present = pos_now is not None and distance(pos_now, pit) <= PRESENCE_RADIUS
    early = pos_before is not None and distance(pos_before, pit) <= EARLY_RADIUS
    player_wards = [
        w
        for w in wards
        if int(w.get("creatorId", 0)) == ctx.participant_id
        and 0 <= ts - int(w["timestamp"]) <= VISION_WINDOW_MS
    ]
    return {
        "present": present,
        "arrived_early": early,
        "arrived_late": present and not early,
        "dead_before": any(
            in_objective_setup_window(
                t,
                ts,
                window_ms=DEAD_BEFORE_WINDOW_MS,
                exclude_ms=BEFORE_OBJECTIVE_EXCLUDE_MS,
            )
            for t in my_death_ts
        ),
        "wards_before": len(player_wards),
        "control_wards_before": sum(
            1 for w in player_wards if w.get("wardType") == "CONTROL_WARD"
        ),
    }


def _record_for_event(
    ctx: TimelineContext,
    event: dict[str, Any],
    *,
    kind: ObjectiveKind,
    my_team_id: int,
    my_death_ts: list[int],
    wards: list[dict[str, Any]],
) -> ObjectiveRecord:
    ts = int(event["timestamp"])
    pit = DRAGON_PIT if kind in (ObjectiveKind.DRAGON, ObjectiveKind.ELDER) else BARON_PIT
    setup = _setup_at(ctx, ts=ts, pit=pit, my_death_ts=my_death_ts, wards=wards)
    return ObjectiveRecord(
        minute=ms_to_min(ts),
        kind=kind,
        taken_by_team=int(event.get("killerTeamId", 0)) == my_team_id,
        **setup,
    )


def _record_for_grubs(
    ctx: TimelineContext,
    events: list[dict[str, Any]],
    *,
    my_team_id: int,
    my_death_ts: list[int],
    wards: list[dict[str, Any]],
) -> ObjectiveRecord:
    """Collapse every Void grub kill in the match into one camp objective."""
    ordered = sorted(events, key=lambda e: int(e["timestamp"]))
    first_ts = int(ordered[0]["timestamp"])
    secured = sum(1 for e in ordered if int(e.get("killerTeamId", 0)) == my_team_id)
    total = len(ordered)
    present = False
    for event in ordered:
        setup = _setup_at(
            ctx,
            ts=int(event["timestamp"]),
            pit=BARON_PIT,
            my_death_ts=my_death_ts,
            wards=wards,
        )
        if setup["present"]:
            present = True
            break
    # Setup/vision judged against the start of the contest (first grub kill).
    first_setup = _setup_at(
        ctx, ts=first_ts, pit=BARON_PIT, my_death_ts=my_death_ts, wards=wards
    )
    return ObjectiveRecord(
        minute=ms_to_min(first_ts),
        kind=ObjectiveKind.GRUBS,
        taken_by_team=secured > (total - secured),
        present=present,
        arrived_early=first_setup["arrived_early"],
        arrived_late=present and not first_setup["arrived_early"],
        dead_before=first_setup["dead_before"],
        wards_before=first_setup["wards_before"],
        control_wards_before=first_setup["control_wards_before"],
        secured_count=secured,
        objective_total=total,
    )


def extract_objectives(ctx: TimelineContext) -> list[ObjectiveRecord]:
    """Contextualise every epic monster take in the game.

    Args:
        ctx: Timeline context.

    Returns:
        One :class:`~models.ObjectiveRecord` per epic monster take. Void grubs
        (HORDE) are collapsed into a single camp record with a secured split.
    """
    my_team_id = 100 if ctx.blue_side else 200
    my_death_ts = [
        int(e["timestamp"])
        for e in ctx.events_of("CHAMPION_KILL")
        if int(e.get("victimId", 0)) == ctx.participant_id
    ]
    wards = ctx.events_of("WARD_PLACED")

    records: list[ObjectiveRecord] = []
    grub_events: list[dict[str, Any]] = []
    for event in ctx.events_of("ELITE_MONSTER_KILL"):
        kind = _kind_of(event)
        if kind is None:
            continue
        if kind is ObjectiveKind.GRUBS:
            grub_events.append(event)
            continue
        records.append(
            _record_for_event(
                ctx,
                event,
                kind=kind,
                my_team_id=my_team_id,
                my_death_ts=my_death_ts,
                wards=wards,
            )
        )
    if grub_events:
        records.append(
            _record_for_grubs(
                ctx,
                grub_events,
                my_team_id=my_team_id,
                my_death_ts=my_death_ts,
                wards=wards,
            )
        )
    records.sort(key=lambda r: r.minute)
    return records


def extract_objectives_enriched(
    ctx: TimelineContext,
    *,
    summoners: list[str],
) -> tuple[list[ObjectiveRecord], list[BuildingRecord]]:
    """Extract objectives with macro/trade enrichment and building records."""
    buildings = extract_buildings(ctx)
    objectives = enrich_objectives(ctx, extract_objectives(ctx), buildings, summoners=summoners)
    return objectives, buildings


def objectives_dataframe(records: list[MatchRecord]) -> pd.DataFrame:
    """Flatten every objective event into a dataframe.

    Args:
        records: Parsed match records.

    Returns:
        One row per epic monster take with the player's setup context.
    """
    rows: list[dict[str, Any]] = []
    for record in records:
        for obj in record.objectives:
            rows.append(
                {
                    "match_id": record.match_id,
                    "win": int(record.win),
                    "kind": obj.kind.value,
                    "minute": round(obj.minute, 2),
                    "taken_by_team": obj.taken_by_team,
                    "present": obj.present,
                    "arrived_early": obj.arrived_early,
                    "arrived_late": obj.arrived_late,
                    "dead_before": obj.dead_before,
                    "wards_before": obj.wards_before,
                    "control_wards_before": obj.control_wards_before,
                    "secured_count": obj.secured_count,
                    "objective_total": obj.objective_total,
                    "macro_role": obj.macro_role,
                    "justified_absence": obj.justified_absence,
                    "absence_reason": obj.absence_reason,
                    "sidelane_pressure": obj.sidelane_pressure,
                    "defending_lane": obj.defending_lane,
                    "nearby_enemy_count": obj.nearby_enemy_count,
                    "manpower_at_pit": obj.manpower_at_pit,
                    "pit_ally_champions": list(obj.pit_ally_champions),
                    "pit_enemy_champions": list(obj.pit_enemy_champions),
                    "tp_available": obj.tp_available,
                    "trade_outcome": obj.trade_outcome,
                    "trade_summary": obj.trade_summary,
                    "trade_value_delta": obj.trade_value_delta,
                    "trade_gain": list(obj.trade_gain),
                    "trade_loss": list(obj.trade_loss),
                }
            )
    return pd.DataFrame(rows)


def _taken_rate(group: pd.DataFrame) -> float:
    """Prefer secured-share for multi-kill camps (grubs); else boolean take rate."""
    if {"secured_count", "objective_total"}.issubset(group.columns):
        shares = []
        for _, row in group.iterrows():
            secured = row.get("secured_count")
            total = row.get("objective_total")
            if pd.notna(secured) and pd.notna(total) and float(total) > 0:
                shares.append(float(secured) / float(total))
            else:
                shares.append(float(bool(row.get("taken_by_team"))))
        if shares:
            return round(float(sum(shares) / len(shares)), 3)
    return round(float(group["taken_by_team"].mean()), 3)


def objective_summary(obj_df: pd.DataFrame) -> dict[str, Any]:
    """Aggregate per-objective-kind setup statistics.

    Args:
        obj_df: Output of :func:`objectives_dataframe`.

    Returns:
        Presence/death/vision rates per objective kind plus overall rates.
    """
    if obj_df.empty:
        return {"total_objectives": 0, "by_kind": {}}
    by_kind: dict[str, Any] = {}
    for kind, group in obj_df.groupby("kind"):
        by_kind[str(kind)] = {
            "count": int(len(group)),
            "taken_rate": _taken_rate(group),
            "presence_rate": round(float(group["present"].mean()), 3),
            "early_rate": round(float(group["arrived_early"].mean()), 3),
            "dead_before_rate": round(float(group["dead_before"].mean()), 3),
            "avg_wards_before": round(float(group["wards_before"].mean()), 2),
            "avg_control_wards_before": round(float(group["control_wards_before"].mean()), 2),
        }
    return {
        "total_objectives": int(len(obj_df)),
        "overall_presence_rate": round(float(obj_df["present"].mean()), 3),
        "overall_dead_before_rate": round(float(obj_df["dead_before"].mean()), 3),
        "by_kind": by_kind,
    }
