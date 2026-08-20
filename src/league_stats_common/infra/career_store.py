"""SQLite persistence for Career mode ladders.

Career state cannot be re-derived from match data alone: ``hit=12, need=15`` is
``In progress`` for a goal that never cleared and ``At risk`` for one that did,
and rung targets are frozen at generation time so they never move under a player
who is closing in on them. Both live here, keyed by champion + role + the
primary account slug.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Sequence

from league_stats_runner.analysis.career.models import Comparator, Rung, StoredGoal
from league_stats_common.utils import get_logger

_KNOWN_COMPARATORS: frozenset[str] = frozenset({"at_least", "under", "at_most"})


def _load_comparator(value: object) -> Comparator:
    text = str(value)
    if text in _KNOWN_COMPARATORS:
        return text  # type: ignore[return-value]
    return "at_least"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS career_goals (
    build_key   TEXT NOT NULL,
    slot        INTEGER NOT NULL,
    goal_index  INTEGER NOT NULL,
    track_key   TEXT NOT NULL,
    text        TEXT NOT NULL,
    column_name TEXT NOT NULL,
    comparator  TEXT NOT NULL,
    target      REAL NOT NULL,
    need        INTEGER NOT NULL,
    state       TEXT NOT NULL,
    since_ms    INTEGER NOT NULL DEFAULT 0,
    peer_seeded INTEGER NOT NULL DEFAULT 0,
    why         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (build_key, slot, goal_index)
);
CREATE TABLE IF NOT EXISTS career_used_tracks (
    build_key  TEXT NOT NULL,
    track_key  TEXT NOT NULL,
    cleared_at TEXT NOT NULL,
    PRIMARY KEY (build_key, track_key, cleared_at)
);
CREATE TABLE IF NOT EXISTS career_flags (
    build_key TEXT PRIMARY KEY,
    pending_congrats_track TEXT NOT NULL DEFAULT '',
    pending_drop_slot INTEGER NOT NULL DEFAULT -1,
    recap_acked_match_id TEXT NOT NULL DEFAULT '',
    recap_acked_game_ms INTEGER NOT NULL DEFAULT 0,
    recap_acked_hits_json TEXT NOT NULL DEFAULT '',
    recap_acked_track_key TEXT NOT NULL DEFAULT ''
);
"""


