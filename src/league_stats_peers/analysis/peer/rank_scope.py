"""Rank window helpers for peer baseline filtering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from league_stats_peers.analysis.peer.benchmarks import TIER_ORDER, adjacent_tiers
from league_stats_common.core.models import RankedEntry

MASTER_PLUS: Final[frozenset[str]] = frozenset({"MASTER", "GRANDMASTER", "CHALLENGER"})
DIVISIONS: Final[tuple[str, ...]] = ("I", "II", "III", "IV")

# RFC "PEERS priority scheduling...", §5: confirmed constant, not an open
# question -- "3 divisions above, 3 below" a target's exact (tier, division).
PEER_DIVISION_SCOPE_RADIUS: Final[int] = 3

_NON_MASTER_TIER_ORDER: Final[tuple[str, ...]] = tuple(
    tier for tier in TIER_ORDER if tier not in MASTER_PLUS
)
_DIVISION_INDEX: Final[dict[str, int]] = {"IV": 0, "III": 1, "II": 2, "I": 3}
_MASTER_PLUS_ORDER: Final[tuple[str, ...]] = ("MASTER", "GRANDMASTER", "CHALLENGER")


def division_ordinal(tier: str, division: str) -> int:
    """Ordinal position of (tier, division) on the promotion ladder.

    Division IV = 0 (lowest) through I = 3 (highest) within a tier, tiers
    ordered by `TIER_ORDER` (excluding Master+, which has no divisions).
    Master+ each get one synthetic slot, stacked directly above Diamond I --
    Master, then Grandmaster, then Challenger -- so the ladder stays uniform
    and a Diamond I (or Master) player's window can meaningfully spill
    across that boundary in either direction.
    """
    upper_tier = tier.upper()
    if upper_tier in MASTER_PLUS:
        diamond_i = (len(_NON_MASTER_TIER_ORDER) - 1) * 4 + _DIVISION_INDEX["I"]
        return diamond_i + 1 + _MASTER_PLUS_ORDER.index(upper_tier)
    tier_index = _NON_MASTER_TIER_ORDER.index(upper_tier)
    div_index = _DIVISION_INDEX[division.upper()]
    return tier_index * 4 + div_index


@dataclass(frozen=True)
class RankScope:
    """Defines which peer ranks are accepted for a baseline lookup."""

    target: RankedEntry
    widened: bool
    extra_tiers: frozenset[str] = field(default_factory=frozenset)
    # Additive, opt-in division-level ordinal-distance check (RFC §5). `None`
    # (every existing call site) preserves tier-only matching exactly --
    # never a replacement of `build_exact_scope`/`build_widened_scope`/
    # `build_wider_scope`, which stay as coarser, progressively-wider
    # fallback rungs.
    division_radius: int | None = None

    @property
    def allowed_tiers(self) -> set[str]:
        """Tier names included in this scope."""
        tiers = {self.target.tier.upper()}
        if self.widened:
            tiers |= adjacent_tiers(self.target.tier)
        tiers |= self.extra_tiers
        return tiers


def build_exact_scope(ranked: RankedEntry) -> RankScope:
    """Same tier (all divisions) as the tracked player."""
    return RankScope(target=ranked, widened=False)


def build_division_scope(ranked: RankedEntry, radius: int = PEER_DIVISION_SCOPE_RADIUS) -> RankScope:
    """Same tier plus neighbors within `radius` divisions -- tighter and more
    precise than `build_widened_scope`'s whole-tier +/-1. Used at fallback
    level 0, the tightest/most-relevant rung, where match quality matters
    most.

    `widened=True` here is a simplification: `allowed_tiers` doesn't
    otherwise matter once `division_radius` is set, since `rank_matches`
    skips straight to the ordinal check for that case -- but keeping
    `allowed_tiers`' existing tier-scope check as the first, cheap filter
    regardless means a target several tiers away is rejected before ever
    computing an ordinal. For the confirmed `radius=3` default, +/-1 whole
    tier is always wide enough to cover every in-radius division-level
    neighbor (3 divisions can cross at most one tier boundary in each
    direction) -- a larger radius could reject valid ordinal-distance peers
    via this cheap filter before reaching the ordinal check, so this
    constraint would need revisiting before ever configuring a materially
    larger radius.
    """
    return RankScope(target=ranked, widened=True, division_radius=radius)


def build_widened_scope(ranked: RankedEntry) -> RankScope:
    """Same tier plus immediately adjacent tiers (±1)."""
    return RankScope(target=ranked, widened=True)


def build_wider_scope(ranked: RankedEntry) -> RankScope:
    """Same tier plus up to two adjacent tiers in each direction (±2).

    Used as a last-resort store fallback before static JSON benchmarks.
    """
    first_ring = adjacent_tiers(ranked.tier)
    second_ring: set[str] = set()
    for t in first_ring:
        second_ring |= adjacent_tiers(t)
    extra = frozenset(second_ring - first_ring - {ranked.tier.upper()})
    return RankScope(target=ranked, widened=True, extra_tiers=extra)


def rank_matches(peer_tier: str, peer_rank: str, scope: RankScope) -> bool:
    """Return whether a peer's rank falls inside the scope."""
    tier = peer_tier.upper()
    if tier not in scope.allowed_tiers:
        return False

    if scope.division_radius is not None:
        peer_ordinal = division_ordinal(tier, peer_rank)
        target_ordinal = division_ordinal(scope.target.tier, scope.target.rank)
        return abs(peer_ordinal - target_ordinal) <= scope.division_radius

    target_tier = scope.target.tier.upper()
    if tier in MASTER_PLUS and target_tier in MASTER_PLUS:
        if scope.widened or tier in scope.extra_tiers:
            return True
        return tier == target_tier

    if tier == target_tier:
        return True

    return scope.widened or tier in scope.extra_tiers


def league_lookup_pairs(scope: RankScope) -> list[tuple[str, str]]:
    """Return (tier, division) pairs to query via league-v4.

    The player's exact tier+division is placed first so seed PUUIDs are
    rank-relevant from the start and fewer match downloads are wasted on
    out-of-scope players.
    """
    target_tier = scope.target.tier.upper()
    target_div = (scope.target.rank or "").upper()

    pairs: list[tuple[str, str]] = []

    # Player's exact division first (most relevant seeds)
    if target_tier not in MASTER_PLUS and target_div:
        pairs.append((target_tier, target_div))

    # Remaining divisions of the target tier
    for div in DIVISIONS:
        if div != target_div:
            if target_tier not in MASTER_PLUS:
                pairs.append((target_tier, div))

    # Master+ exact tier
    if target_tier in MASTER_PLUS:
        pairs.append((target_tier, ""))

    # Adjacent / extra tiers (widened and wider scope)
    other_tiers = sorted(scope.allowed_tiers - {target_tier})
    for tier in other_tiers:
        if tier in MASTER_PLUS:
            pairs.append((tier, ""))
        else:
            for div in DIVISIONS:
                pairs.append((tier, div))

    return pairs
