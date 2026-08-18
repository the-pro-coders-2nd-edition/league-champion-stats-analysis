"""SQLite persistence for Career mode ladders.

Career state cannot be re-derived from match data alone: ``hit=12, need=15`` is
``In progress`` for a goal that never cleared and ``At risk`` for one that did,
and rung targets are frozen at generation time so they never move under a player
who is closing in on them. Both live here, keyed by champion + role + the
primary account slug.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Sequence

from league_stats.analysis.career.models import Rung, StoredGoal
from league_stats.utils import get_logger

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
    pending_congrats_track TEXT NOT NULL DEFAULT ''
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
            "target, need, state, since_ms FROM career_goals WHERE build_key = ? "
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
                    comparator="under" if row[5] == "under" else "at_least",
                    target=float(row[6]),
                    need=int(row[7]),
                ),
                state=str(row[8]),
                since_ms=int(row[9]),
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
    ) -> None:
        """Replace a slot with a freshly generated track and its frozen rungs."""
        self.delete_slot(key, slot)
        self._conn.executemany(
            "INSERT INTO career_goals (build_key, slot, goal_index, track_key, text, "
            "column_name, comparator, target, need, state, since_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
