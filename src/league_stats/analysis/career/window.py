"""Rolling-window reads: how many of the last N games hit a goal's target."""

from __future__ import annotations

import math

import pandas as pd

from league_stats.analysis.career.models import WINDOW, Rung


def recent_window(matches_df: pd.DataFrame, window: int = WINDOW) -> pd.DataFrame:
    """The most recent ``window`` games, newest first."""
    if matches_df.empty:
        return matches_df
    if "game_creation_ms" in matches_df.columns:
        ordered = matches_df.sort_values("game_creation_ms", ascending=False)
    else:
        ordered = matches_df
    return ordered.head(window)


def count_hits(window_df: pd.DataFrame, rung: Rung) -> int:
    """Games in the window that meet a rung's target."""
    if window_df.empty or rung.column not in window_df.columns:
        return 0
    series = pd.to_numeric(window_df[rung.column], errors="coerce").dropna()
    if series.empty:
        return 0
    if rung.comparator == "at_least":
        return int((series >= rung.target).sum())
    return int((series < rung.target).sum())


def player_median(matches_df: pd.DataFrame, column: str) -> float | None:
    """Median of a column, or ``None`` when it is missing or unusable."""
    return _statistic(matches_df, column, "median")


def player_mean(matches_df: pd.DataFrame, column: str) -> float | None:
    """Mean of a column, or ``None`` when it is missing or unusable."""
    return _statistic(matches_df, column, "mean")


def _statistic(matches_df: pd.DataFrame, column: str, kind: str) -> float | None:
    if matches_df.empty or column not in matches_df.columns:
        return None
    series = pd.to_numeric(matches_df[column], errors="coerce").dropna()
    if series.empty:
        return None
    value = float(series.median() if kind == "median" else series.mean())
    return value if math.isfinite(value) else None
