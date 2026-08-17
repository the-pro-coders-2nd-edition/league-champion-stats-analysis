"""Tests for the group-report account filter helpers."""

from __future__ import annotations

from league_stats.core.models import MatchRecord
from league_stats.ingest.parser import ItemCatalog, MatchParser
from league_stats.pipeline.bundles import filter_records_by_accounts
from league_stats.pipeline.orchestrator import account_subset_keys, account_view_key
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_player_match, make_timeline

ALT_PUUID = "alt-puuid-22222222-2222-2222-2222-222222222222"


def _record(match_id: str, account: str, puuid: str = MY_PUUID) -> MatchRecord:
    parser = MatchParser(ItemCatalog(FAKE_ITEMS))
    record = parser.parse(
        make_player_match(match_id, champion="Viktor", position="MIDDLE", puuid=puuid),
        make_timeline(),
        puuid,
    )
    return record.model_copy(update={"account": account})


def test_filter_records_by_accounts_slices_case_insensitively() -> None:
    """Records are matched on their account label ignoring case."""
    records = [
        _record("EUW1_1", "Alice#EUW"),
        _record("EUW1_2", "Bob#NA1", puuid=ALT_PUUID),
        _record("EUW1_3", "alice#euw"),
    ]
    only_alice = filter_records_by_accounts(records, {"Alice#EUW"})
    assert [record.match_id for record in only_alice] == ["EUW1_1", "EUW1_3"]
    both = filter_records_by_accounts(records, {"Alice#EUW", "Bob#NA1"})
    assert len(both) == 3


def test_filter_records_by_accounts_no_filter_returns_all() -> None:
    """``None`` or an empty set means no account filtering."""
    records = [_record("EUW1_1", "Alice#EUW")]
    assert filter_records_by_accounts(records, None) == records
    assert filter_records_by_accounts(records, set()) == records


def test_account_subset_keys_small_group_all_combinations() -> None:
    """Groups within the limit get every proper subset precomputed."""
    subsets = account_subset_keys(["B#1", "A#1", "C#1"])
    assert ("A#1",) in subsets
    assert ("A#1", "B#1") in subsets
    # 3 singletons + 3 pairs; the full set is the main report ("all").
    assert len(subsets) == 6
    assert ("A#1", "B#1", "C#1") not in subsets


def test_account_subset_keys_large_group_singletons_only() -> None:
    """Groups above the limit only get one view per single account."""
    labels = [f"P{index}#EUW" for index in range(5)]
    subsets = account_subset_keys(labels)
    assert subsets == [(label,) for label in sorted(labels)]


def test_account_view_key_is_sorted_join() -> None:
    assert account_view_key(["Bob#NA1", "Alice#EUW"]) == "Alice#EUW|Bob#NA1"
    assert account_view_key(("Solo#1",)) == "Solo#1"
