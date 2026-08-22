"""Synthetic Riot Match-V5 documents for unit tests.

Builds a minimal but structurally faithful 20-minute game where the tracked
player (participant 1, blue side) plays Viktor mid against Syndra.
"""

from __future__ import annotations

from typing import Any, Iterator

import mongomock

from league_stats_peers.infra.peer_sample_store import PeerSampleStore
from league_stats_runner.infra.raw_match_store import RawMatchStore

MY_PUUID = "puuid-viktor"
MATCH_ID = "EUW1_9999"

CHAMPS_BLUE = ["Viktor", "LeeSin", "Jinx", "Thresh", "Ornn"]
CHAMPS_RED = ["Syndra", "Vi", "Ezreal", "Leona", "Renekton"]
POSITIONS = ["MIDDLE", "JUNGLE", "BOTTOM", "UTILITY", "TOP"]

FAKE_ITEMS: dict[int, dict[str, Any]] = {
    1056: {"name": "Doran's Ring", "gold": {"total": 400}, "tags": [], "into": ["3802"]},
    3802: {"name": "Lost Chapter", "gold": {"total": 1100}, "tags": [], "into": ["6655"]},
    6655: {"name": "Luden's Companion", "gold": {"total": 2900}, "tags": [], "into": []},
    4645: {"name": "Shadowflame", "gold": {"total": 3200}, "tags": [], "into": []},
    3157: {"name": "Zhonya's Hourglass", "gold": {"total": 3250}, "tags": [], "into": []},
    3020: {"name": "Sorcerer's Shoes", "gold": {"total": 1100}, "tags": ["Boots"], "into": []},
    1001: {"name": "Boots", "gold": {"total": 300}, "tags": ["Boots"], "into": ["3020"]},
    2138: {"name": "Elixir of Iron", "gold": {"total": 500}, "tags": [], "into": []},
    3363: {"name": "Farsight Alteration", "gold": {"total": 0}, "tags": ["Trinket"], "into": []},
}


def make_participant(pid: int, team_id: int, champion: str, position: str) -> dict[str, Any]:
    """Build a participant entry for the synthetic match document.

    Args:
        pid: Participant id (1-10).
        team_id: 100 (blue) or 200 (red).
        champion: Champion name.
        position: Team position (MIDDLE, JUNGLE...).

    Returns:
        A participant dict with every field the parser reads.
    """
    is_me = pid == 1
    return {
        "participantId": pid,
        "puuid": MY_PUUID if is_me else f"puuid-{pid}",
        "riotIdGameName": "Test" if is_me else f"Player{pid}",
        "riotIdTagline": "EUW" if is_me else "NA1",
        "teamId": team_id,
        "championName": champion,
        "teamPosition": position,
        "win": team_id == 100,
        "kills": 7 if is_me else 3,
        "deaths": 2 if is_me else 4,
        "assists": 5 if is_me else 6,
        "goldEarned": 12000 if is_me else 9000,
        "totalMinionsKilled": 180 if is_me else 120,
        "neutralMinionsKilled": 8 if is_me else 4,
        "champExperience": 14000,
        "champLevel": 13,
        "totalDamageDealtToChampions": 24000 if is_me else 12000,
        "totalDamageTaken": 18000 if is_me else 14000,
        "trueDamageDealtToChampions": 1000,
        "physicalDamageDealtToChampions": 3000,
        "magicDamageDealtToChampions": 20000 if is_me else 8000,
        "totalHeal": 800,
        "totalHealsOnTeammates": 0,
        "totalDamageShieldedOnTeammates": 0,
        "timeCCingOthers": 25,
        "largestKillingSpree": 4 if is_me else 2,
        "doubleKills": 1 if is_me else 0,
        "tripleKills": 0,
        "quadraKills": 0,
        "pentaKills": 0,
        "visionScore": 28 if is_me else 18,
        "wardsPlaced": 11,
        "wardsKilled": 4,
        "visionWardsBoughtInGame": 3 if is_me else 1,
        "totalTimeSpentDead": 45 if is_me else 90,
        "gameEndedInEarlySurrender": False,
        "summoner1Id": 4,
        "summoner2Id": 12,
        "item0": 6655, "item1": 3157, "item2": 3020, "item3": 0, "item4": 0, "item5": 0,
        "challenges": {"killParticipation": 0.55},
        "perks": {
            "statPerks": {"offense": 5008, "flex": 5008, "defense": 5011},
            "styles": [
                {
                    "style": 8200,
                    "selections": [
                        {"perk": 8229}, {"perk": 8226}, {"perk": 8210}, {"perk": 8237},
                    ],
                },
                {"style": 8300, "selections": [{"perk": 8345}, {"perk": 8347}]},
            ],
        },
    }


