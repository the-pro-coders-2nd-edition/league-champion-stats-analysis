"""SQLite-backed job queue and player registry for the web app.

One :class:`JobStore` instance is shared between the FastAPI request threads
and the background worker thread(s); a lock serialises access to the single
connection. Jobs survive restarts: queued jobs are picked up again, while
jobs that were mid-run are marked failed by :meth:`JobStore.recover_orphans`.

**Architectural decision (Phase 5, Task 2, closed):** `JobStore` stays on SQLite
permanently by design, not as a migration gap. Phase 2's Task 4 proved shared-
SQLite with `BEGIN IMMEDIATE` guards works correctly across processes; RUNNER's
gRPC job-id namespace has no natural mapping to the queue schema; and the UI's
queue-position/ETA features query `JobStore`'s SQL directly with no clear benefit
from Mongo migration.
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
CANCELLED = "cancelled"

ACTIVE_STATES: tuple[str, ...] = (QUEUED, FETCHING, ANALYZING, REPORT_READY, PEER_RUNNING)
RUNNING_STATES: tuple[str, ...] = (FETCHING, ANALYZING, REPORT_READY, PEER_RUNNING)
TERMINAL_STATES: tuple[str, ...] = (DONE, FAILED, CANCELLED)

JOB_KIND_ANALYZE = "analyze"
JOB_KIND_REFRESH = "refresh"
JOB_KIND_REGENERATE = "regenerate"

# Default gap between watch checks for one group.
DEFAULT_WATCH_INTERVAL_S = 60

# Used for ETA display until enough completed jobs exist to average.
DEFAULT_JOB_DURATION_S = 20 * 60

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    player_slug TEXT NOT NULL,
    riot_id TEXT NOT NULL,
    tagline TEXT NOT NULL,
    region TEXT NOT NULL,
    players_json TEXT NOT NULL DEFAULT '[]',
    filter_champion TEXT,
    filter_role TEXT,
    min_games INTEGER,
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
    peer_failed INTEGER NOT NULL DEFAULT 0,
    watch_enabled INTEGER NOT NULL DEFAULT 0,
    watch_interval_s INTEGER NOT NULL DEFAULT {DEFAULT_WATCH_INTERVAL_S},
    last_watch_at REAL,
    last_watch_error TEXT NOT NULL DEFAULT '',
    watch_seen_json TEXT NOT NULL DEFAULT '{{}}'
);
"""


def encode_players(players: list[dict[str, Any]]) -> str:
    """Serialize tracked players for SQLite storage."""
    from league_stats.core.models import solo_rank_fields

    payload: list[dict[str, Any]] = []
    for player in players:
        entry: dict[str, Any] = {
            "riot_id": player["riot_id"],
            "tagline": player["tagline"],
        }
        raw_icon = player.get("profile_icon_id")
        if raw_icon is not None:
            try:
                entry["profile_icon_id"] = int(raw_icon)
            except (TypeError, ValueError):
                pass
        entry.update(solo_rank_fields(player))
        payload.append(entry)
    return json.dumps(payload, separators=(",", ":"))


def decode_players(
    raw: str | None, *, riot_id: str = "", tagline: str = ""
) -> list[dict[str, Any]]:
    """Deserialize tracked players, falling back to the primary identity."""
    from league_stats.core.models import solo_rank_fields

    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list) and parsed:
            players: list[dict[str, Any]] = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("riot_id", "")).strip()
                tag = str(item.get("tagline", "")).strip()
                if not name or not tag:
                    continue
                entry: dict[str, Any] = {"riot_id": name, "tagline": tag}
                raw_icon = item.get("profile_icon_id")
                if raw_icon is not None:
                    try:
                        entry["profile_icon_id"] = int(raw_icon)
                    except (TypeError, ValueError):
                        pass
                entry.update(solo_rank_fields(item))
                players.append(entry)
            if players:
                return players
    if riot_id and tagline:
        return [{"riot_id": riot_id, "tagline": tagline}]
    return []


def players_label(players: list[dict[str, Any]]) -> str:
    """Comma-separated display label for tracked players."""
    return ", ".join(f"{p['riot_id']}#{p['tagline']}" for p in players)


