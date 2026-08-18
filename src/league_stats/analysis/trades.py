"""Cross-map objective / structure trade pairing."""

from __future__ import annotations

from typing import Any, Literal

from league_stats.analysis.buildings import structure_display_label, structure_label
from league_stats.analysis.timeline import TimelineContext
from league_stats.core.models import BuildingRecord, ObjectiveKind, ObjectiveRecord
from league_stats.utils import ms_to_min

TradeOutcome = Literal["none", "won", "lost", "traded_for", "traded_away", "held", "even"]

STRUCTURE_VALUES: dict[str, float] = {
    "plate": 0.5,
    "top_t1": 2,
    "top_t2": 3,
    "top_inhib": 6,
    "mid_t1": 2,
    "mid_t2": 3,
    "mid_inhib": 6,
    "bot_t1": 2,
    "bot_t2": 3,
    "bot_inhib": 6,
    "nexus": 10,
}

OBJECTIVE_VALUES: dict[str, float] = {
    "dragon": 4,
    "elder": 10,
    "baron": 8,
    "herald": 3,
    "grubs": 2,
}

TRADE_WINDOW_BEFORE_MS = 90_000
TRADE_WINDOW_AFTER_MS = 45_000
DEFEND_TOWER_WINDOW_BEFORE_MS = 120_000
DEFEND_TOWER_WINDOW_AFTER_MS = 30_000


def _value_for_structure(structure_id: str) -> float:
    return STRUCTURE_VALUES.get(structure_id, 2.0)


def _value_for_objective(kind: ObjectiveKind) -> float:
    return OBJECTIVE_VALUES.get(kind.value, 4.0)


def _structures_in_window(
    buildings: list[BuildingRecord],
    ts: int,
    *,
    taken_by_team: bool | None = None,
    destroyed_team_id: int | None = None,
    before_ms: int = TRADE_WINDOW_BEFORE_MS,
    after_ms: int = TRADE_WINDOW_AFTER_MS,
) -> list[BuildingRecord]:
    rows: list[BuildingRecord] = []
    for building in buildings:
        if not (ts - before_ms <= building.timestamp_ms <= ts + after_ms):
            continue
        if taken_by_team is not None and building.taken_by_team != taken_by_team:
            continue
        if destroyed_team_id is not None and building.destroyed_team_id != destroyed_team_id:
            continue
        rows.append(building)
    return rows


def _format_trade_summary(structure_ids: list[str]) -> str:
    labels: list[str] = []
    seen: set[str] = set()
    for sid in structure_ids:
        if sid in seen:
            continue
        seen.add(sid)
        parts = sid.split("_", 1)
        if len(parts) == 2:
            lane, tier = parts
            tier_label = {"t1": "T1", "t2": "T2", "inhib": "Inhib"}.get(tier, tier.upper())
            labels.append(f"{lane.title()} {tier_label}")
        elif sid == "nexus":
            labels.append("Nexus")
    return " + ".join(labels)


def _format_owned_structures(structure_ids: list[str], *, enemy: bool) -> str:
    """Label structures with ``our`` or ``their`` ownership."""
    bare = _format_trade_summary(structure_ids)
    if not bare:
        return ""
    owner = "their" if enemy else "our"
    parts = [part.strip() for part in bare.split("+")]
    return " + ".join(f"{owner} {part}" for part in parts)


def format_trade_asset_labels(asset_ids: list[str], *, gained: bool) -> list[str]:
    """Human-readable labels for trade_gain / trade_loss asset ids."""
    labels: list[str] = []
    seen: set[str] = set()
    for asset_id in asset_ids:
        if asset_id in seen:
            continue
        seen.add(asset_id)
        if asset_id in OBJECTIVE_VALUES:
            labels.append(asset_id.title())
        else:
            labels.append(_format_owned_structures([asset_id], enemy=gained))
    return labels


def _objective_clause(kind: ObjectiveKind, *, secured: bool) -> str:
    label = kind.value.title()
    return f"Secured {label}" if secured else f"Lost {label}"