def make_match(
    duration_s: int = 1200,
    queue_id: int = 420,
    *,
    game_ended_in_surrender: bool = False,
    game_ended_in_early_surrender: bool = False,
) -> dict[str, Any]:
    """Build the synthetic match document.

    Args:
        duration_s: Game duration in seconds.
        queue_id: Queue id (420 = ranked solo).

    Returns:
        A match-v5-shaped dict.
    """
    participants = [
        make_participant(i + 1, 100, CHAMPS_BLUE[i], POSITIONS[i]) for i in range(5)
    ] + [
        make_participant(i + 6, 200, CHAMPS_RED[i], POSITIONS[i]) for i in range(5)
    ]
    match = {
        "metadata": {"matchId": MATCH_ID},
        "info": {
            "queueId": queue_id,
            "gameDuration": duration_s,
            "gameCreation": 1_700_000_000_000,
            "gameVersion": "14.23.456.789",
            "participants": participants,
        },
    }
    for participant in participants:
        participant["gameEndedInSurrender"] = game_ended_in_surrender
        participant["gameEndedInEarlySurrender"] = game_ended_in_early_surrender
    return match


def make_player_match(
    match_id: str,
    *,
    champion: str = "Viktor",
    position: str = "MIDDLE",
    puuid: str = MY_PUUID,
    duration_s: int = 1200,
    queue_id: int = 420,
) -> dict[str, Any]:
    """Build a synthetic match with the tracked player on a specific build."""
    match = make_match(duration_s=duration_s, queue_id=queue_id)
    match["metadata"]["matchId"] = match_id
    match["info"]["participants"][0]["championName"] = champion
    match["info"]["participants"][0]["teamPosition"] = position
    match["info"]["participants"][0]["puuid"] = puuid
    return match


def _participant_frame(pid: int, minute: int) -> dict[str, Any]:
    """Build a participant frame for a given minute.

    The tracked player (1) and the opponent (6) sit in mid lane; the player
    farms slightly better. Other players idle near their bases.

    Args:
        pid: Participant id.
        minute: Frame minute.

    Returns:
        A participant frame dict.
    """
    if pid == 1:
        position = {"x": 7000 + minute * 30, "y": 7000 + minute * 30}
        gold, cs = 500 + minute * 420, minute * 8
    elif pid == 6:
        position = {"x": 7800, "y": 7800}
        gold, cs = 500 + minute * 360, minute * 7
    elif pid <= 5:
        position = {"x": 800, "y": 800}
        gold, cs = 500 + minute * 300, minute * 5
    else:
        position = {"x": 14000, "y": 14000}
        gold, cs = 500 + minute * 300, minute * 5
    return {
        "totalGold": gold,
        "currentGold": 300 + (minute % 5) * 100,
        "xp": minute * 550,
        "minionsKilled": cs,
        "jungleMinionsKilled": 0,
        "position": position,
    }


