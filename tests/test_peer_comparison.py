"""Tests for rank-peer comparison and tier benchmarks."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from league_stats_peers.analysis.peer.benchmarks import adjacent_tiers, resolve_benchmark_path, tier_benchmark
from league_stats_peers.analysis.peer.comparison import (
    build_comparisons,
    compare_metrics_for_role,
    peer_recommendations,
)
from league_stats_peers.analysis.peer.comparison import _extract_champion_role_from_match
from league_stats_common.core.models import RankedEntry
from tests.fixtures import MY_PUUID, make_match


@pytest.fixture
def benchmark_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a temporary Viktor mid benchmark file for static loader tests."""
    import league_stats_peers.analysis.peer.benchmarks as benchmarks

    directory = tmp_path / "benchmarks"
    directory.mkdir()
    payload = {
        "GOLD": {
            "winrate": 0.5,
            "kda": 2.5,
            "dpm": 650.0,
            "cspm": 7.2,
            "deaths": 5.0,
            "vspm": 1.0,
            "control_wards": 2.0,
            "kill_participation": 0.55,
            "damage_share": 0.24,
        },
        "EMERALD": {
            "winrate": 0.5,
            "kda": 2.7,
            "dpm": 680.0,
            "cspm": 7.4,
            "deaths": 4.8,
            "vspm": 1.1,
            "control_wards": 2.2,
            "kill_participation": 0.57,
            "damage_share": 0.25,
        },
    }
    (directory / "viktor_middle.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(benchmarks, "BENCHMARKS_DIR", directory)
    return directory


def test_tier_benchmark_maps_winrate(benchmark_dir: Path) -> None:
    """JSON ``winrate`` is exposed as ``win`` for comparisons."""
    emerald = tier_benchmark("EMERALD", "Viktor", "MIDDLE")
    assert emerald["win"] == 0.5
    assert emerald["winrate"] == 0.5


def test_tier_benchmark_returns_gold_defaults(benchmark_dir: Path) -> None:
    """Unknown tiers fall back to GOLD benchmarks."""
    gold = tier_benchmark("GOLD", "Viktor", "MIDDLE")
    unknown = tier_benchmark("NOT_A_TIER", "Viktor", "MIDDLE")
    assert gold["dpm"] == unknown["dpm"]
    assert gold["cspm"] > 6.0


def test_benchmark_path_prefers_champion_specific(benchmark_dir: Path) -> None:
    """Champion-specific benchmarks are preferred over role fallback."""
    path = resolve_benchmark_path("Viktor", "MIDDLE")
    assert path.name == "viktor_middle.json"


def test_adjacent_tiers_includes_neighbours() -> None:
    """PLATINUM neighbours are GOLD and EMERALD."""
    neighbours = adjacent_tiers("PLATINUM")
    assert neighbours == {"PLATINUM", "GOLD", "EMERALD"}


def test_infer_platform_from_match_id() -> None:
    """Match id prefixes map to platform routing hosts."""
    from league_stats_common.infra.riot_api import RiotApiClient

    assert RiotApiClient.infer_platform_from_match_id("EUW1_12345") == "euw1"
    assert RiotApiClient.infer_platform_from_match_id("EUN1_999") == "eun1"
    assert RiotApiClient.infer_platform_from_match_id("UNKNOWN_1") is None


def test_extract_champion_role_excludes_player() -> None:
    """The tracked player is not included in peer rows."""
    match = make_match()
    rows = _extract_champion_role_from_match(match, MY_PUUID, "Viktor", "MIDDLE")
    assert rows == []


def test_extract_champion_role_finds_enemy() -> None:
    """A matching opponent is extracted with combat stats."""
    match = make_match()
    match["info"]["participants"][5]["championName"] = "Viktor"
    match["info"]["participants"][5]["teamPosition"] = "MIDDLE"
    rows = _extract_champion_role_from_match(match, MY_PUUID, "Viktor", "MIDDLE")
    assert len(rows) == 1
    assert rows[0]["puuid"] != MY_PUUID
    assert rows[0]["dpm"] > 0


def test_extract_champion_role_filters_lane() -> None:
    """Only the configured lane is included."""
    match = make_match()
    match["info"]["participants"][5]["championName"] = "Viktor"
    match["info"]["participants"][5]["teamPosition"] = "TOP"
    rows = _extract_champion_role_from_match(match, MY_PUUID, "Viktor", "MIDDLE")
    assert rows == []


def test_comparison_summary_handles_none_delta_pct() -> None:
    """Summary lines work when peer average is zero (no % gap)."""
    from league_stats_peers.analysis.peer.comparison import _comparison_summary_line
    from league_stats_common.core.models import MetricComparison

    comp = MetricComparison(
        metric="gd10",
        label="Gold diff @10",
        yours=120.0,
        peer_avg=0.0,
        delta=120.0,
        delta_pct=None,
        direction="higher",
        verdict="above",
    )
    line = _comparison_summary_line(comp)
    assert "120.0" in line
    assert "%" not in line


def test_compare_metrics_swaps_dpm_for_support() -> None:
    metrics = compare_metrics_for_role("UTILITY")
    keys = [m[0] for m in metrics]
    assert "ccpm" in keys
    assert "dpm" not in keys


def test_build_comparisons_verdicts() -> None:
    """Higher-is-better metrics classify gaps correctly."""
    user = {"kda": 3.0, "deaths": 4.0, "dpm": 800.0, "win": 0.6}
    peer = {"kda": 2.4, "deaths": 5.4, "dpm": 640.0, "win": 0.5}
    comparisons = build_comparisons(user, peer)
    by_key = {c.metric: c for c in comparisons}
    assert by_key["kda"].verdict == "above"
    assert by_key["deaths"].verdict == "above"  # fewer deaths is better
    assert by_key["dpm"].verdict == "above"


def test_peer_recommendations_flag_weaknesses() -> None:
    """Large negative gaps produce rank-peer coaching tips."""
    user = {"deaths": 7.0, "cspm": 5.5, "vspm": 0.7, "dpm": 500.0}
    peer = {"deaths": 5.0, "cspm": 7.0, "vspm": 1.2, "dpm": 680.0}
    comparisons = build_comparisons(user, peer)
    recs = peer_recommendations(
        comparisons, "Gold II", peer_games=20, build_label="Viktor mid"
    )
    titles = " | ".join(r.title for r in recs)
    assert "deaths run higher" in titles.lower() or "farming trails" in titles.lower()
    assert all(r.action for r in recs)


def test_comparisons_dataframe_from_result() -> None:
    """Comparison export is one row per metric."""
    from league_stats_peers.analysis.peer.comparison import comparisons_dataframe
    from league_stats_common.core.models import MetricComparison, PeerComparisonResult

    result = PeerComparisonResult(
        rank_label="Gold II",
        tier="GOLD",
        source="test",
        peer_games=0,
        peer_players=0,
        comparisons=[
            MetricComparison(
                metric="kda",
                label="KDA",
                yours=3.0,
                peer_avg=2.4,
                delta=0.6,
                delta_pct=25.0,
                direction="higher",
                verdict="above",
            )
        ],
    )
    frame = comparisons_dataframe(result)
    assert len(frame) == 1
    assert frame.iloc[0]["metric"] == "kda"


# ------------------------------------------ build_peer_comparison / finish_peer_comparison
#
# Phase 3 Task 3 fix round 1 extracted `finish_peer_comparison` out of
# `build_peer_comparison` (the post-baseline finalisation step) so RUNNER's
# `peers_mode="grpc"` path (`league_stats.web.worker._build_peer_for_pool_via_grpc`)
# could call the same function instead of duplicating its logic. There was no
# direct test of `build_peer_comparison`'s own orchestration before this --
# only indirect coverage via full pipeline/end-to-end tests -- so these close
# that gap and prove the extraction didn't change `build_peer_comparison`'s
# behavior.


class _NoHistoryStore:
    """Minimal `store` stand-in: `finish_peer_comparison` only needs
    `iter_match_ids` (via `collect_user_history_peers`) for this test's
    purposes -- an empty history is a normal, common case."""

    def iter_match_ids(self, puuid: str):
        return iter(())


def _sample_baseline():
    from league_stats_peers.analysis.peer.baseline import PeerBaseline

    return PeerBaseline(
        metrics={"win": 0.55, "kda": 3.2, "dpm": 620.0, "deaths": 4.5, "cspm": 7.0},
        games=80,
        players=15,
        source="peer store",
        confidence="high",
        fallback_level=0,
    )


def _sample_matches_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"win": 1, "kda": 4.0, "dpm": 700.0, "deaths": 3, "cspm": 7.5},
            {"win": 0, "kda": 2.5, "dpm": 550.0, "deaths": 5, "cspm": 6.5},
        ]
    )


