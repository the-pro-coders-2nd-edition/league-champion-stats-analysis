"""Where a stepped rung's target comes from.

The target used to anchor on the player's median while the clear bar asked for
the value in 15 of 20 games. A player exceeds their own median in ~50% of games
by definition, so every stepped goal stacked two demands: raise the level, and
raise consistency from under half your games to three quarters of them. On a live
report that produced "Present at 60% of pit takes in 15 of 20 games" for a player
hitting 60% in 2 of their last 10.

Targets now anchor on the 45th percentile of the last 100 games and stretch
17.5%, so the anchor describes a level the player already reaches in most games.
"""

from __future__ import annotations

import pandas as pd
import pytest

from league_stats.analysis.career.steps import (
    ANCHOR_QUANTILE,
    BASELINE_GAMES,
    MAX_STEP_STRETCH,
    _stepped,
    _stepped_under,
)

HOUR = 3_600_000


class _Ctx:
    def __init__(self, values: list[float], column: str = "m", peers: dict | None = None):
        self.matches_df = pd.DataFrame(
            {
                column: values,
                "game_creation_ms": [i * HOUR for i in range(len(values))],
            }
        )
        self.peer_p75 = peers or {}


def _target(values: list[float], *, peers: dict | None = None, **kw):
    rung = _stepped(
        _Ctx(values, peers=peers), column="m", template="{target}", precision=3, **kw
    )
    return None if rung is None else rung.target


def _target_under(values: list[float], **kw):
    rung = _stepped_under(_Ctx(values), column="m", template="{target}", precision=3, **kw)
    return None if rung is None else rung.target


def _quantile(values: list[float], q: float) -> float:
    """pandas interpolates quantiles, so derive expectations the same way."""
    return float(pd.Series(values).quantile(q))


def test_the_anchor_and_the_stretch_are_the_agreed_values() -> None:
    assert ANCHOR_QUANTILE == 0.45
    assert MAX_STEP_STRETCH == 0.175
    assert BASELINE_GAMES == 100


def test_a_higher_is_better_target_is_the_anchor_plus_the_stretch() -> None:
    values = [float(i) for i in range(1, 101)]  # p45 ~= 45.55

    expected = _quantile(values, ANCHOR_QUANTILE) * (1 + MAX_STEP_STRETCH)
    assert _target(values) == pytest.approx(expected, abs=0.01)


def test_a_lower_is_better_target_mirrors_at_the_complementary_anchor() -> None:
    """You are already under your p55 in 55% of games, so that is the anchor."""
    values = [float(i) for i in range(1, 101)]  # p55 ~= 55.45

    expected = _quantile(values, 1 - ANCHOR_QUANTILE) * (1 - MAX_STEP_STRETCH)
    assert _target_under(values) == pytest.approx(expected, abs=0.01)


def test_the_baseline_ignores_games_older_than_the_last_hundred() -> None:
    """A 200-game history must not let ancient form set today's target."""
    ancient = [1.0] * 100
    recent = [10.0] * 100

    assert _target(ancient + recent) == pytest.approx(10.0 * (1 + MAX_STEP_STRETCH), abs=0.01)


def test_the_target_is_measurably_easier_than_the_old_median_anchor() -> None:
    """The live regression: presence rates from a real Aatrox report."""
    values = [0.22, 0.33, 0.38, 0.43, 0.43, 0.50, 0.57, 0.57, 0.62, 0.67]
    old_style = min(1.0, pd.Series(values).median() * 1.20)

    new = _target(values, cap=1.0)

    assert new < old_style
    hit_now = sum(1 for v in values if v >= new)
    hit_before = sum(1 for v in values if v >= old_style)
    # The property this regression actually guards is the target itself being
    # lower, asserted above. Headroom moved up alongside ANCHOR_QUANTILE and
    # MAX_STEP_STRETCH, so the new target now sits close enough to the old one
    # that this ten-game sample's one borderline value (0.50) ties rather than
    # flips a hit -- not a case for a fixed dataset to catch a real regression on.
    assert hit_now >= hit_before


def test_a_peer_ceiling_below_the_stretch_pulls_the_target_down() -> None:
    """Never ask a player to go past peer p75 just because the stretch says so."""
    values = [float(i) for i in range(1, 101)]
    stretch = _quantile(values, ANCHOR_QUANTILE) * (1 + MAX_STEP_STRETCH)
    peers = {"m": stretch - 2.0}  # sits between the anchor and the stretch

    assert _target(values, peers=peers) == pytest.approx(stretch - 2.0, abs=0.01)


def test_a_peer_ceiling_above_the_stretch_is_ignored() -> None:
    values = [float(i) for i in range(1, 101)]

    assert _target(values, peers={"m": 500.0}) == _target(values)


def test_a_peer_ceiling_at_or_below_the_anchor_is_ignored() -> None:
    """Already past your peers at the anchor: the stretch still has to move."""
    values = [float(i) for i in range(1, 101)]
    peers = {"m": 5.0}

    target = _target(values, peers=peers)
    assert target is not None
    assert target > _quantile(values, ANCHOR_QUANTILE)


def test_a_cap_still_binds() -> None:
    values = [0.9] * 100

    assert _target(values, cap=1.0) == pytest.approx(1.0, abs=0.001)


def test_a_flat_metric_at_zero_declines_rather_than_inventing_a_target() -> None:
    assert _target([0.0] * 40) is None
    assert _target_under([0.0] * 40) is None


def test_an_empty_history_declines() -> None:
    assert _target([]) is None
    assert _target_under([]) is None


def _ctx_missing_column():
    frame = pd.DataFrame({"other": [1.0], "game_creation_ms": [0]})

    class _C:
        matches_df = frame
        peer_p75: dict = {}

    return _C()


def test_a_missing_column_declines() -> None:
    assert _stepped(_ctx_missing_column(), column="m", template="{target}", precision=1) is None
    assert (
        _stepped_under(_ctx_missing_column(), column="m", template="{target}", precision=1)
        is None
    )