def make_timeline(duration_s: int = 1200) -> dict[str, Any]:
    """Build the synthetic timeline document.

    Includes purchases (boots, Lost Chapter, Luden's, Zhonya), skill-ups,
    wards, one dragon, one baron, one tower, a mid-lane teamfight and three
    deaths of the tracked player.

    Args:
        duration_s: Game duration in seconds.

    Returns:
        A timeline-v5-shaped dict.
    """
    minutes = duration_s // 60
    frames: list[dict[str, Any]] = []
    for minute in range(minutes + 1):
        frames.append(
            {
                "timestamp": minute * 60_000,
                "participantFrames": {str(pid): _participant_frame(pid, minute) for pid in range(1, 11)},
                "events": [],
            }
        )

    def ev(timestamp_ms: int, **fields: Any) -> dict[str, Any]:
        """Shorthand event constructor."""
        return {"timestamp": timestamp_ms, **fields}

    events: list[dict[str, Any]] = [
        # Starting shop + purchase timeline for player 1.
        ev(10_000, type="ITEM_PURCHASED", participantId=1, itemId=1056),
        ev(300_000, type="ITEM_PURCHASED", participantId=1, itemId=1001),
        ev(302_000, type="ITEM_PURCHASED", participantId=1, itemId=3802),
        ev(540_000, type="ITEM_PURCHASED", participantId=1, itemId=3020),
        ev(542_000, type="ITEM_PURCHASED", participantId=1, itemId=6655),
        ev(900_000, type="ITEM_PURCHASED", participantId=1, itemId=3157),
        ev(905_000, type="ITEM_PURCHASED", participantId=1, itemId=3363),
        ev(1_100_000, type="ITEM_PURCHASED", participantId=1, itemId=2138),
        # Skill levels: Q E W ... R at 11 min.
        ev(60_000, type="SKILL_LEVEL_UP", participantId=1, skillSlot=1),
        ev(120_000, type="SKILL_LEVEL_UP", participantId=1, skillSlot=3),
        ev(180_000, type="SKILL_LEVEL_UP", participantId=1, skillSlot=2),
        *[
            ev(240_000 + i * 60_000, type="SKILL_LEVEL_UP", participantId=1, skillSlot=1)
            for i in range(4)
        ],
        ev(660_000, type="SKILL_LEVEL_UP", participantId=1, skillSlot=4),
        # Wards.
        ev(200_000, type="WARD_PLACED", creatorId=1, wardType="YELLOW_TRINKET"),
        ev(400_000, type="WARD_PLACED", creatorId=1, wardType="CONTROL_WARD"),
        ev(760_000, type="WARD_PLACED", creatorId=4, wardType="CONTROL_WARD"),
        ev(770_000, type="WARD_PLACED", creatorId=1, wardType="YELLOW_TRINKET"),
        ev(500_000, type="WARD_KILL", killerId=6, wardType="CONTROL_WARD"),
        # Player kills Syndra early (with shutdown collected).
        ev(
            360_000, type="CHAMPION_KILL", killerId=1, victimId=6,
            position={"x": 7500, "y": 7500}, bounty=300, shutdownBounty=150,
            assistingParticipantIds=[],
            victimDamageReceived=[
                {"participantId": 1, "physicalDamage": 0, "magicDamage": 900, "trueDamage": 0}
            ],
        ),
        # Player dies to a mid-lane gank dive under tower at 8 min.
        ev(
            480_000, type="CHAMPION_KILL", killerId=6, victimId=1,
            position={"x": 5750, "y": 6300}, bounty=200, shutdownBounty=0,
            assistingParticipantIds=[7],
            victimDamageReceived=[
                {"participantId": 6, "physicalDamage": 0, "magicDamage": 700, "trueDamage": 0},
                {"participantId": 7, "physicalDamage": 400, "magicDamage": 0, "trueDamage": 0},
            ],
        ),
        # Player dies alone in enemy jungle 60 s before the dragon.
        ev(
            720_000, type="CHAMPION_KILL", killerId=7, victimId=1,
            position={"x": 11000, "y": 6200}, bounty=350, shutdownBounty=0,
            assistingParticipantIds=[],
            victimDamageReceived=[
                {"participantId": 7, "physicalDamage": 1200, "magicDamage": 0, "trueDamage": 100}
            ],
        ),
        ev(
            780_000, type="ELITE_MONSTER_KILL", killerTeamId=200, monsterType="DRAGON",
            monsterSubType="CHEMTECH_DRAGON", position={"x": 9866, "y": 4414},
        ),
        # Our team takes a tower (enemy building destroyed).
        ev(800_000, type="BUILDING_KILL", teamId=200, buildingType="TOWER_BUILDING",
           position={"x": 8955, "y": 8510}),
        # Mid-lane teamfight at 16 min: 4 kills in one cluster; blue wins 3-1.
        ev(
            960_000, type="CHAMPION_KILL", killerId=1, victimId=6,
            position={"x": 7400, "y": 7500}, bounty=300, shutdownBounty=0,
            assistingParticipantIds=[2],
            victimDamageReceived=[
                {"participantId": 1, "physicalDamage": 0, "magicDamage": 1500, "trueDamage": 0}
            ],
        ),
        ev(
            965_000, type="CHAMPION_KILL", killerId=2, victimId=7,
            position={"x": 7300, "y": 7600}, bounty=300, shutdownBounty=0,
            assistingParticipantIds=[1], victimDamageReceived=[],
        ),
        ev(
            975_000, type="CHAMPION_KILL", killerId=8, victimId=2,
            position={"x": 7350, "y": 7450}, bounty=300, shutdownBounty=0,
            assistingParticipantIds=[], victimDamageReceived=[],
        ),
        ev(
            985_000, type="CHAMPION_KILL", killerId=1, victimId=8,
            position={"x": 7500, "y": 7400}, bounty=300, shutdownBounty=0,
            assistingParticipantIds=[3], victimDamageReceived=[],
        ),
        # Baron for blue at 19 min.
        ev(
            1_140_000, type="ELITE_MONSTER_KILL", killerTeamId=100,
            monsterType="BARON_NASHOR", position={"x": 5007, "y": 10471},
        ),
        # Player's second death, side lane bot, late.
        ev(
            1_150_000, type="CHAMPION_KILL", killerId=9, victimId=1,
            position={"x": 13800, "y": 1400}, bounty=450, shutdownBounty=0,
            assistingParticipantIds=[], victimDamageReceived=[],
        ),
    ]
    frames[-1]["events"] = sorted(events, key=lambda e: e["timestamp"])
    return {"info": {"frames": frames}}


