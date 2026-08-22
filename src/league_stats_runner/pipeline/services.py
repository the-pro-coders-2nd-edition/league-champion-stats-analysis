"""Composition root and wired application services."""

from __future__ import annotations

from dataclasses import dataclass, field

from league_stats_common.core.config import AppConfig
from league_stats_common.core.progress import ProgressReporter
from league_stats_common.infra.cache import HttpCache
from league_stats_common.infra.ddragon_assets import DDragonAssets
from league_stats_common.infra.riot_api import RiotApiClient
from league_stats_runner.infra.raw_match_store import RawMatchStore


@dataclass
class Services:
    """Wired application services (composition root for DI)."""

    config: AppConfig
    http_cache: HttpCache
    store: RawMatchStore
    client: RiotApiClient
    assets: DDragonAssets
    progress: ProgressReporter = field(default_factory=ProgressReporter)


@dataclass
class PlayerContext:
    """Resolved player identity and PUUID."""

    riot_id: str
    tagline: str
    puuid: str
    profile_icon_id: int | None = None
    solo_tier: str | None = None
    solo_rank: str | None = None
    solo_lp: int | None = None
    flex_tier: str | None = None
    flex_rank: str | None = None
    flex_lp: int | None = None

    @property
    def label(self) -> str:
        return f"{self.riot_id}#{self.tagline}"

    def _queue_payload(self, queue: str, tier: str | None, rank: str | None, lp: int | None) -> dict[str, str | int]:
        payload: dict[str, str | int] = {}
        if tier:
            payload[f"{queue}_tier"] = tier
            if rank:
                payload[f"{queue}_rank"] = rank
            if lp is not None:
                payload[f"{queue}_lp"] = lp
        return payload

    def as_player_dict(self) -> dict[str, str | int]:
        """Identity dict suitable for job/meta persistence (optional icon/rank)."""
        payload: dict[str, str | int] = {
            "riot_id": self.riot_id,
            "tagline": self.tagline,
        }
        if self.profile_icon_id is not None:
            payload["profile_icon_id"] = self.profile_icon_id
        payload.update(
            self._queue_payload("solo", self.solo_tier, self.solo_rank, self.solo_lp)
        )
        payload.update(
            self._queue_payload("flex", self.flex_tier, self.flex_rank, self.flex_lp)
        )
        return payload


