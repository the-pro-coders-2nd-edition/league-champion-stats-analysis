"""SQLite-backed job queue and player registry for the web app.

One :class:`JobStore` instance is shared between the FastAPI request threads
and the background worker thread(s); a lock serialises access to the single
connection. Jobs survive restarts: queued jobs are picked up again, while
jobs that were mid-run are marked failed by :meth:`JobStore.recover_orphans`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

# Job lifecycle states.
QUEUED = "queued"
FETCHING = "fetching"
ANALYZING = "analyzing"
REPORT_READY = "report_ready"
PEER_RUNNING = "peer_running"
DONE = "done"
FAILED = "failed"

ACTIVE_STATES: tuple[str, ...] = (QUEUED, FETCHING, ANALYZING, REPORT_READY, PEER_RUNNING)
RUNNING_STATES: tuple[str, ...] = (FETCHING, ANALYZING, REPORT_READY, PEER_RUNNING)
TERMINAL_STATES: tuple[str, ...] = (DONE, FAILED)

JOB_KIND_ANALYZE = "analyze"
JOB_KIND_REFRESH = "refresh"

# Used for ETA display until enough completed jobs exist to average.
DEFAULT_JOB_DURATION_S = 20 * 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    player_slug TEXT NOT NULL,
    riot_id TEXT NOT NULL,
    tagline TEXT NOT NULL,
    region TEXT NOT NULL,
    players_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL DEFAULT 'queued',
    stage_detail TEXT NOT NULL DEFAULT '',
    stage_current INTEGER,
    stage_total INTEGER,
    error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs (state);
CREATE INDEX IF NOT EXISTS idx_jobs_player ON jobs (player_slug);
CREATE TABLE IF NOT EXISTS players (
    slug TEXT PRIMARY KEY,
    riot_id TEXT NOT NULL,
    tagline TEXT NOT NULL,
    region TEXT NOT NULL,
    players_json TEXT NOT NULL DEFAULT '[]',
    last_job_id INTEGER,
    base_completed_at REAL,
    peer_completed_at REAL,
    peer_failed INTEGER NOT NULL DEFAULT 0
);
"""


def encode_players(players: list[dict[str, str]]) -> str:
    """Serialize tracked players for SQLite storage."""
    return json.dumps(
        [{"riot_id": p["riot_id"], "tagline": p["tagline"]} for p in players],
        separators=(",", ":"),
    )


def decode_players(raw: str | None, *, riot_id: str = "", tagline: str = "") -> list[dict[str, str]]:
    """Deserialize tracked players, falling back to the primary identity."""
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list) and parsed:
            players: list[dict[str, str]] = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("riot_id", "")).strip()
                tag = str(item.get("tagline", "")).strip()
                if name and tag:
                    players.append({"riot_id": name, "tagline": tag})
            if players:
                return players
    if riot_id and tagline:
        return [{"riot_id": riot_id, "tagline": tagline}]
    return []


def players_label(players: list[dict[str, str]]) -> str:
    """Comma-separated display label for tracked players."""
    return ", ".join(f"{p['riot_id']}#{p['tagline']}" for p in players)