def _join_trade_parts(*parts: str) -> str:
    return " — ".join(part for part in parts if part)


def evaluate_objective_trade(
    ctx: TimelineContext,
    objective: ObjectiveRecord,
    buildings: list[BuildingRecord],
    *,
    macro_role: str,
    defending_lane: str | None,
) -> dict[str, Any]:
    """Pair one epic take with nearby structure swings."""
    ts = int(round(objective.minute * 60_000))
    my_team_id = 100 if ctx.blue_side else 200
    allied_structures = _structures_in_window(buildings, ts, taken_by_team=True)
    enemy_structures = _structures_in_window(buildings, ts, taken_by_team=False)
    gain_ids = [b.structure_id for b in allied_structures]
    loss_ids = [b.structure_id for b in enemy_structures]

    obj_value = _value_for_objective(objective.kind)
    gain_value = sum(_value_for_structure(sid) for sid in gain_ids)
    loss_value = sum(_value_for_structure(sid) for sid in loss_ids)

    trade_gain = list(gain_ids)
    trade_loss = list(loss_ids)
    if not objective.taken_by_team:
        trade_loss.append(objective.kind.value)

    if objective.taken_by_team:
        trade_gain.append(objective.kind.value)

    delta = gain_value + (obj_value if objective.taken_by_team else 0.0)
    delta -= loss_value + (0.0 if objective.taken_by_team else obj_value)

    outcome: TradeOutcome = "none"
    summary: str | None = None
    enemy_gained = _format_owned_structures(gain_ids, enemy=True)
    our_lost = _format_owned_structures(loss_ids, enemy=False)
    objective_clause = _objective_clause(objective.kind, secured=objective.taken_by_team)

    if macro_role == "defending_split" and defending_lane:
        my_team_destroyed = _structures_in_window(
            buildings,
            ts,
            destroyed_team_id=my_team_id,
            before_ms=DEFEND_TOWER_WINDOW_BEFORE_MS,
            after_ms=DEFEND_TOWER_WINDOW_AFTER_MS,
        )
        lane_losses = [b for b in my_team_destroyed if b.lane == defending_lane]
        if lane_losses:
            outcome = "lost"
            lost = lane_losses[-1]
            summary = (
                f"Lost our {structure_display_label(lost.lane, lost.tier, building_type=lost.building_type)}"
            )
        else:
            outcome = "held"
            summary = f"Held our {defending_lane.title()} tower"
    elif objective.taken_by_team:
        # Cross-map trade: we secured the epic while they took our structures.
        # Securing the epic plus enemy towers is a clean win, not a trade.
        if gain_ids:
            outcome = "won"
            if loss_ids:
                summary = _join_trade_parts(
                    objective_clause,
                    f"gained {enemy_gained}",
                    f"lost {our_lost}",
                )
            else:
                summary = _join_trade_parts(objective_clause, f"gained {enemy_gained}")
        elif loss_ids:
            outcome = "traded_away"
            summary = _join_trade_parts(objective_clause, f"lost {our_lost}")
        else:
            outcome = "won"
            summary = objective_clause
    elif not objective.taken_by_team:
        if gain_ids and loss_ids:
            outcome = "traded_for" if delta >= 0 else "lost"
            summary = _join_trade_parts(
                f"Gained {enemy_gained}",
                objective_clause,
                f"lost {our_lost}",
            )
        elif gain_ids:
            outcome = "traded_for" if delta >= 0 else "lost"
            summary = _join_trade_parts(f"Gained {enemy_gained}", objective_clause)
        elif loss_ids:
            outcome = "lost"
            summary = _join_trade_parts(objective_clause, f"lost {our_lost}")
        else:
            outcome = "lost"
            summary = objective_clause
    elif abs(delta) < 0.5:
        outcome = "even"

    return {
        "trade_outcome": outcome,
        "trade_gain": trade_gain,
        "trade_loss": trade_loss,
        "trade_value_delta": round(delta, 2) if outcome != "none" else None,
        "trade_summary": summary,
    }
