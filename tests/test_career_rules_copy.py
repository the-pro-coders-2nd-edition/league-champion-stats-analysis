"""The Career rules panel must describe the engine that is actually running.

The panel drifted: it still said targets came from "your p50 toward peer p75" long
after they moved to a percentile-anchored target, claimed every goal asks 15 of 20
when the setup-window goal asks 12, and said "all three goals" when a block ships
fewer if the match history is thin. These tests derive every number from the
constants, so the copy cannot fall behind the code again -- including the anchor
and stretch themselves, which have already moved once (P35+15% to P45+17.5%).
"""

from __future__ import annotations

from league_stats.analysis.career.models import (
    CLEAR_BAR,
    HOLD_RATIO,
    SETUP_CLEAR_BAR,
    WINDOW,
    hold_bar,
)
from league_stats.analysis.career.steps import (
    ANCHOR_QUANTILE,
    BASELINE_GAMES,
    MAX_STEP_STRETCH,
    STEP_BANK,
)
from league_stats.presentation.career import CAREER_RULES


def _rule(key: str) -> dict[str, str]:
    for rule in CAREER_RULES:
        if rule["key"] == key:
            return rule
    raise AssertionError(f"no {key!r} rule; have {[r['key'] for r in CAREER_RULES]}")


def _text(key: str) -> str:
    rule = _rule(key)
    return f"{rule['value']} {rule['note']}"


def test_every_rule_has_a_key_a_value_and_a_note() -> None:
    for rule in CAREER_RULES:
        assert rule["key"] and rule["value"] and rule["note"]


def test_the_measurement_window_is_stated() -> None:
    assert str(WINDOW) in _text("Window")


def test_the_clear_bar_states_both_bars_that_exist() -> None:
    """Not every goal asks 15 of 20 -- the setup-window goal asks 12."""
    text = _text("Clear bar")

    assert f"{CLEAR_BAR} of {WINDOW}" in text
    assert f"{SETUP_CLEAR_BAR} of {WINDOW}" in text


def test_the_clear_bar_does_not_promise_exactly_three_goals() -> None:
    """A block ships fewer than three when the match history is missing columns."""
    assert "all three" not in _text("Clear bar").lower()


def test_the_hold_bar_states_the_ratio_and_the_derived_count() -> None:
    text = _text("Hold bar")

    assert f"{int(HOLD_RATIO * 100)}%" in text
    assert str(hold_bar(CLEAR_BAR)) in text


def test_the_target_rule_names_the_anchor_the_baseline_and_the_stretch() -> None:
    """``:g`` formatting, not ``int()``: a 17.5% stretch must not truncate to 17%."""
    text = _text("Target")

    assert f"P{ANCHOR_QUANTILE * 100:g}" in text
    assert str(BASELINE_GAMES) in text
    assert f"{MAX_STEP_STRETCH * 100:g}%" in text


def test_the_target_rule_no_longer_claims_the_retired_median_anchor() -> None:
    text = _text("Target").lower()

    assert "p50" not in text
    assert "toward peer p75" not in text


def test_the_target_rule_says_peers_can_only_lower_a_target() -> None:
    assert "down" in _text("Target").lower()


def test_a_rule_explains_that_a_block_is_a_category_of_chosen_steps() -> None:
    """Why two players weak at the same thing get different goals."""
    text = _text("Blocks").lower()

    assert "categor" in text


def test_a_rule_states_the_scope_and_that_the_filters_do_not_apply() -> None:
    text = _text("Scope").lower()

    assert "ranked" in text
    assert "filter" in text


def test_the_baseline_window_is_distinguished_from_the_measurement_window() -> None:
    """Two different windows are in play and conflating them is the easy mistake."""
    assert BASELINE_GAMES != WINDOW
    joined = " ".join(f"{r['key']} {r['value']} {r['note']}" for r in CAREER_RULES)
    assert str(BASELINE_GAMES) in joined and str(WINDOW) in joined


def test_the_bank_still_only_uses_the_two_documented_clear_bars() -> None:
    """If a step introduces a third bar, the Clear bar copy has to say so."""
    import pandas as pd

    from league_stats.analysis.career.tracks import TrackContext

    frame = pd.DataFrame(
        {c: [1.0] * 30 for c in ("cspm", "vspm", "damage_share", "deaths_pre20")}
    )
    frame["game_creation_ms"] = range(30)
    ctx = TrackContext(
        matches_df=frame, objectives_df=pd.DataFrame({"present": [1]}), role="MIDDLE"
    )
    bars = set()
    for step in STEP_BANK:
        rung = step.build(ctx)
        if rung is not None:
            bars.add(rung.need)
    assert bars <= {CLEAR_BAR, SETUP_CLEAR_BAR}
