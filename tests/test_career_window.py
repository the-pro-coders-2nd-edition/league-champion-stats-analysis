"""Rolling-window hit counting for Career goals."""

from __future__ import annotations

import pandas as pd

from league_stats.analysis.career.models import Rung
from league_stats.analysis.career.window import (
    count_hits,
    newest_game_ms,
    player_mean,
    player_median,
    player_quantile,
    recent_window,
)


def _frame(values: list[float], column: str = "cspm") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_creation_ms": list(range(len(values))),
            column: values,
        }
    )


def test_recent_window_takes_the_newest_games_first() -> None:
    frame = _frame([1.0, 2.0, 3.0, 4.0, 5.0])
    window = recent_window(frame, window=3)
    assert list(window["cspm"]) == [5.0, 4.0, 3.0]


def test_recent_window_of_empty_frame_is_empty() -> None:
    assert recent_window(pd.DataFrame()).empty


def test_count_hits_at_least() -> None:
    rung = Rung(text="", column="cspm", comparator="at_least", target=7.0, need=15)
    assert count_hits(_frame([6.0, 7.0, 8.0, 6.9]), rung) == 2


def test_count_hits_under_is_strict() -> None:
    rung = Rung(text="", column="deaths_pre20", comparator="under", target=3.0, need=15)
    frame = _frame([2.0, 3.0, 4.0, 0.0], column="deaths_pre20")
    assert count_hits(frame, rung) == 2


def test_count_hits_missing_column_is_zero() -> None:
    rung = Rung(text="", column="nope", comparator="at_least", target=1.0, need=15)
    assert count_hits(_frame([1.0]), rung) == 0


def test_player_median_and_mean() -> None:
    frame = _frame([4.0, 6.0, 8.0, 10.0])
    assert player_median(frame, "cspm") == 7.0
    assert player_mean(frame, "cspm") == 7.0
    assert player_median(frame, "nope") is None
    assert player_mean(pd.DataFrame(), "cspm") is None


def test_recent_window_ignores_games_at_or_before_the_start_line() -> None:
    frame = _frame([1.0, 2.0, 3.0, 4.0, 5.0])
    window = recent_window(frame, since_ms=2)
    assert list(window["cspm"]) == [5.0, 4.0]


def test_recent_window_with_no_games_after_the_start_line_is_empty() -> None:
    frame = _frame([1.0, 2.0, 3.0])
    assert recent_window(frame, since_ms=99).empty


def test_newest_game_ms() -> None:
    assert newest_game_ms(_frame([1.0, 2.0, 3.0])) == 2
    assert newest_game_ms(pd.DataFrame()) == 0
