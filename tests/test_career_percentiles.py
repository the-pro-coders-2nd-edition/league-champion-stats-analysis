"""Peer percentile support behind Career mode rung targets."""

from __future__ import annotations

from typing import Any

from league_stats.analysis.peer.baseline import PeerBaseline
from league_stats.analysis.peer.cache import aggregate_peer_metrics, peer_metric_quantiles
from league_stats.core.models import MetricComparison


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
