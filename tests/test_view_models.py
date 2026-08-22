"""Tests for dashboard view-model helpers."""

from __future__ import annotations

from league_stats_runner.pipeline.view_models import (
    annotate_card_tiers,
    cards_from_specs,
    enrich_value_semantics,
    form_delta_chart_value,
    form_row_display,
    form_sample_subtitle,
    format_map_distance,
    overview_card_entries,
    priority_label,
)
from league_stats_common.core.role_metrics import MetricSpec, role_profile
from league_stats_runner.presentation.metric_colors import interpolate_metric_color, LOSS_HEX, score_form_delta


def test_priority_label_maps_badge_classes() -> None:
    assert priority_label("high") == "High"
    assert priority_label("medium") == "Medium"
    assert priority_label("low") == "Low"


def test_format_map_distance_uses_screen_landmarks() -> None:
    assert format_map_distance(None) == "—"
    assert format_map_distance(3000) == "3k ≈ 1 screen"
    assert format_map_distance(4800) == "4.8k ≈ 1.6 screens"
    assert format_map_distance(9000) == "9k ≈ 3 screens"


def test_distance_metric_spec_formats_with_landmarks() -> None:
    cards = cards_from_specs(
        (MetricSpec("Dist to ADC", "positioning", "dist_bottom"),),
        {"positioning": {"dist_bottom": 4500}},
        section="lane",
        role="UTILITY",
    )
    assert cards[0]["value"] == "4.5k ≈ 1.5 screens"


def test_annotate_card_tiers_orders_headline_metrics_first() -> None:
    entries = [
        {"label": "Roams pre-15", "value": "1.2"},
        {"label": "Gold diff @10", "value": "+120"},
        {"label": "Lane win rate", "value": "55%"},
    ]
    ordered = annotate_card_tiers(entries, "lane")
    assert [entry["label"] for entry in ordered[:3]] == [
        "Gold diff @10",
        "Lane win rate",
        "Roams pre-15",
    ]
    assert ordered[0]["tier"] == "headline"
    assert ordered[-1]["tier"] == "more"


def test_death_section_cards_skip_value_colors() -> None:
    profile = role_profile("MIDDLE")
    summaries = {
        "deaths": {
            "solo_death_rate": 0.50,
            "gank_death_rate": 0.20,
        }
    }
    cards = cards_from_specs(profile.deaths[:2], summaries, section="deaths")
    for card in cards:
        assert not card.get("value_color")
        assert not card.get("value_class")


def test_enrich_value_semantics_colors_diff_and_win_rate() -> None:
    from league_stats_runner.presentation.metric_colors import CS_DIFF_SPAN, score_lane_diff

    gd = {"label": "Gold diff @10", "value": "+250", "value_class": ""}
    csd = {"label": "CS diff @10", "value": "+8", "value_class": ""}
    wr = {"label": "Lane win rate", "value": "42%", "value_class": ""}
    mid_wr = {"label": "Lane win rate", "value": "50%", "value_class": ""}
    fight_deaths = {"label": "Death rate in fights", "value": "42%", "value_class": ""}
    deaths_game = {"label": "Deaths/game", "value": "4.2", "value_class": ""}
    enrich_value_semantics(gd)
    enrich_value_semantics(csd)
    enrich_value_semantics(wr)
    enrich_value_semantics(mid_wr)
    enrich_value_semantics(fight_deaths)
    enrich_value_semantics(deaths_game)
    assert gd["value_class"] == "win"
    assert csd["value_class"] == "win"
    assert wr["value_class"] == "loss"
    assert mid_wr["value_class"] == ""
    assert gd["value_color"] != wr["value_color"]
    assert csd["value_color"] == interpolate_metric_color(score_lane_diff(8.0, span=CS_DIFF_SPAN))
    assert mid_wr["value_color"] == interpolate_metric_color(0.0)
    assert not fight_deaths.get("value_color")
    assert not fight_deaths.get("value_class")
    assert not deaths_game.get("value_color")
    assert not deaths_game.get("value_class")