class JobStore:
    """Thread-safe SQLite store for analysis jobs and known players."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # `busy_timeout` must be set FIRST: it is what makes every later
        # statement on this connection -- including the `journal_mode=WAL`
        # pragma itself and `_migrate`'s `BEGIN IMMEDIATE` below -- retry
        # instead of failing immediately with "database is locked" when two
        # processes/threads open this same file at once.
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._lock = threading.Lock()

    def _migrate(self) -> None:
        """Add columns introduced after the initial schema.

        Wrapped in `BEGIN IMMEDIATE` for the same reason as `enqueue`'s fix
        (see its comment, and this module's cross-process notes in
        `cron_watch/service.py`): `app` and `cron-watch` both open a
        `JobStore` onto the same shared `app.sqlite` file with no startup
        ordering guarantee between them. Without a lock taken *before* the
        `PRAGMA table_info` checks, two processes could both see a column as
        missing and both run `ALTER TABLE ... ADD COLUMN`, and the loser
        crashes with `sqlite3.OperationalError: duplicate column name`.
        `BEGIN IMMEDIATE` takes SQLite's write lock up front, so a second
        process's own `BEGIN IMMEDIATE` blocks (up to `busy_timeout`) until
        the first migration commits, and then sees the already-migrated
        schema instead of racing it.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for table in ("jobs", "players"):
                columns = {
                    row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")
                }
                if "players_json" not in columns:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN players_json TEXT NOT NULL DEFAULT '[]'"
                    )
            player_columns = {
                row[1] for row in self._conn.execute("PRAGMA table_info(players)")
            }
            watch_columns = (
                ("watch_enabled", "INTEGER NOT NULL DEFAULT 0"),
                ("watch_interval_s", f"INTEGER NOT NULL DEFAULT {DEFAULT_WATCH_INTERVAL_S}"),
                ("last_watch_at", "REAL"),
                ("last_watch_error", "TEXT NOT NULL DEFAULT ''"),
                ("watch_seen_json", "TEXT NOT NULL DEFAULT '{}'"),
            )
            for name, ddl in watch_columns:
                if name not in player_columns:
                    self._conn.execute(f"ALTER TABLE players ADD COLUMN {name} {ddl}")
            job_columns = {
                row[1] for row in self._conn.execute("PRAGMA table_info(jobs)")
            }
            if "filter_champion" not in job_columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN filter_champion TEXT")
            if "filter_role" not in job_columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN filter_role TEXT")
            if "min_games" not in job_columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN min_games INTEGER")
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------ jobs

    def enqueue(
        self,
        *,
        kind: str,
        riot_id: str,
        tagline: str,
        region: str,
        player_slug: str,
        players: list[dict[str, Any]] | None = None,
        filter_champion: str | None = None,
        filter_role: str | None = None,
        min_games: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Queue a job, deduplicating against an existing active job.

        Optional ``filter_champion`` / ``filter_role`` scope analysis to one
        build (used by per-champion report refresh). ``min_games`` overrides
        the config threshold for how many ranked games a build needs.

        Returns:
            ``(job, created)`` — the active or newly created job row.
        """
        tracked = players or [{"riot_id": riot_id, "tagline": tagline}]
        champion = (filter_champion or "").strip() or None
        role = (filter_role or "").strip() or None
        games_threshold = int(min_games) if min_games is not None else None
        with self._lock:
            # `BEGIN IMMEDIATE` (not the module's default deferred BEGIN, which
            # only takes a lock at the first *write*) makes the
            # check-then-insert below atomic across separate OS processes
            # sharing this file, not just across threads in this one process.
            # Without it, two processes (e.g. the monolith and CRON-watch)
            # could both run `_active_job_for` and see "nothing active" before
            # either INSERTs, defeating this dedup entirely -- WAL mode and
            # `busy_timeout` alone only prevent SQLITE_BUSY errors, they do
            # not make this sequence atomic. See
            # `cron_watch/service.py`'s module docstring for the fuller
            # writeup of this gap and why this class's own `threading.Lock`
            # (process-local) does not cover it.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._active_job_for(player_slug)
                if existing is not None:
                    self._conn.rollback()
                    return existing, False
                now = time.time()
                cursor = self._conn.execute(
                    """
                    INSERT INTO jobs (kind, player_slug, riot_id, tagline, region,
                                      players_json, filter_champion, filter_role,
                                      min_games, state, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        kind,
                        player_slug,
                        riot_id,
                        tagline,
                        region,
                        encode_players(tracked),
                        champion,
                        role,
                        games_threshold,
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
            except Exception:
                self._conn.rollback()
                raise

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
            # Retry: a job may be cancelled between SELECT and the conditional UPDATE.
            while True:
                row = self._conn.execute(
                    "SELECT id FROM jobs WHERE state = ? ORDER BY id LIMIT 1", (QUEUED,)
                ).fetchone()
                if row is None:
                    return None
                now = time.time()
                cursor = self._conn.execute(
                    "UPDATE jobs SET state = ?, started_at = ?, updated_at = ? "
                    "WHERE id = ? AND state = ?",
                    (FETCHING, now, now, row["id"], QUEUED),
                )
                self._conn.commit()
                if cursor.rowcount:
                    return self._get(int(row["id"]))

    def set_state(
        self,
        job_id: int,
        state: str,
        *,
        detail: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Transition a job to a new state, optionally updating detail/error.

        Returns:
            ``False`` when the job is already cancelled and ``state`` is not
            ``cancelled`` (so the worker cannot overwrite a user cancel).
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return False
            if row["state"] == CANCELLED and state != CANCELLED:
                return False
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
            return True

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
            row = self._conn.execute(
                "SELECT state FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None or row["state"] == CANCELLED:
                return
            self._conn.execute(
                "UPDATE jobs SET stage_detail = ?, stage_current = ?, stage_total = ?, "
                "updated_at = ? WHERE id = ?",
                (detail, current, total, time.time(), job_id),
            )
            self._conn.commit()

    def is_cancelled(self, job_id: int) -> bool:
        """Return whether the job has been cancelled."""
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return row is not None and row["state"] == CANCELLED

    def cancel(self, job_id: int) -> dict[str, Any] | None:
        """Cancel a queued or running job without deleting on-disk reports.

        Returns:
            The updated job row, or ``None`` if the job is missing or already
            in a terminal state.
        """
        with self._lock:
            try:
                job = self._get(job_id)
            except LookupError:
                return None
            if job["state"] not in ACTIVE_STATES:
                return None
            now = time.time()
            self._conn.execute(
                "UPDATE jobs SET state = ?, stage_detail = ?, error = ?, "
                "finished_at = ?, updated_at = ? WHERE id = ?",
                (CANCELLED, "Cancelled by user", "", now, now, job_id),
            )
            self._conn.commit()
            return self._get(job_id)

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
        players: list[dict[str, Any]] | None = None,
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

    def set_watch(
        self, slug: str, *, enabled: bool, interval_s: int | None = None
    ) -> bool:
        """Turn watching on or off for a group. Returns ``False`` if unknown."""
        with self._lock:
            row = self._conn.execute(
                "SELECT slug FROM players WHERE slug = ?", (slug,)
            ).fetchone()
            if row is None:
                return False
            if interval_s is None:
                self._conn.execute(
                    "UPDATE players SET watch_enabled = ?, last_watch_error = '' "
                    "WHERE slug = ?",
                    (1 if enabled else 0, slug),
                )
            else:
                self._conn.execute(
                    "UPDATE players SET watch_enabled = ?, watch_interval_s = ?, "
                    "last_watch_error = '' WHERE slug = ?",
                    (1 if enabled else 0, max(60, int(interval_s)), slug),
                )
            self._conn.commit()
            return True

    def list_watched_players(self) -> list[dict[str, Any]]:
        """Every group with watching enabled, identities decoded."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM players WHERE watch_enabled = 1 ORDER BY slug"
            ).fetchall()
        watched: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["players"] = decode_players(
                data.get("players_json"),
                riot_id=str(data.get("riot_id", "")),
                tagline=str(data.get("tagline", "")),
            )
            try:
                data["watch_seen"] = json.loads(data.get("watch_seen_json") or "{}")
            except json.JSONDecodeError:
                data["watch_seen"] = {}
            watched.append(data)
        return watched

    def record_watch_tick(
        self,
        slug: str,
        *,
        seen: dict[str, str] | None = None,
        error: str = "",
        at: float | None = None,
    ) -> None:
        """Stamp a watch check, storing the newest match id seen per account.

        ``at`` lets the poller supply its own clock, so the timestamp it writes
        and the one it compares against when deciding whether a group is due
        cannot disagree.
        """
        stamp = time.time() if at is None else float(at)
        with self._lock:
            if seen is None:
                self._conn.execute(
                    "UPDATE players SET last_watch_at = ?, last_watch_error = ? "
                    "WHERE slug = ?",
                    (stamp, error, slug),
                )
            else:
                self._conn.execute(
                    "UPDATE players SET last_watch_at = ?, last_watch_error = ?, "
                    "watch_seen_json = ? WHERE slug = ?",
                    (stamp, error, json.dumps(seen), slug),
                )
            self._conn.commit()

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
