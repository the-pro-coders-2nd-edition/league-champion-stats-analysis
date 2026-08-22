"""Tests for combat output metric selection."""

from __future__ import annotations

from league_stats_runner.analysis.combat import (
    build_uses_cc,
    combat_output_metric,
    prefers_cc_over_dpm,
)


def test_support_prefers_cc_over_dpm() -> None:
    assert prefers_cc_over_dpm("UTILITY")
    assert combat_output_metric("UTILITY") == ("ccpm", "CC/min")


def test_tank_damage_share_prefers_cc() -> None:
    assert prefers_cc_over_dpm("TOP", avg_damage_share=0.12)
    assert combat_output_metric("JUNGLE", avg_damage_share=0.11) == ("ccpm", "CC/min")


def test_carry_builds_keep_dpm() -> None:
    assert not prefers_cc_over_dpm("MIDDLE", avg_damage_share=0.24)
    assert combat_output_metric("TOP", avg_damage_share=0.22) == ("dpm", "DPM")


def test_low_cc_build_skips_cc_goals() -> None:
    assert not build_uses_cc(avg_ccpm=0.9)
    assert build_uses_cc(avg_ccpm=1.0)
    assert build_uses_cc(avg_ccpm=2.5)
    assert not build_uses_cc(avg_ccpm=None)