def test_overview_card_entries_skip_deaths_game_color() -> None:
    cards = overview_card_entries(
        {
            "winrate": 0.53,
            "avg_kda": "3.1",
            "avg_dpm": "640",
            "avg_cspm": "7.2",
            "avg_damage_share": 0.24,
            "avg_deaths": 6.5,
            "avg_vspm": 1.1,
            "avg_duration": 28,
        }
    )
    deaths = next(card for card in cards if card["label"] == "Deaths/game")
    assert not deaths.get("value_color")
    assert not deaths.get("value_class")


def test_overview_card_entries_include_tiers() -> None:
    cards = overview_card_entries(
        {
            "winrate": 0.53,
            "avg_kda": "3.1",
            "avg_dpm": "640",
            "avg_cspm": "7.2",
            "avg_damage_share": 0.24,
            "avg_deaths": 4.2,
            "avg_vspm": 1.1,
            "avg_duration": 28,
        }
    )
    headline = [card for card in cards if card.get("tier") == "headline"]
    assert len(headline) == 4
    assert headline[0]["label"] == "Win rate"
    assert headline[2]["label"] == "DPM"


def test_overview_card_entries_use_cc_for_support() -> None:
    cards = overview_card_entries(
        {
            "winrate": 0.51,
            "avg_kda": "2.8",
            "avg_ccpm": "2.1",
            "avg_damage_share": 0.08,
            "avg_deaths": 4.8,
            "avg_vspm": 1.9,
            "avg_duration": 29,
        },
        role="UTILITY",
    )
    headline = [card for card in cards if card.get("tier") == "headline"]
    assert headline[2]["label"] == "CC/min"
    assert headline[2]["value"] == "2.1"
    labels = {card["label"] for card in cards}
    assert "DPM" not in labels
    assert "CS/min" not in labels
    assert "Damage share" not in labels


def test_form_row_display_gd10_negative_baseline_shows_positive_improvement() -> None:
    """GD@10 from -58 to +90 should read as +148 improvement in green."""
    row = form_row_display(
        {
            "metric": "gd10",
            "label": "Gold diff @10",
            "recent": 90.0,
            "baseline": -58.0,
            "delta": 148.0,
            "delta_pct": -254.8,
            "direction": "higher",
            "verdict": "improved",
            "significant": True,
        }
    )
    assert row["gap"] == "+148"
    assert row["verdict"] == "improved"
    assert row["gap_color"] != LOSS_HEX
    assert row["gap_color"] == interpolate_metric_color(148 / 300)


def test_form_row_display_deaths_improved_shows_negative_gap_in_green() -> None:
    """Fewer deaths should show a negative percent change with green coloring."""
    row = form_row_display(
        {
            "metric": "deaths",
            "label": "Deaths/game",
            "recent": 4.0,
            "baseline": 5.0,
            "delta": -1.0,
            "delta_pct": -20.0,
            "direction": "lower",
            "verdict": "improved",
            "significant": True,
        }
    )
    assert row["gap"] == "-20%"
    assert row["gap_color"] != LOSS_HEX
    assert row["gap_color"] == interpolate_metric_color(1 / 2.5)


def test_form_row_display_death_rate_improved_shows_negative_percent() -> None:
    """Lower greed death rate should show negative % vs baseline."""
    row = form_row_display(
        {
            "metric": "greed_death_rate",
            "label": "Greed death rate",
            "recent": 0.20,
            "baseline": 0.30,
            "delta": -0.10,
            "delta_pct": -33.3,
            "direction": "lower",
            "verdict": "improved",
            "significant": True,
        }
    )
    assert row["gap"] == "-33%"
    assert row["gap_color"] != LOSS_HEX


def test_form_sample_subtitle_describes_exclusive_windows() -> None:
    text = form_sample_subtitle(recent_games=20, baseline_games=80)
    assert text == "Statistics from your last 20 games compared to the 80 games before that."


