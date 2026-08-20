"""Rolling-window reads: how many of the last N games hit a goal's target."""

from __future__ import annotations

import math

import pandas as pd

from league_stats_runner.analysis.career.models import WINDOW, Rung


def recent_window(
    matches_df: pd.DataFrame,
    window: int = WINDOW,
    *,
    since_ms: int = 0,
) -> pd.DataFrame:
    """The most recent ``window`` games newer than ``since_ms``, newest first.

    ``since_ms`` is what stops a freshly generated block from completing on
    games that were already in the bag when it was created.
    """
    if matches_df.empty:
        return matches_df
    if "game_creation_ms" not in matches_df.columns:
        return matches_df.head(window)
    frame = matches_df
    if since_ms:
        created = pd.to_numeric(frame["game_creation_ms"], errors="coerce")
        frame = frame[created > since_ms]
    return frame.sort_values("game_creation_ms", ascending=False).head(window)


def newest_game_ms(matches_df: pd.DataFrame) -> int:
    """Timestamp of the most recent game, used as a new block's start line."""
    if matches_df.empty or "game_creation_ms" not in matches_df.columns:
        return 0
    created = pd.to_numeric(matches_df["game_creation_ms"], errors="coerce").dropna()
    return int(created.max()) if not created.empty else 0


def count_hits(window_df: pd.DataFrame, rung: Rung) -> int:
    """Games in the window that meet a rung's target."""
    if window_df.empty or rung.column not in window_df.columns:
        return 0
    series = pd.to_numeric(window_df[rung.column], errors="coerce").dropna()
    if series.empty:
        return 0
    return series_hits(series, rung.comparator, rung.target)


def series_hits(series: pd.Series, comparator: str, target: float) -> int:
    """How many values meet ``comparator`` against ``target``."""
    if comparator == "at_least":
        return int((series >= target).sum())
    if comparator == "at_most":
        return int((series <= target).sum())
    return int((series < target).sum())


def player_median(matches_df: pd.DataFrame, column: str) -> float | None:
    """Median of a column, or ``None`` when it is missing or unusable."""
    return _statistic(matches_df, column, "median")


def player_mean(matches_df: pd.DataFrame, column: str) -> float | None:
    """Mean of a column, or ``None`` when it is missing or unusable."""
    return _statistic(matches_df, column, "mean")


def player_quantile(matches_df: pd.DataFrame, column: str, q: float) -> float | None:
    """Quantile of a column, or ``None`` when it is missing or unusable.

    Used as the rung ceiling when no peer percentile is available: the player's
    own good games become the target to reproduce consistently.
    """
    if matches_df.empty or column not in matches_df.columns:
        return None
    series = pd.to_numeric(matches_df[column], errors="coerce").dropna()
    if series.empty:
        return None
    value = float(series.quantile(q))
    return value if math.isfinite(value) else None


def _statistic(matches_df: pd.DataFrame, column: str, kind: str) -> float | None:
    if matches_df.empty or column not in matches_df.columns:
        return None
    series = pd.to_numeric(matches_df[column], errors="coerce").dropna()
    if series.empty:
        return None
    value = float(series.median() if kind == "median" else series.mean())
    return value if math.isfinite(value) else None
