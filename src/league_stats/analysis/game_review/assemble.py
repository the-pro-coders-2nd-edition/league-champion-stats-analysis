"""Assemble a full GameDetail from one MatchRecord and analysis frames."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from league_stats.analysis.buildings import structure_pressure_buckets
from league_stats.analysis.game_review.behaviors import evaluate_behaviors
from league_stats.analysis.game_review.skills import (
    build_skill_levels_by_level,
    skill_display_max_level,
)
from league_stats.analysis.trades import format_trade_asset_labels
from league_stats.analysis.game_review.compare import (
    compare_key_stats_to_baseline,
    compare_to_baseline,
)
from league_stats.analysis.game_review.hints import game_review_key_stats_for_role
from league_stats.analysis.game_review.score import compute_game_score
from league_stats.analysis.timeline import TIMELINE_SERIES_KEYS, timeline_dataframe_rows
from league_stats.core.config import RANKED_FLEX_QUEUE_ID, RANKED_SOLO_QUEUE_ID
from league_stats.core.models import (
    GameBuildInfo,
    GameDeathRow,
    GameDetail,
    GameFightRow,
    GameObjectiveRow,
    MatchRecord,
)
from league_stats.pipeline.frames import AnalysisFrames


def _queue_label(queue_id: int) -> str:
    if queue_id == RANKED_SOLO_QUEUE_ID:
        return "solo"
    if queue_id == RANKED_FLEX_QUEUE_ID:
        return "flex"
    return "all"


def _iso_date(game_creation_ms: int) -> str:
    dt = datetime.fromtimestamp(game_creation_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


_DEATH_FLAG_LABELS: dict[str, str] = {
    "alone": "Solo death",
    "after_greed": "Greed death",
    "before_neutral_objective": "Dead before objective",
    "to_gank": "Gank death",
    "outnumbered": "Outnumbered",
    "before_dragon": "Dead before dragon",
    "before_baron": "Dead before baron",
}


def _death_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    for column, label in _DEATH_FLAG_LABELS.items():
        if row.get(column):
            flags.append(label)
    return flags


def _as_name_list(value: Any) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, list):
        return [str(name) for name in value if name]
    return [str(value)]


def _filter_frame(df: pd.DataFrame, match_id: str) -> pd.DataFrame:
    if df.empty or "match_id" not in df.columns:
        return df.iloc[0:0]
    return df[df["match_id"] == match_id]


def _key_stats(game_row: dict[str, Any], *, role: str) -> dict[str, float | int | None]:
    keys = tuple(game_review_key_stats_for_role(role))
    return {key: game_row.get(key) for key in keys}


def assemble_game_detail(
    record: MatchRecord,
    frames: AnalysisFrames,
    *,
    baseline_means: dict[str, float],
    archetype: str,
    index: int,
    role: str,
) -> GameDetail:
    """Build one game review detail payload."""
    game_row = record.to_row()
    deaths_df = _filter_frame(frames.deaths_df, record.match_id)
    fights_df = _filter_frame(frames.teamfights_df, record.match_id)
    objectives_df = _filter_frame(frames.objectives_df, record.match_id)

    deaths_rows = deaths_df.to_dict("records") if not deaths_df.empty else []
    good, bad = evaluate_behaviors(
        record,
        game_row,
        deaths_rows,
        baseline_means=baseline_means,
        archetype=archetype,
    )

    timeline = [
        {
            key: float(row[key])
            for key in TIMELINE_SERIES_KEYS
            if key in row and row[key] is not None
        }
        for row in timeline_dataframe_rows(record.match_id, record.timeline)
    ]

    deaths = [
        GameDeathRow(
            minute=float(row.get("minute") or 0),
            zone=str(row.get("zone") or "unknown"),
            killer=str(row.get("killer")) if row.get("killer") else None,
            flags=_death_flags(row),
            gold_given=(
                int(row["gold_given"])
                if row.get("gold_given") is not None and pd.notna(row.get("gold_given"))
                else None
            ),
        )
        for row in deaths_rows
    ]

    fights = [
        GameFightRow(
            start_minute=float(row.get("start_minute") or 0),
            kills=int(row.get("kills") or 0),
            deaths=1 if row.get("died") else 0,
            assists=int(row.get("assists") or 0),
            damage=int(row.get("damage_dealt") or 0),
            fight_won=bool(row.get("fight_won")) if pd.notna(row.get("fight_won")) else False,
            allies_present=(
                int(row["allies_present"]) if pd.notna(row.get("allies_present")) else None
            ),
            enemies_present=(
                int(row["enemies_present"]) if pd.notna(row.get("enemies_present")) else None
            ),
            manpower_advantage=(
                int(row["manpower_advantage"]) if pd.notna(row.get("manpower_advantage")) else None
            ),
            ally_champions=list(row.get("ally_champions") or []),
            enemy_champions=list(row.get("enemy_champions") or []),
        )
        for row in (
            fights_df[fights_df["participated"].astype(bool)].to_dict("records")
            if not fights_df.empty and "participated" in fights_df.columns
            else []
        )
    ]

    objectives = [
        GameObjectiveRow(
            kind=str(row.get("kind") or "unknown"),
            minute=float(row.get("minute") or 0),
            taken_by_team=bool(row.get("taken_by_team")),
            present=bool(row.get("present")),
            dead_before=bool(row.get("dead_before")),
            wards_before=int(row.get("wards_before") or 0),
            secured_count=(
                int(row["secured_count"]) if pd.notna(row.get("secured_count")) else None
            ),
            objective_total=(
                int(row["objective_total"]) if pd.notna(row.get("objective_total")) else None
            ),
            macro_role=str(row["macro_role"]) if row.get("macro_role") else None,
            justified_absence=bool(row.get("justified_absence")),
            absence_reason=str(row["absence_reason"]) if row.get("absence_reason") else None,
            sidelane_pressure=bool(row.get("sidelane_pressure")),
            defending_lane=str(row["defending_lane"]) if row.get("defending_lane") else None,
            nearby_enemy_count=(
                int(row["nearby_enemy_count"])
                if row.get("nearby_enemy_count") is not None and pd.notna(row.get("nearby_enemy_count"))
                else None
            ),
            manpower_at_pit=str(row["manpower_at_pit"]) if row.get("manpower_at_pit") else None,
            pit_ally_champions=_as_name_list(row.get("pit_ally_champions")),
            pit_enemy_champions=_as_name_list(row.get("pit_enemy_champions")),
            tp_available=(
                bool(row["tp_available"])
                if row.get("tp_available") is not None and pd.notna(row.get("tp_available"))
                else None
            ),
            trade_outcome=str(row["trade_outcome"]) if row.get("trade_outcome") else None,
            trade_summary=str(row["trade_summary"]) if row.get("trade_summary") else None,
            trade_value_delta=(
                float(row["trade_value_delta"])
                if row.get("trade_value_delta") is not None and pd.notna(row.get("trade_value_delta"))
                else None
            ),
            trade_gain=[str(item) for item in (row.get("trade_gain") or []) if item],
            trade_loss=[str(item) for item in (row.get("trade_loss") or []) if item],
            trade_gain_labels=format_trade_asset_labels(
                [str(item) for item in (row.get("trade_gain") or []) if item],
                gained=True,
            ),
            trade_loss_labels=format_trade_asset_labels(
                [str(item) for item in (row.get("trade_loss") or []) if item],
                gained=False,
            ),
        )
        for row in (objectives_df.to_dict("records") if not objectives_df.empty else [])
    ]

    item_path = [item for item in record.item_path if item]
    if not item_path:
        item_path = [item for item in record.final_items if item]

    build = GameBuildInfo(
        keystone=record.runes.keystone,
        primary_tree=record.runes.primary_tree,
        secondary_tree=record.runes.secondary_tree,
        primary_runes=list(record.runes.primary_runes),
        secondary_runes=list(record.runes.secondary_runes),
        shards=list(record.runes.shards),
        summoners=list(record.summoners),
        skill_order=record.skill_order,
        skill_sequence=list(record.skill_sequence),
        skill_levels_by_level=build_skill_levels_by_level(record.skill_sequence),
        skill_max_level=skill_display_max_level(record.champ_level, record.skill_sequence),
        items=item_path,
    )

    return GameDetail(
        match_id=record.match_id,
        index=index,
        date=_iso_date(record.game_creation_ms),
        game_creation_ms=record.game_creation_ms,
        queue=_queue_label(record.queue_id),
        result="win" if record.win else "loss",
        duration_min=round(record.duration_min, 1),
        patch=record.patch,
        opponent=record.lane_opponent or "Unknown",
        side=record.side.value,
        kda=f"{record.combat.kills}/{record.combat.deaths}/{record.combat.assists}",
        archetype=archetype,
        account=record.account,
        score=compute_game_score(game_row, baseline_means, role=role),
        behaviors_good=good,
        behaviors_bad=bad,
        vs_baseline=compare_to_baseline(game_row, baseline_means, role=role),
        key_stats=_key_stats(game_row, role=role),
        key_stats_vs_baseline=compare_key_stats_to_baseline(
            game_row, baseline_means, role=role
        ),
        deaths=deaths,
        fights=fights,
        objectives=objectives,
        build=build,
        timeline=timeline,
        timeline_figure="",
        key_moments=list(record.key_moments),
        structure_pressure=structure_pressure_buckets(record),
        ai_recap=None,
    )