def test_form_sample_subtitle_singular_game() -> None:
    text = form_sample_subtitle(recent_games=1, baseline_games=1)
    assert text == "Statistics from your last 1 game compared to the 1 game before that."


def test_form_delta_chart_value_uses_percent_or_raw_lane_diff() -> None:
    gd_row = {"metric": "gd10", "delta": 148.0, "direction": "higher"}
    deaths_row = {
        "metric": "deaths",
        "delta": -1.0,
        "direction": "lower",
        "baseline": 5.0,
        "delta_pct": -20.0,
    }
    assert form_delta_chart_value(gd_row) == 148.0
    assert form_delta_chart_value(deaths_row) == -20.0


def test_form_impact_calibration_weights_win_rate_above_small_death_shifts() -> None:
    win_score = score_form_delta("win", 0.15)
    death_regression = score_form_delta("deaths", -0.44)
    assert win_score == 1.0
    assert death_regression == -0.44 / 2.5
    assert win_score > abs(death_regression)


def test_form_row_display_inline_verdict_uses_neutral_gap_color() -> None:
    """Small DPM drift within the inline band should not flash red."""
    row = form_row_display(
        {
            "metric": "dpm",
            "label": "DPM",
            "recent": 1015.89,
            "baseline": 1056.33,
            "delta": -40.43,
            "delta_pct": -3.8,
            "direction": "higher",
            "verdict": "inline",
            "significant": False,
        }
    )
    assert row["verdict"] == "inline"
    assert row["gap"] == "-4%"
    assert row["gap_color"] == ""


def test_cards_from_specs_attaches_peer_metric_keys() -> None:
    cards = cards_from_specs(
        (
            MetricSpec("Win rate", "overview", "winrate_pct"),
            MetricSpec("KDA", "overview", "avg_kda"),
            MetricSpec("Game length", "overview", "avg_duration", suffix=" min"),
        ),
        {"overview": {"winrate": 0.55, "avg_kda": 3.2, "avg_duration": 28}},
        section="overview",
        role="MIDDLE",
    )
    by_label = {card["label"]: card for card in cards}
    assert by_label["Win rate"]["metric"] == "win"
    assert by_label["KDA"]["metric"] == "kda"
    assert "metric" not in by_label["Game length"]


def test_peer_row_display_includes_metric_key() -> None:
    from league_stats_runner.pipeline.view_models import peer_row_display

    row = peer_row_display(
        {
            "metric": "kda",
            "label": "KDA",
            "yours": 3.0,
            "peer_avg": 2.5,
            "delta_pct": 20.0,
            "direction": "higher",
            "verdict": "above",
        }
    )
    assert row["metric"] == "kda"


def test_attach_peer_benchmarks_only_on_headline_non_inline_cards() -> None:
    from league_stats_runner.pipeline.bundles import _attach_peer_benchmarks

    cards = [
        {"label": "KDA", "value": "3.0", "tier": "headline", "metric": "kda"},
        {"label": "CS/min", "value": "7.5", "tier": "more", "metric": "cspm"},
        {"label": "Vision/min", "value": "1.0", "tier": "headline", "metric": "vspm"},
    ]
    _attach_peer_benchmarks(
        [cards],
        {
            "tier": "GOLD",
            "rows": [
                {
                    "metric": "kda",
                    "gap": "+12%",
                    "verdict": "above",
                    "gap_color": "#00ff00",
                },
                {
                    "metric": "cspm",
                    "gap": "+5%",
                    "verdict": "above",
                    "gap_color": "#00ff00",
                },
                {
                    "metric": "vspm",
                    "gap": "+2%",
                    "verdict": "inline",
                    "gap_color": "",
                },
            ],
        },
    )
    assert cards[0]["benchmark"] == "+12% vs Gold peers"
    assert cards[0]["benchmark_tone"] == "above"
    assert "benchmark" not in cards[1]
    assert "benchmark" not in cards[2]
