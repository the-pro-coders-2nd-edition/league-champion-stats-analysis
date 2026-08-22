"""Peer percentile support behind Career mode rung targets."""

from __future__ import annotations

from typing import Any

from league_stats_peers.analysis.peer.baseline import PeerBaseline
from league_stats_peers.analysis.peer.cache import aggregate_peer_metrics, peer_metric_quantiles
from league_stats_common.core.models import MetricComparison


def _rows(values: list[float]) -> list[dict[str, Any]]:
    return [
        {"puuid": f"p{i}", "match_id": f"m{i}", "metrics": {"cspm": value, "win": 1.0}}
        for i, value in enumerate(values)
    ]


def test_peer_metric_quantiles_median_and_p75() -> None:
    rows = _rows([4.0, 6.0, 8.0, 10.0, 12.0])
    assert peer_metric_quantiles(rows, 0.5)["cspm"] == 8.0
    assert peer_metric_quantiles(rows, 0.75)["cspm"] == 10.0


def test_peer_metric_quantiles_empty_rows() -> None:
    assert peer_metric_quantiles([], 0.75) == {}


def test_aggregate_peer_metrics_still_returns_means() -> None:
    metrics = aggregate_peer_metrics(_rows([4.0, 6.0, 8.0]))
    assert metrics["cspm"] == 6.0


def test_peer_baseline_defaults_to_empty_percentiles() -> None:
    baseline = PeerBaseline(
        metrics={"cspm": 6.0},
        games=50,
        players=20,
        source="test",
        confidence="medium",
        fallback_level=0,
    )
    assert baseline.metrics_p50 == {}
    assert baseline.metrics_p75 == {}


def test_metric_comparison_percentiles_default_to_none() -> None:
    row = MetricComparison(
        metric="cspm",
        label="CS/min",
        yours=6.0,
        peer_avg=6.5,
        delta=-0.5,
        delta_pct=-7.7,
        direction="higher",
        verdict="below",
    )
    assert row.peer_p50 is None
    assert row.peer_p75 is None


def test_window_slicing_preserves_peer_percentiles() -> None:
    import pandas as pd

    from league_stats_peers.analysis.peer import build_comparisons, peer_comparison_for_window
    from league_stats_common.core.models import PeerComparisonResult

    base = PeerComparisonResult(
        rank_label="GOLD II",
        tier="GOLD",
        source="test",
        peer_games=0,
        peer_players=0,
        comparisons=build_comparisons(
            {"cspm": 6.0, "deaths": 5.0},
            {"cspm": 6.5, "deaths": 5.2},
            peer_p50={"cspm": 6.4},
            peer_p75={"cspm": 7.5},
        ),
    )
    sliced = peer_comparison_for_window(
        base, pd.DataFrame([{"cspm": 6.2, "deaths": 4.0, "win": 1}]), []
    )
    by_metric = {row.metric: row for row in sliced.comparisons}
    assert by_metric["cspm"].peer_p75 == 7.5
    assert by_metric["cspm"].peer_p50 == 6.4