def test_build_peer_comparison_matches_finish_peer_comparison_for_the_same_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`build_peer_comparison` (resolves its own baseline via
    `resolve_peer_baseline`) must produce the exact same result
    `finish_peer_comparison` (given that same baseline directly) does --
    proving the fix-round-1 extraction changed nothing about the in-process
    path's behavior."""
    import league_stats_peers.analysis.peer.comparison as comparison_module
    from league_stats_peers.analysis.peer.comparison import build_peer_comparison, finish_peer_comparison

    baseline = _sample_baseline()
    monkeypatch.setattr(comparison_module, "resolve_peer_baseline", lambda *a, **k: baseline)
    matches_df = _sample_matches_df()
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)
    store = _NoHistoryStore()

    via_build = build_peer_comparison(
        client=object(),
        store=store,
        matches_df=matches_df,
        records=[],
        user_puuid=MY_PUUID,
        ranked=ranked,
        champion="Ahri",
        role="MIDDLE",
    )
    via_finish = finish_peer_comparison(
        baseline,
        matches_df=matches_df,
        records=[],
        store=store,
        user_puuid=MY_PUUID,
        ranked=ranked,
        champion="Ahri",
        role="MIDDLE",
    )

    assert via_build is not None
    assert via_build.model_dump() == via_finish.model_dump()
    assert via_build.peer_games == 80
    assert via_build.comparisons, "expected real comparisons to have been computed"


