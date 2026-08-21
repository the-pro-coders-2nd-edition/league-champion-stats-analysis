"""Career mode is one ladder over all ranked games, built once per report.

``build_career_bundle`` used to run inside ``build_window_bundle``, so it fired
once per queue/window slice -- 3 queues x 3 windows = 9 calls to
``advance_career`` against one ladder key, each with different match data. The
last slice to run owned the persisted state, and 20 flex games could retire a
goal whose rungs were derived from solo games. These tests pin the replacement:
the ladder is built once from every ranked game, and only the "all ranked" views
carry it.
"""

from __future__ import annotations

import pytest

from league_stats_runner.presentation.career import career_scope_view, empty_career_view


def test_scope_view_tells_the_reader_the_ladder_covers_all_ranked() -> None:
    view = career_scope_view()

    assert view["has_career"] is False
    assert view["tracks_all_ranked"] is True


def test_scope_view_is_otherwise_shaped_like_the_empty_view() -> None:
    """The report renders one component for both, so the keys must match."""
    scope = career_scope_view()
    empty = empty_career_view()

    assert set(scope) == set(empty) | {"tracks_all_ranked"}


def test_empty_view_does_not_claim_all_ranked_scope() -> None:
    """An 'all ranked' slice with no ladder yet must not show the switch notice."""
    assert empty_career_view().get("tracks_all_ranked") is not True


def test_career_view_declares_its_all_ranked_scope() -> None:
    """A rendered ladder says what it covers, so the tab can caption it."""
    from league_stats_runner.analysis.career.engine import CareerSnapshot
    from league_stats_runner.presentation.career import build_career_view

    assert build_career_view(CareerSnapshot()).get("tracks_all_ranked") is not True


@pytest.mark.parametrize("queue_key", ["solo", "flex", "all"])
def test_every_view_carries_the_same_ladder(queue_key: str) -> None:
    """Career is not a function of the queue filter, so it shows in every view.

    Gating it behind the all-ranked filter made it invisible in the view the report
    opens on, because DEFAULT_QUEUE_FILTER is solo.
    """
    from league_stats_runner.pipeline.bundles import career_view_for_queue

    ladder = {"has_career": True, "blocks": [{"slot": 0}], "widget": [], "rules": [],
              "legend": [], "congrats": None}
    view = career_view_for_queue(queue_key, ladder)

    assert view["has_career"] is True
    assert view["blocks"] == ladder["blocks"]


@pytest.mark.parametrize("queue_key", ["solo", "flex", "all"])
def test_every_view_is_captioned_with_the_scope_it_covers(queue_key: str) -> None:
    from league_stats_runner.pipeline.bundles import career_view_for_queue

    ladder = {"has_career": True, "blocks": [], "widget": [], "rules": [],
              "legend": [], "congrats": None}

    assert career_view_for_queue(queue_key, ladder)["tracks_all_ranked"] is True


def test_ranked_records_for_the_ladder_span_both_queues() -> None:
    """One ladder over all ranked games means solo and flex both feed it."""
    from league_stats_common.core.config import RANKED_FLEX_QUEUE_ID, RANKED_SOLO_QUEUE_ID
    from league_stats_runner.pipeline.bundles import ranked_career_records

    class _Rec:
        def __init__(self, queue_id: int, match_id: str) -> None:
            self.queue_id = queue_id
            self.match_id = match_id

    records = [
        _Rec(RANKED_SOLO_QUEUE_ID, "s1"),
        _Rec(RANKED_FLEX_QUEUE_ID, "f1"),
        _Rec(1700, "arena"),
        _Rec(RANKED_SOLO_QUEUE_ID, "s2"),
    ]

    kept = [record.match_id for record in ranked_career_records(records)]
    assert kept == ["s1", "f1", "s2"]