def build_key(player_slug: str, champion: str, role: str) -> str:
    """Ladder identity: one ladder per champion + role per tracked player."""
    return f"{player_slug}|{champion}|{role.upper()}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CareerStore:
    """Persistent store of Career goals, retired tracks and pending banners."""

    def __init__(self, db_path: Path) -> None:
        """Open (or create) the store and apply the schema."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=30.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_SCHEMA)
        self._log = get_logger("career_store")
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Add columns that post-date a ladder already on disk."""
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(career_goals)")}
        if "since_ms" not in columns:
            self._log.info("Adding career_goals.since_ms")
            self._conn.execute(
                "ALTER TABLE career_goals ADD COLUMN since_ms INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.commit()
        # Defaults to 0, so every block already on disk reads as peer-blind and
        # gets retargeted on the next run that has peer percentiles.
        if "peer_seeded" not in columns:
            self._log.info("Adding career_goals.peer_seeded")
            self._conn.execute(
                "ALTER TABLE career_goals ADD COLUMN peer_seeded INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.commit()
        # Defaults to empty, so a goal written before the column existed simply has no
        # explanation until its block next regenerates.
        if "why" not in columns:
            self._log.info("Adding career_goals.why")
            self._conn.execute(
                "ALTER TABLE career_goals ADD COLUMN why TEXT NOT NULL DEFAULT ''"
            )
            self._conn.commit()
        flag_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(career_flags)")}
        if "pending_drop_slot" not in flag_columns:
            self._log.info("Adding career_flags.pending_drop_slot")
            self._conn.execute(
                "ALTER TABLE career_flags ADD COLUMN pending_drop_slot INTEGER NOT NULL DEFAULT -1"
            )
            self._conn.commit()
        if "recap_acked_match_id" not in flag_columns:
            self._log.info("Adding career_flags recap columns")
            self._conn.execute(
                "ALTER TABLE career_flags ADD COLUMN recap_acked_match_id TEXT NOT NULL DEFAULT ''"
            )
            self._conn.execute(
                "ALTER TABLE career_flags ADD COLUMN recap_acked_game_ms INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.execute(
                "ALTER TABLE career_flags ADD COLUMN recap_acked_hits_json TEXT NOT NULL DEFAULT ''"
            )
            self._conn.execute(
                "ALTER TABLE career_flags ADD COLUMN recap_acked_track_key TEXT NOT NULL DEFAULT ''"
            )
            self._conn.commit()

    def __enter__(self) -> "CareerStore":
        """Enter a context manager scope."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the store on context exit."""
        self.close()

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def load_goals(self, key: str) -> list[StoredGoal]:
        """Every persisted goal for a ladder, ordered by slot then goal index."""
        rows = self._conn.execute(
            "SELECT slot, goal_index, track_key, text, column_name, comparator, "
            "target, need, state, since_ms, peer_seeded, why FROM career_goals "
            "WHERE build_key = ? "
            "ORDER BY slot, goal_index",
            (key,),
        ).fetchall()
        return [
            StoredGoal(
                slot=int(row[0]),
                goal_index=int(row[1]),
                track_key=str(row[2]),
                rung=Rung(
                    text=str(row[3]),
                    column=str(row[4]),
                    comparator=_load_comparator(row[5]),
                    target=float(row[6]),
                    need=int(row[7]),
                    why=str(row[11] or ""),
                ),
                state=str(row[8]),
                since_ms=int(row[9]),
                peer_seeded=bool(row[10]),
            )
            for row in rows
        ]

    def write_slot(
        self,
        key: str,
        slot: int,
        track_key: str,
        rungs: Sequence[Rung],
        states: Sequence[str],
        since_ms: int = 0,
        peer_seeded: bool = False,
    ) -> None:
        """Replace a slot with a freshly generated track and its frozen rungs.

        ``peer_seeded`` records whether peer percentiles were available when the
        rungs were frozen. A slot written without them is provisional: the next
        run that has peers rebuilds it, unless the player has already started it.
        """
        self.delete_slot(key, slot)
        self._conn.executemany(
            "INSERT INTO career_goals (build_key, slot, goal_index, track_key, text, "
            "column_name, comparator, target, need, state, since_ms, peer_seeded, why) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    key,
                    slot,
                    index,
                    track_key,
                    rung.text,
                    rung.column,
                    rung.comparator,
                    float(rung.target),
                    int(rung.need),
                    states[index],
                    int(since_ms),
                    1 if peer_seeded else 0,
                    rung.why,
                )
                for index, rung in enumerate(rungs)
            ],
        )
        self._conn.commit()

    def save_goal_states(self, key: str, states: dict[tuple[int, int], str]) -> None:
        """Persist recomputed states for ``(slot, goal_index)`` pairs."""
        if not states:
            return
        self._conn.executemany(
            "UPDATE career_goals SET state = ? "
            "WHERE build_key = ? AND slot = ? AND goal_index = ?",
            [(state, key, slot, index) for (slot, index), state in states.items()],
        )
        self._conn.commit()

    def delete_slot(self, key: str, slot: int) -> None:
        """Drop every goal in a slot."""
        self._conn.execute(
            "DELETE FROM career_goals WHERE build_key = ? AND slot = ?", (key, slot)
        )
        self._conn.commit()

    def move_slot(self, key: str, src: int, dst: int, *, since_ms: int | None = None) -> None:
        """Shift a slot's goals left, replacing whatever sat at the destination.

        ``since_ms`` re-stamps the start line, which matters on promotion to the
        live slot: a queued block must not inherit credit from the games that
        cleared the block ahead of it.
        """
        self.delete_slot(key, dst)
        if since_ms is None:
            self._conn.execute(
                "UPDATE career_goals SET slot = ? WHERE build_key = ? AND slot = ?",
                (dst, key, src),
            )
        else:
            self._conn.execute(
                "UPDATE career_goals SET slot = ?, since_ms = ? "
                "WHERE build_key = ? AND slot = ?",
                (dst, int(since_ms), key, src),
            )
        self._conn.commit()

    def record_used_track(self, key: str, track_key: str) -> None:
        """Mark a track as retired so fresh tracks are preferred over recycling."""
        self._conn.execute(
            "INSERT OR IGNORE INTO career_used_tracks (build_key, track_key, cleared_at) "
            "VALUES (?, ?, ?)",
            (key, track_key, _now()),
        )
        self._conn.commit()

    def used_track_keys(self, key: str) -> set[str]:
        """Track keys this ladder has already retired at least once."""
        rows = self._conn.execute(
            "SELECT DISTINCT track_key FROM career_used_tracks WHERE build_key = ?", (key,)
        ).fetchall()
        return {str(row[0]) for row in rows}

    def set_pending_congrats(self, key: str, track_key: str) -> None:
        """Queue the block-complete banner for the next render."""
        self._conn.execute(
            "INSERT INTO career_flags (build_key, pending_congrats_track) VALUES (?, ?) "
            "ON CONFLICT(build_key) DO UPDATE SET pending_congrats_track = excluded.pending_congrats_track",
            (key, track_key),
        )
        self._conn.commit()

    def peek_pending_congrats(self, key: str) -> str:
        """Read the pending banner without consuming it.

        Consuming at build time was safe while every rebuild was user-initiated.
        Under group watch a background rebuild the reader never opens would
        swallow the banner, so the flag now survives until a reader acknowledges
        it via :meth:`clear_pending_congrats`.
        """
        row = self._conn.execute(
            "SELECT pending_congrats_track FROM career_flags WHERE build_key = ?", (key,)
        ).fetchone()
        return str(row[0]) if row and row[0] else ""

    def clear_pending_congrats(self, key: str) -> None:
        """Mark the block-complete banner as seen."""
        self._conn.execute(
            "UPDATE career_flags SET pending_congrats_track = '' WHERE build_key = ?",
            (key,),
        )
        self._conn.commit()

    def peek_recap_ack(self, key: str) -> tuple[str, int, dict[str, int], str]:
        """Last acknowledged recap: match id, its game_creation_ms, goal hit counts, track key.

        Empty/zero/empty-dict/empty when this ladder has never acknowledged a recap.
        """
        row = self._conn.execute(
            "SELECT recap_acked_match_id, recap_acked_game_ms, recap_acked_hits_json, "
            "recap_acked_track_key FROM career_flags WHERE build_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return "", 0, {}, ""
        match_id, game_ms, hits_json, track_key = (
            str(row[0] or ""),
            int(row[1] or 0),
            str(row[2] or ""),
            str(row[3] or ""),
        )
        hits: dict[str, int] = {}
        if hits_json:
            try:
                hits = {str(k): int(v) for k, v in json.loads(hits_json).items()}
            except (ValueError, TypeError, json.JSONDecodeError):
                hits = {}
        return match_id, game_ms, hits, track_key

    def ack_recap(
        self,
        key: str,
        *,
        match_id: str,
        game_ms: int,
        hits: dict[str, int],
        track_key: str,
    ) -> None:
        """Record the newest game, goal-hit counts and track a reader has seen recapped."""
        self._conn.execute(
            "INSERT INTO career_flags (build_key, recap_acked_match_id, recap_acked_game_ms, "
            "recap_acked_hits_json, recap_acked_track_key) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(build_key) DO UPDATE SET recap_acked_match_id = excluded.recap_acked_match_id, "
            "recap_acked_game_ms = excluded.recap_acked_game_ms, "
            "recap_acked_hits_json = excluded.recap_acked_hits_json, "
            "recap_acked_track_key = excluded.recap_acked_track_key",
            (key, match_id, int(game_ms), json.dumps(hits), track_key),
        )
        self._conn.commit()

    def request_drop(self, key: str, slot: int) -> None:
        """Queue a manual block drop for the next analysis run.

        The HTTP route that offers the button has no match data, so it cannot
        restamp a promoted block's window or generate a replacement itself.
        Recording the intent here lets :func:`advance_career` perform the drop
        with the real ``TrackContext`` on the run the request kicks off.
        """
        self._conn.execute(
            "INSERT INTO career_flags (build_key, pending_drop_slot) VALUES (?, ?) "
            "ON CONFLICT(build_key) DO UPDATE SET pending_drop_slot = excluded.pending_drop_slot",
            (key, int(slot)),
        )
        self._conn.commit()

    def peek_pending_drop(self, key: str) -> int | None:
        """The slot a reader asked to drop, or ``None`` when nothing is queued."""
        row = self._conn.execute(
            "SELECT pending_drop_slot FROM career_flags WHERE build_key = ?", (key,)
        ).fetchone()
        if row is None or int(row[0]) < 0:
            return None
        return int(row[0])

    def clear_pending_drop(self, key: str) -> None:
        """Mark a queued drop as performed."""
        self._conn.execute(
            "UPDATE career_flags SET pending_drop_slot = -1 WHERE build_key = ?", (key,)
        )
        self._conn.commit()

    def clear_all(self) -> dict[str, int]:
        """Delete every ladder, retired track and pending flag.

        Returns row counts per table before deletion. Safe on an empty store.
        """
        counts = self.row_counts()
        self._conn.executescript(
            "DELETE FROM career_goals;"
            "DELETE FROM career_used_tracks;"
            "DELETE FROM career_flags;"
        )
        self._conn.commit()
        return counts

    def row_counts(self) -> dict[str, int]:
        """Row counts for each Career table."""
        counts: dict[str, int] = {}
        for table in ("career_goals", "career_used_tracks", "career_flags"):
            row = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = int(row[0]) if row else 0
        return counts
