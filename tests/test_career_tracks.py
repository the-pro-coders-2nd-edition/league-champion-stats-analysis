"""Career track eligibility, rung generation and weakest-first ranking."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from league_stats.analysis.career.tracks import (
    TRACK_SPECS,
    TRACKS_BY_KEY,
    TrackContext,
    build_rungs,
    is_significant,
    rank_track_keys,
    track_spec,
)


@dataclass(frozen=True)
class _Component:
    name: str
    score: float


def _matches(**columns: list[float]) -> pd.DataFrame:
    size = len(next(iter(columns.values())))
    return pd.DataFrame({"game_creation_ms": list(range(size)), **columns})


def _ctx(matches: pd.DataFrame, **kwargs: object) -> TrackContext:
    return TrackContext(
        matches_df=matches,
        objectives_df=kwargs.get("objectives_df", pd.DataFrame()),  # type: ignore[arg-type]
        role=str(kwargs.get("role", "MIDDLE")),
        peer_p75=kwargs.get("peer_p75", {}),  # type: ignore[arg-type]
    )


def test_laning_income_steps_proportionally_from_p50_to_peer_p75() -> None:
    ctx = _ctx(_matches(cspm=[6.0, 6.0, 6.0]), peer_p75={"cspm": 7.5})
    rungs = build_rungs(TRACKS_BY_KEY["laning_income"], ctx)

    assert rungs is not None
    assert [r.target for r in rungs] == [6.5, 7.0, 7.5]
    assert [r.need for r in rungs] == [15, 15, 15]
    assert rungs[0].comparator == "at_least"
    assert rungs[2].text == "7.5 CS per minute in 15 of 20 games"


def test_laning_income_falls_back_to_the_players_own_p75() -> None:
    # No peer percentiles: the ceiling becomes what the player's good games do.
    ctx = _ctx(_matches(cspm=[5.0, 6.0, 7.0, 8.0, 9.0]))
    rungs = build_rungs(TRACKS_BY_KEY["laning_income"], ctx)

    assert rungs is not None
    assert [r.target for r in rungs] == [7.3, 7.7, 8.0]
    assert not is_significant(TRACKS_BY_KEY["laning_income"], ctx)


def test_laning_income_falls_back_when_already_past_peer_p75() -> None:
    ctx = _ctx(_matches(cspm=[7.0, 8.0, 9.0, 10.0]), peer_p75={"cspm": 7.5})
    rungs = build_rungs(TRACKS_BY_KEY["laning_income"], ctx)

    assert rungs is not None
    # p50 8.5 → own p75 9.25 in three steps.
    assert [r.target for r in rungs] == [8.8, 9.0, 9.2]


def test_laning_income_is_significant_only_when_behind_peers() -> None:
    behind = _ctx(_matches(cspm=[6.0, 6.0, 6.0]), peer_p75={"cspm": 7.5})
    ahead = _ctx(_matches(cspm=[8.0, 9.0, 10.0]), peer_p75={"cspm": 7.5})
    assert is_significant(TRACKS_BY_KEY["laning_income"], behind)
    assert not is_significant(TRACKS_BY_KEY["laning_income"], ahead)


def test_laning_income_ineligible_when_the_player_has_zero_variance() -> None:
    # Same CS every game and no peer data: nothing to step toward.
    ctx = _ctx(_matches(cspm=[6.0, 6.0, 6.0]))
    assert build_rungs(TRACKS_BY_KEY["laning_income"], ctx) is None


def test_vision_uptime_builds_for_any_role_but_only_flags_for_support() -> None:
    matches = _matches(vspm=[0.60, 0.60, 0.60])
    mid = _ctx(matches, peer_p75={"vspm": 0.9})
    support = _ctx(matches, role="UTILITY", peer_p75={"vspm": 0.9})

    for ctx in (mid, support):
        rungs = build_rungs(TRACKS_BY_KEY["vision_uptime"], ctx)
        assert rungs is not None
        assert [r.target for r in rungs] == [0.7, 0.8, 0.9]

    # Only the support coach rule exists, so only support treats it as a finding.
    assert is_significant(TRACKS_BY_KEY["vision_uptime"], support)
    assert not is_significant(TRACKS_BY_KEY["vision_uptime"], mid)


def test_fight_impact_renders_whole_percentages() -> None:
    ctx = _ctx(_matches(damage_share=[0.20, 0.20]), peer_p75={"damage_share": 0.29})
    rungs = build_rungs(TRACKS_BY_KEY["fight_impact"], ctx)

    assert rungs is not None
    assert rungs[2].text == "29% team damage share in 15 of 20 games"
    assert rungs[0].column == "damage_share"


def test_death_discipline_tightens_then_pivots_to_the_setup_window() -> None:
    matches = _matches(
        deaths_pre20=[3.0, 3.0, 3.0, 3.0],
        deaths_before_neutral_objective=[1.0, 0.0, 1.0, 2.0],
    )
    rungs = build_rungs(TRACKS_BY_KEY["death_discipline"], _ctx(matches))

    assert rungs is not None
    assert rungs[0].text == "Under 2 deaths before 20 min in 15 of 20 games"
    assert rungs[1].text == "Under 1 deaths before 20 min in 15 of 20 games"
    assert rungs[0].comparator == "under"
    assert rungs[2].column == "deaths_before_neutral_objective"
    assert rungs[2].target == 1.0
    assert rungs[2].need == 12


def test_death_discipline_ineligible_when_already_at_one_death() -> None:
    matches = _matches(
        deaths_pre20=[1.0, 1.0, 1.0],
        deaths_before_neutral_objective=[0.0, 0.0, 0.0],
    )
    assert build_rungs(TRACKS_BY_KEY["death_discipline"], _ctx(matches)) is None


def test_map_presence_builds_a_three_behaviour_sequence() -> None:
    matches = _matches(
        objectives_present_rate=[0.4, 0.4, 0.4],
        tf_participation=[0.62, 0.62, 0.62],
        control_wards=[0.0, 1.0, 0.0],
    )
    objectives = pd.DataFrame({"present": [1, 0, 0, 0, 1, 0]})
    rungs = build_rungs(TRACKS_BY_KEY["map_presence"], _ctx(matches, objectives_df=objectives))

    assert rungs is not None
    assert rungs[0].text == "Present at 40% of pit takes in 15 of 20 games"
    assert rungs[1].text == "One control ward per game in 15 of 20 games"
    assert rungs[1].target == 1.0
    assert rungs[2].text == "Attend 70% of teamfights in 15 of 20 games"


def test_map_presence_still_builds_when_presence_is_healthy() -> None:
    matches = _matches(
        objectives_present_rate=[0.8, 0.8],
        tf_participation=[0.8, 0.8],
        control_wards=[1.0, 1.0],
    )
    objectives = pd.DataFrame({"present": [1, 1, 1, 1]})
    ctx = _ctx(matches, objectives_df=objectives)

    rungs = build_rungs(TRACKS_BY_KEY["map_presence"], ctx)
    assert rungs is not None
    assert rungs[0].text == "Present at 100% of pit takes in 15 of 20 games"
    assert not is_significant(TRACKS_BY_KEY["map_presence"], ctx)


def test_economy_discipline_steps_below_current_averages() -> None:
    matches = _matches(
        avg_unspent_gold=[1650.0, 1650.0],
        avg_unspent_gold_per_fight=[1420.0, 1420.0],
        avg_gold_at_death=[1180.0, 1180.0],
    )
    rungs = build_rungs(TRACKS_BY_KEY["economy_discipline"], _ctx(matches))

    assert rungs is not None
    assert rungs[0].text == "Under 1500g banked before recall in 15 of 20 games"
    assert rungs[1].text == "Under 1300g banked entering fights in 15 of 20 games"
    assert rungs[2].text == "Under 1000g banked on death in 15 of 20 games"
    assert all(r.comparator == "under" for r in rungs)


def test_economy_discipline_tightens_further_for_healthy_players() -> None:
    matches = _matches(
        avg_unspent_gold=[600.0, 600.0],
        avg_unspent_gold_per_fight=[500.0, 500.0],
        avg_gold_at_death=[400.0, 400.0],
    )
    ctx = _ctx(matches)
    rungs = build_rungs(TRACKS_BY_KEY["economy_discipline"], ctx)

    assert rungs is not None
    # Already under the component norm, so the target tightens rather than
    # loosening back up to it.
    assert rungs[0].text == "Under 500g banked before recall in 15 of 20 games"
    assert rungs[2].text == "Under 300g banked on death in 15 of 20 games"
    assert not is_significant(TRACKS_BY_KEY["economy_discipline"], ctx)


def test_a_healthy_player_can_still_fill_three_blocks() -> None:
    matches = _matches(
        cspm=[8.0, 9.0, 10.0, 11.0],
        vspm=[1.0, 1.2, 1.4, 1.6],
        damage_share=[0.28, 0.30, 0.32, 0.34],
        deaths_pre20=[3.0, 3.0, 3.0, 3.0],
        deaths_before_neutral_objective=[0.0, 0.0, 0.0, 0.0],
        avg_unspent_gold=[600.0, 600.0, 600.0, 600.0],
        avg_unspent_gold_per_fight=[500.0, 500.0, 500.0, 500.0],
        avg_gold_at_death=[400.0, 400.0, 400.0, 400.0],
        tf_participation=[0.9, 0.9, 0.9, 0.9],
        control_wards=[2.0, 2.0, 2.0, 2.0],
        objectives_present_rate=[0.9, 0.9, 0.9, 0.9],
    )
    objectives = pd.DataFrame({"present": [1, 1, 1, 1]})
    ctx = _ctx(matches, objectives_df=objectives)

    buildable = [spec.key for spec in TRACK_SPECS if build_rungs(spec, ctx) is not None]
    assert len(buildable) >= 3


def test_rank_track_keys_puts_the_weakest_category_first() -> None:
    components = [
        _Component("Laning", 70.0),
        _Component("Economy", 22.0),
        _Component("Fight", 55.0),
        _Component("Survival", 41.0),
        _Component("Vision", 90.0),
        _Component("Objectives", 33.0),
    ]
    assert rank_track_keys(components)[:3] == [
        "economy_discipline",
        "map_presence",
        "death_discipline",
    ]


def test_rank_track_keys_uses_the_jungle_category_name() -> None:
    components = [_Component("Early game", 5.0), _Component("Vision", 95.0)]
    assert rank_track_keys(components)[0] == "laning_income"


def test_rank_track_keys_excludes_and_defaults_missing_categories() -> None:
    components = [_Component("Economy", 10.0)]
    ranked = rank_track_keys(components, exclude={"economy_discipline"})
    assert "economy_discipline" not in ranked
    assert len(ranked) == 5


def test_track_spec_lookup() -> None:
    assert track_spec("map_presence") is not None
    assert track_spec("nope") is None