def test_ladder_is_advanced_once_per_report_not_once_per_slice(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 queues x 3 windows used to mean 9 advance_career calls on one ladder."""
    from league_stats_runner.pipeline import bundles as bundles_module
    from league_stats_runner.pipeline.orchestrator import build_report_views
    from tests.test_reports import _config, _make_records, _peer

    config = _config(tmp_path)
    records = _make_records()
    graphs = config.run_graphs_dir
    graphs.mkdir(parents=True, exist_ok=True)

    calls: list[int] = []
    real = bundles_module.build_career_bundle

    def counted(cfg, frames, peer, components):
        calls.append(len(frames.matches_df))
        return real(cfg, frames, peer, components)

    monkeypatch.setattr(bundles_module, "build_career_bundle", counted)
    build_report_views(config, records, graphs, peer_comparison=_peer(records))

    assert len(calls) == 1


def test_the_ladder_sees_every_ranked_game_not_a_window(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 50-game window must not decide the rungs of a 20-game habit tracker."""
    from league_stats_runner.pipeline import bundles as bundles_module
    from league_stats_runner.pipeline.orchestrator import build_report_views
    from tests.test_reports import _config, _make_records, _peer

    config = _config(tmp_path)
    records = _make_records()
    graphs = config.run_graphs_dir
    graphs.mkdir(parents=True, exist_ok=True)

    seen: list[int] = []
    real = bundles_module.build_career_bundle

    def counted(cfg, frames, peer, components):
        seen.append(len(frames.matches_df))
        return real(cfg, frames, peer, components)

    monkeypatch.setattr(bundles_module, "build_career_bundle", counted)
    build_report_views(config, records, graphs, peer_comparison=_peer(records))

    assert seen == [len(bundles_module.ranked_career_records(records))]


def test_stage_a_never_attempts_career(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage A (no peer comparison yet) must not call advance_career at all.

    It used to call it and get told "not ready" (``ctx.can_seed_blocks()``
    returning False), which is a different bug than never calling it. This
    pins the stronger guarantee: Career is not even attempted until Stage B.
    """
    from league_stats_runner.pipeline import bundles as bundles_module
    from league_stats_runner.pipeline.orchestrator import build_report_views
    from tests.test_reports import _config, _make_records

    config = _config(tmp_path)
    records = _make_records()
    graphs = config.run_graphs_dir
    graphs.mkdir(parents=True, exist_ok=True)

    calls: list[int] = []
    real = bundles_module.build_career_bundle

    def counted(cfg, frames, peer, components):
        calls.append(len(frames.matches_df))
        return real(cfg, frames, peer, components)

    monkeypatch.setattr(bundles_module, "build_career_bundle", counted)
    build_report_views(config, records, graphs, peer_comparison=None)

    assert calls == []


def test_stage_a_renders_the_awaiting_peers_loading_shape(tmp_path) -> None:
    """With Career never attempted, Stage A's field must still read as loading.

    The frontend (``CareerMode.svelte``) shows a loading skeleton for
    ``awaiting_peers`` and a "no career yet" empty message otherwise. Stage A
    has to produce the former, not the latter, while Stage B is still pending.
    """
    from league_stats_runner.pipeline.orchestrator import build_report_views
    from tests.test_reports import _config, _make_records

    config = _config(tmp_path)
    records = _make_records()
    graphs = config.run_graphs_dir
    graphs.mkdir(parents=True, exist_ok=True)

    views, _, _ = build_report_views(config, records, graphs, peer_comparison=None)

    for queue_key in ("solo", "flex", "all"):
        for window in views[queue_key]["windows"].values():
            career = window["career"]
            assert career["has_career"] is False
            assert career["awaiting_peers"] is True


def test_the_ladder_reaches_every_queue_view(tmp_path) -> None:
    from league_stats_runner.pipeline.orchestrator import build_report_views
    from tests.test_reports import _config, _make_records, _peer

    config = _config(tmp_path)
    records = _make_records()
    graphs = config.run_graphs_dir
    graphs.mkdir(parents=True, exist_ok=True)

    views, _, _ = build_report_views(
        config, records, graphs, peer_comparison=_peer(records)
    )

    for queue_key in ("solo", "flex", "all"):
        for window in views[queue_key]["windows"].values():
            career = window["career"]
            assert career["tracks_all_ranked"] is True
            assert career["has_career"] is True