class JobStore:
    """Thread-safe SQLite store for analysis jobs and known players."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._lock = threading.Lock()

    def _migrate(self) -> None:
        """Add columns introduced after the initial schema."""
        for table in ("jobs", "players"):
            columns = {
                row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            if "players_json" not in columns:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN players_json TEXT NOT NULL DEFAULT '[]'"
                )
        self._conn.commit()

    # ------------------------------------------------------------------ jobs

    def enqueue(
        self,
        *,
        kind: str,
        riot_id: str,
        tagline: str,
        region: str,
        player_slug: str,
        players: list[dict[str, str]] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Queue a job, deduplicating against an existing active job.

        Returns:
            ``(job, created)`` — the active or newly created job row.
        """
        tracked = players or [{"riot_id": riot_id, "tagline": tagline}]
        with self._lock:
            existing = self._active_job_for(player_slug)
            if existing is not None:
                return existing, False
            now = time.time()
            cursor = self._conn.execute(
                """
                INSERT INTO jobs (kind, player_slug, riot_id, tagline, region,
                                  players_json, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kind,
                    player_slug,
                    riot_id,
                    tagline,
                    region,
                    encode_players(tracked),
                    QUEUED,
                    now,
                    now,
                ),
            )
            self._conn.execute(
                "UPDATE players SET last_job_id = ? WHERE slug = ?",
                (cursor.lastrowid, player_slug),
            )
            self._conn.commit()
            return self._get(int(cursor.lastrowid)), True

    def get(self, job_id: int) -> dict[str, Any] | None:
        """Load one job by id."""
        with self._lock:
            try:
                return self._get(job_id)
            except LookupError:
                return None

    def _get(self, job_id: int) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise LookupError(f"job {job_id} not found")
        data = dict(row)
        data["players"] = decode_players(
            data.get("players_json"),
            riot_id=str(data.get("riot_id", "")),
            tagline=str(data.get("tagline", "")),
        )
        return data

    def _active_job_for(self, player_slug: str) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        row = self._conn.execute(
            f"SELECT * FROM jobs WHERE player_slug = ? AND state IN ({placeholders}) "
            "ORDER BY id DESC LIMIT 1",
            (player_slug, *ACTIVE_STATES),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["players"] = decode_players(
            data.get("players_json"),
            riot_id=str(data.get("riot_id", "")),
            tagline=str(data.get("tagline", "")),
        )
        return data

    def active_job_for_player(self, player_slug: str) -> dict[str, Any] | None:
        """Return the queued or running job for a player, if any."""
        with self._lock:
            return self._active_job_for(player_slug)

    def list_active_jobs(self) -> list[dict[str, Any]]:
        """Return every queued or running job (newest active job per player)."""
        with self._lock:
            placeholders = ",".join("?" for _ in ACTIVE_STATES)
            rows = self._conn.execute(
                f"SELECT * FROM jobs WHERE state IN ({placeholders}) ORDER BY id DESC",
                ACTIVE_STATES,
            ).fetchall()
            seen: set[str] = set()
            jobs: list[dict[str, Any]] = []
            for row in rows:
                slug = str(row["player_slug"])
                if slug in seen:
                    continue
                seen.add(slug)
                data = dict(row)
                data["players"] = decode_players(
                    data.get("players_json"),
                    riot_id=str(data.get("riot_id", "")),
                    tagline=str(data.get("tagline", "")),
                )
                jobs.append(data)
            return jobs

    def claim_next(self) -> dict[str, Any] | None:
        """Atomically claim the oldest queued job, moving it to ``fetching``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM jobs WHERE state = ? ORDER BY id LIMIT 1", (QUEUED,)
            ).fetchone()
            if row is None:
                return None
            now = time.time()
            self._conn.execute(
                "UPDATE jobs SET state = ?, started_at = ?, updated_at = ? WHERE id = ?",
                (FETCHING, now, now, row["id"]),
            )
            self._conn.commit()
            return self._get(int(row["id"]))

    def set_state(
        self,
        job_id: int,
        state: str,
        *,
        detail: str | None = None,
        error: str | None = None,
    ) -> None:
        """Transition a job to a new state, optionally updating detail/error."""
        with self._lock:
            now = time.time()
            sets = ["state = ?", "updated_at = ?"]
            params: list[Any] = [state, now]
            if detail is not None:
                sets.append("stage_detail = ?")
                params.append(detail)
                sets.append("stage_current = NULL")
                sets.append("stage_total = NULL")
            if error is not None:
                sets.append("error = ?")
                params.append(error)
            if state in TERMINAL_STATES:
                sets.append("finished_at = ?")
                params.append(now)
            params.append(job_id)
            self._conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)
            self._conn.commit()

    def update_progress(
        self,
        job_id: int,
        *,
        detail: str,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        """Update progress fields without changing the job state."""
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET stage_detail = ?, stage_current = ?, stage_total = ?, "
                "updated_at = ? WHERE id = ?",
                (detail, current, total, time.time(), job_id),
            )
            self._conn.commit()

    def queue_position(self, job_id: int) -> int | None:
        """0-based number of queued jobs ahead; ``None`` unless still queued."""
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None or row["state"] != QUEUED:
                return None
            ahead = self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE state = ? AND id < ?", (QUEUED, job_id)
            ).fetchone()[0]
            running = self._conn.execute(
                f"SELECT COUNT(*) FROM jobs WHERE state IN "
                f"({','.join('?' for _ in RUNNING_STATES)})",
                RUNNING_STATES,
            ).fetchone()[0]
            return int(ahead) + int(running)

    def average_duration_s(self) -> float:
        """Rolling average duration of recently completed jobs."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT finished_at - started_at AS d FROM jobs "
                "WHERE state = ? AND started_at IS NOT NULL AND finished_at IS NOT NULL "
                "ORDER BY id DESC LIMIT 5",
                (DONE,),
            ).fetchall()
        durations = [float(row["d"]) for row in rows if row["d"] and row["d"] > 0]
        if not durations:
            return float(DEFAULT_JOB_DURATION_S)
        return sum(durations) / len(durations)

    def recover_orphans(self) -> int:
        """Fail jobs left mid-run by a previous process (queued jobs survive)."""
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE jobs SET state = ?, error = ?, finished_at = ?, updated_at = ? "
                f"WHERE state IN ({','.join('?' for _ in RUNNING_STATES)})",
                (
                    FAILED,
                    "Server restarted while this job was running. Submit it again.",
                    time.time(),
                    time.time(),
                    *RUNNING_STATES,
                ),
            )
            self._conn.commit()
            return int(cursor.rowcount)

    # --------------------------------------------------------------- players

    def upsert_player(
        self,
        *,
        slug: str,
        riot_id: str,
        tagline: str,
        region: str,
        players: list[dict[str, str]] | None = None,
    ) -> None:
        """Register (or update the identity of) a player or group."""
        tracked = players or [{"riot_id": riot_id, "tagline": tagline}]
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO players (slug, riot_id, tagline, region, players_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (slug) DO UPDATE SET
                    riot_id = excluded.riot_id,
                    tagline = excluded.tagline,
                    region = excluded.region,
                    players_json = excluded.players_json
                """,
                (slug, riot_id, tagline, region, encode_players(tracked)),
            )
            self._conn.commit()

    def get_player(self, slug: str) -> dict[str, Any] | None:
        """Load one player/group row with a decoded ``players`` list."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM players WHERE slug = ?", (slug,)
            ).fetchone()
            if row is None:
                return None
            data = dict(row)
            data["players"] = decode_players(
                data.get("players_json"),
                riot_id=str(data.get("riot_id", "")),
                tagline=str(data.get("tagline", "")),
            )
            return data

    def mark_player_base_complete(self, slug: str) -> None:
        """Record that the base (pre-peer) report finished for a player."""
        with self._lock:
            self._conn.execute(
                "UPDATE players SET base_completed_at = ?, peer_failed = 0 WHERE slug = ?",
                (time.time(), slug),
            )
            self._conn.commit()

    def mark_player_peer_complete(self, slug: str) -> None:
        """Record that peer analysis finished for a player."""
        with self._lock:
            self._conn.execute(
                "UPDATE players SET peer_completed_at = ?, peer_failed = 0 WHERE slug = ?",
                (time.time(), slug),
            )
            self._conn.commit()

    def mark_player_peer_failed(self, slug: str) -> None:
        """Record that peer analysis failed (base report remains available)."""
        with self._lock:
            self._conn.execute(
                "UPDATE players SET peer_failed = 1 WHERE slug = ?", (slug,)
            )
            self._conn.commit()

    def recent_players(self, limit: int = 50) -> list[dict[str, Any]]:
        """Players ordered by most recent activity."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM players ORDER BY COALESCE(base_completed_at, 0) DESC "
                "LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()
