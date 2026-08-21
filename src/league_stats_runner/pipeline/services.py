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

    @property
    def label(self) -> str:
        return f"{self.riot_id}#{self.tagline}"

    def as_player_dict(self) -> dict[str, str | int]:
        """Identity dict suitable for job/meta persistence (optional icon/rank)."""
        payload: dict[str, str | int] = {
            "riot_id": self.riot_id,
            "tagline": self.tagline,
        }
        if self.profile_icon_id is not None:
            payload["profile_icon_id"] = self.profile_icon_id
        if self.solo_tier:
            payload["solo_tier"] = self.solo_tier
            if self.solo_rank:
                payload["solo_rank"] = self.solo_rank
            if self.solo_lp is not None:
                payload["solo_lp"] = self.solo_lp
        return payload