def test_finish_peer_comparison_reads_user_history_via_raw_match_store() -> None:
    """Direct proof that `finish_peer_comparison`'s user-history scan
    (`collect_user_history_peers`, which calls `store.iter_match_ids` then
    `store.load_match`) works against a real `RawMatchStore` (mongomock-
    backed), not just a hand-rolled stub like `_NoHistoryStore` above.

    This is the third call site Phase 5 Task 1's investigation flagged as
    needing `RawMatchStore.iter_match_ids` (alongside stage A's
    `discover_build_pools`/`load_all_records`): RUNNER's `peers_mode="grpc"`
    path (`web/worker.py`'s `_build_peer_for_pool_via_grpc`) calls
    `finish_peer_comparison(..., store=services.store, ...)`, and
    `services.store` is always a `RawMatchStore`. The end-to-end test in
    `test_runner_service.py`
    (`test_enqueue_job_and_stream_progress_uses_raw_match_store_in_mongo_mode`)
    short-circuits on `ranked is None` before ever reaching this code, so it
    doesn't cover this path -- this test closes that gap directly and
    cheaply, without needing a full ranked-resolution end-to-end run.
    """
    import mongomock

    from league_stats_peers.analysis.peer.comparison import finish_peer_comparison
    from league_stats_runner.infra.raw_match_store import RawMatchStore

    match = make_match()
    match_id = match["metadata"]["matchId"]
    # Mirror matchup: the red-side mid laner (a different puuid, not excluded)
    # also plays Viktor mid -- scanning the tracked player's own match history
    # for "other Viktor mid games" must find this participant.
    match["info"]["participants"][5]["championName"] = "Viktor"
    match["info"]["participants"][5]["teamPosition"] = "MIDDLE"
    opponent_puuid = match["info"]["participants"][5]["puuid"]

    mongo_client = mongomock.MongoClient()
    store = RawMatchStore(mongo_client, db_name="league_stats_finish_peer_test")
    store.save_match(match_id, MY_PUUID, match)
    store.save_timeline(match_id, {"info": {"frames": []}})

    baseline = _sample_baseline()
    matches_df = _sample_matches_df()
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

    result = finish_peer_comparison(
        baseline,
        matches_df=matches_df,
        records=[],
        store=store,
        user_puuid=MY_PUUID,
        ranked=ranked,
        champion="Viktor",
        role="MIDDLE",
    )

    assert result is not None
    assert opponent_puuid != MY_PUUID
    assert "1 other Viktor mid" in result.source, result.source


def test_build_peer_comparison_returns_none_when_unranked() -> None:
    """No rank resolved -> no baseline lookup is even attempted."""
    from league_stats_peers.analysis.peer.comparison import build_peer_comparison

    result = build_peer_comparison(
        client=object(),
        store=_NoHistoryStore(),
        matches_df=_sample_matches_df(),
        records=[],
        user_puuid=MY_PUUID,
        ranked=None,
        champion="Ahri",
        role="MIDDLE",
    )
    assert result is None


def test_build_peer_comparison_returns_none_when_no_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """`resolve_peer_baseline` returning `None` (e.g. every fallback level
    exhausted) must be a soft `None`, not an exception."""
    import league_stats_peers.analysis.peer.comparison as comparison_module
    from league_stats_peers.analysis.peer.comparison import build_peer_comparison

    monkeypatch.setattr(comparison_module, "resolve_peer_baseline", lambda *a, **k: None)
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

    result = build_peer_comparison(
        client=object(),
        store=_NoHistoryStore(),
        matches_df=_sample_matches_df(),
        records=[],
        user_puuid=MY_PUUID,
        ranked=ranked,
        champion="Ahri",
        role="MIDDLE",
    )
    assert result is None