class CombinedMatchAndPeerStore:
    """Test-only double combining `RawMatchStore` + `PeerSampleStore`.

    Phase 8, Task 1 deleted `MatchStore`, the single on-disk store that
    used to expose both the raw match/timeline surface (now `RawMatchStore`)
    and the peer-game surface (now `PeerSampleStore`) on one object. No real
    production call site needs both surfaces on the same object today
    (PEERS' `_PeerStoreAdapter` wraps `PeerSampleStore` alone; RUNNER's job
    pipeline store, `RawMatchStore`, is deliberately peer-game-method-free
    per its own module docstring) -- but `resolve_peer_baseline`,
    `build_peer_comparison`, `fetch_benchmark_from_api` and `ingest_match`
    are duck-typed against a single object exposing BOTH surfaces (this is
    exactly what `MatchStore` used to provide), and their existing tests
    exercise both surfaces together against one fixture object. This double
    reproduces that combined surface for tests only, backed by a fresh
    `mongomock.MongoClient()` per instance -- it is not used by any
    production code path.
    """

    def __init__(self) -> None:
        client = mongomock.MongoClient()
        self._matches = RawMatchStore(client, db_name="league_stats")
        self._peers = PeerSampleStore(client, db_name="league_stats")

    # -- raw match/timeline surface (RawMatchStore) ------------------------
    def has_match(self, match_id: str) -> bool:
        return self._matches.has_match(match_id)

    def save_match(self, match_id: str, puuid: str, match: dict[str, Any]) -> None:
        self._matches.save_match(match_id, puuid, match)

    def save_timeline(self, match_id: str, timeline: dict[str, Any]) -> None:
        self._matches.save_timeline(match_id, timeline)

    def load_match(self, match_id: str) -> dict[str, Any] | None:
        return self._matches.load_match(match_id)

    def load_timeline(self, match_id: str) -> dict[str, Any] | None:
        return self._matches.load_timeline(match_id)

    def claim_ownership(self, puuid: str, match_ids: list[str]) -> list[str]:
        return self._matches.claim_ownership(puuid, match_ids)

    def iter_all_match_ids(self) -> Iterator[str]:
        return self._matches.iter_all_match_ids()

    def count(self) -> int:
        return self._matches.count()

    def iter_match_ids(self, puuid: str) -> Iterator[str]:
        return self._matches.iter_match_ids(puuid)

    # -- peer-game surface (PeerSampleStore) -------------------------------
    def upsert_peer_game(self, row: dict[str, Any]) -> bool:
        return self._peers.upsert_peer_game(row)

    def load_peer_games(
        self, *, champion: str, role: str, platform: str
    ) -> list[dict[str, Any]]:
        return self._peers.load_peer_games(champion=champion, role=role, platform=platform)

    def count_peer_games(self, *, champion: str, role: str, platform: str) -> int:
        return self._peers.count_peer_games(champion=champion, role=role, platform=platform)

    def iter_unverified_puuids(self, limit: int = 100) -> list[str]:
        return self._peers.iter_unverified_puuids(limit)

    def iter_unverified_puuids_for_build(
        self, champion: str, role: str, platform: str, limit: int = 200
    ) -> list[str]:
        return self._peers.iter_unverified_puuids_for_build(champion, role, platform, limit)

    def set_puuid_rank(self, puuid: str, tier: str, rank: str) -> int:
        return self._peers.set_puuid_rank(puuid, tier, rank)

    def count_by_tier(self) -> dict[str, int]:
        return self._peers.count_by_tier()

    def close(self) -> None:
        """No-op, matching `RawMatchStore.close()`'s shared-client contract."""
