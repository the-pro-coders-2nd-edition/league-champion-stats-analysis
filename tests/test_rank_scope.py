"""Tests for `rank_scope.py`'s division-level (±N division) rank matching.

RFC "PEERS priority scheduling, continued sampling, pre-warm, and patch
cleanup", §5: `rank_matches`/`RankScope` are tier-only today -- all 4
divisions of an in-scope tier are treated identically. This adds an
additive, opt-in `division_radius` -- `None` (every existing call site)
must reproduce today's tier-only behavior exactly.
"""

from __future__ import annotations

from league_stats_peers.analysis.peer.rank_scope import (
    build_division_scope,
    build_widened_scope,
    division_ordinal,
    rank_matches,
)
from league_stats_common.core.models import RankedEntry


def test_division_ordinal_orders_the_full_ladder() -> None:
    assert division_ordinal("IRON", "IV") == 0
    assert division_ordinal("IRON", "I") == 3
    assert division_ordinal("BRONZE", "IV") == 4
    assert division_ordinal("EMERALD", "III") < division_ordinal("EMERALD", "II")
    assert division_ordinal("DIAMOND", "I") < division_ordinal("MASTER", "")
    assert division_ordinal("MASTER", "") < division_ordinal("GRANDMASTER", "")
    assert division_ordinal("GRANDMASTER", "") < division_ordinal("CHALLENGER", "")


def test_division_ordinal_radius_3_from_emerald_iii() -> None:
    """Confirmed by the repo owner: +/-3 divisions from Emerald III spans
    Platinum II through Diamond IV (not Diamond I -- that would be +6)."""
    target = division_ordinal("EMERALD", "III")
    assert division_ordinal("PLATINUM", "II") == target - 3
    assert division_ordinal("DIAMOND", "IV") == target + 3


def test_rank_matches_with_division_radius_excludes_out_of_window_peers() -> None:
    ranked = RankedEntry(tier="EMERALD", rank="III", league_points=0, wins=0, losses=0)
    scope = build_division_scope(ranked)
    target = division_ordinal("EMERALD", "III")

    # Compute expected inclusion directly from division_ordinal rather than
    # hand-picking divisions, to avoid an off-by-one mistake in the test itself.
    assert division_ordinal("PLATINUM", "II") - target == -3
    assert rank_matches("PLATINUM", "II", scope) is True  # exactly at -3, inside

    assert division_ordinal("DIAMOND", "III") - target == 4
    assert rank_matches("DIAMOND", "III", scope) is False  # +4, outside

    assert division_ordinal("DIAMOND", "IV") - target == 3
    assert rank_matches("DIAMOND", "IV", scope) is True  # exactly at +3, inside


def test_rank_matches_without_division_radius_is_unchanged() -> None:
    """division_radius=None (every existing call site) must reproduce
    today's tier-only behavior exactly -- no regression."""
    ranked = RankedEntry(tier="EMERALD", rank="III", league_points=0, wins=0, losses=0)
    scope = build_widened_scope(ranked)
    assert scope.division_radius is None
    assert rank_matches("EMERALD", "I", scope) is True
    assert rank_matches("PLATINUM", "IV", scope) is True  # adjacent tier, any division
    assert rank_matches("DIAMOND", "I", scope) is True  # adjacent tier, any division
    assert rank_matches("GOLD", "I", scope) is False  # two tiers away, out of scope
