"""Content-addressed cache for derived analysis artifacts.

``MatchStore`` caches what Riot sent us. This caches what *we* computed from it:
parsed records, per-game review details, per-slice dashboard bundles. Adding one
new game should cost work proportional to one game, not to the whole history.

The dangerous failure mode is serving an artifact computed by older code, which
is silent and produces wrong coaching. Every key therefore carries a
``code_version`` derived from a hash of the source files that produce that kind
of artifact (see :func:`code_version`), so a code change cannot hit a stale
entry -- it simply looks under a different key and recomputes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Iterable, Sequence

from league_stats.utils import get_logger

_SCHEMA = """
CREATE TABLE IF NOT EXISTS derived (
    kind         TEXT NOT NULL,
    key          TEXT NOT NULL,
    code_version TEXT NOT NULL,
    payload      TEXT NOT NULL,
    bytes        INTEGER NOT NULL,
    created_at   REAL NOT NULL,
    hit_at       REAL NOT NULL,
    PRIMARY KEY (kind, key, code_version)
);
CREATE INDEX IF NOT EXISTS idx_derived_hit ON derived (hit_at);
"""

# Artifact kinds. Each maps to the source paths whose contents define it.
KIND_RECORD: Final[str] = "record"
KIND_GAME_REVIEW: Final[str] = "game_review"
KIND_SLICE: Final[str] = "slice"

_PACKAGE_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

# Source files that determine each artifact kind's contents. Deliberately
# generous: a false invalidation costs one recompute, a missed one serves wrong
# data.
_KIND_SOURCES: Final[dict[str, tuple[str, ...]]] = {
    KIND_RECORD: ("ingest", "core/models.py"),
    KIND_GAME_REVIEW: ("analysis/game_review", "pipeline/game_review.py"),
    KIND_SLICE: (
        "analysis",
        "pipeline/bundles.py",
        "presentation/view_models.py",
        "presentation/tones.py",
    ),
}

DEFAULT_MAX_BYTES: Final[int] = 512 * 1024 * 1024  # 512 MiB


def _iter_source_files(relative: str) -> Iterable[Path]:
    target = _PACKAGE_ROOT / relative
    if target.is_file():
        yield target
        return
    if target.is_dir():
        yield from sorted(target.rglob("*.py"))


@lru_cache(maxsize=None)
def code_version(kind: str) -> str:
    """Short hash of the sources that produce ``kind``.

    Computed once per process. An unknown kind hashes the whole package, which is
    conservative -- it invalidates on any change -- rather than silently sharing
    a key across code revisions.
    """
    relatives = _KIND_SOURCES.get(kind, (".",))
    digest = hashlib.sha256()
    for relative in relatives:
        for path in _iter_source_files(relative):
            digest.update(path.relative_to(_PACKAGE_ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def slice_fingerprint(match_ids: Sequence[str], *, salt: str = "") -> str:
    """Stable key for a set of games, independent of their order.

    ``salt`` carries anything outside the match set that changes the result --
    the role, the queue label, whether peer data was available.
    """
    digest = hashlib.sha256(salt.encode())
    for match_id in sorted(match_ids):
        digest.update(b"\x00")
        digest.update(match_id.encode())
    return digest.hexdigest()[:32]


class DerivedStore:
    """SQLite cache of derived artifacts, keyed by content and code version."""

    def __init__(self, db_path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        """Open (or create) the store and apply the schema."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=30.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_SCHEMA)
        self._max_bytes = max_bytes
        self._log = get_logger("derived")

    def __enter__(self) -> "DerivedStore":
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

    def get(self, kind: str, key: str) -> Any | None:
        """Return a cached artifact, or ``None`` on a miss."""
        row = self._conn.execute(
            "SELECT payload FROM derived WHERE kind = ? AND key = ? AND code_version = ?",
            (kind, key, code_version(kind)),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row[0])
        except json.JSONDecodeError:
            self.delete(kind, key)
            return None
        self._touch(kind, key)
        return payload

    def get_many(self, kind: str, keys: Sequence[str]) -> dict[str, Any]:
        """Return every cached artifact among ``keys``, skipping misses.

        One query instead of one per key, which is what makes warm loads cheap
        when a history has hundreds of games.
        """
        if not keys:
            return {}
        found: dict[str, Any] = {}
        version = code_version(kind)
        chunk = 500
        for start in range(0, len(keys), chunk):
            batch = list(keys[start : start + chunk])
            placeholders = ",".join("?" * len(batch))
            rows = self._conn.execute(
                f"SELECT key, payload FROM derived WHERE kind = ? AND code_version = ? "
                f"AND key IN ({placeholders})",
                (kind, version, *batch),
            ).fetchall()
            for key, payload in rows:
                try:
                    found[str(key)] = json.loads(payload)
                except json.JSONDecodeError:
                    continue
        if found:
            self._touch_many(kind, list(found))
        return found

    def put(self, kind: str, key: str, payload: Any) -> None:
        """Store one artifact, replacing any entry under the same key."""
        blob = json.dumps(payload, separators=(",", ":"), default=str)
        now = time.time()
        self._conn.execute(
            "INSERT INTO derived (kind, key, code_version, payload, bytes, created_at, hit_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(kind, key, code_version) DO UPDATE SET "
            "payload = excluded.payload, bytes = excluded.bytes, hit_at = excluded.hit_at",
            (kind, key, code_version(kind), blob, len(blob), now, now),
        )
        self._conn.commit()

    def put_many(self, kind: str, items: dict[str, Any]) -> None:
        """Store several artifacts in one transaction."""
        if not items:
            return
        now = time.time()
        version = code_version(kind)
        rows = []
        for key, payload in items.items():
            blob = json.dumps(payload, separators=(",", ":"), default=str)
            rows.append((kind, key, version, blob, len(blob), now, now))
        self._conn.executemany(
            "INSERT INTO derived (kind, key, code_version, payload, bytes, created_at, hit_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(kind, key, code_version) DO UPDATE SET "
            "payload = excluded.payload, bytes = excluded.bytes, hit_at = excluded.hit_at",
            rows,
        )
        self._conn.commit()

    def delete(self, kind: str, key: str) -> None:
        """Drop one artifact across every code version."""
        self._conn.execute("DELETE FROM derived WHERE kind = ? AND key = ?", (kind, key))
        self._conn.commit()

    def total_bytes(self) -> int:
        """Sum of stored payload sizes."""
        row = self._conn.execute("SELECT COALESCE(SUM(bytes), 0) FROM derived").fetchone()
        return int(row[0]) if row else 0

    def purge_stale_versions(self) -> int:
        """Drop entries whose ``code_version`` no longer matches current code.

        Only for kinds this process knows about, so a rollback to older code can
        still find its own entries until they are evicted by size.
        """
        removed = 0
        for kind in _KIND_SOURCES:
            cursor = self._conn.execute(
                "DELETE FROM derived WHERE kind = ? AND code_version != ?",
                (kind, code_version(kind)),
            )
            removed += cursor.rowcount or 0
        if removed:
            self._conn.commit()
            self._log.info("Purged %d stale derived artifact(s)", removed)
        return removed

    def evict_to_budget(self) -> int:
        """Evict least-recently-hit entries until under the byte budget."""
        total = self.total_bytes()
        if total <= self._max_bytes:
            return 0
        removed = 0
        for key_row in self._conn.execute(
            "SELECT kind, key, code_version, bytes FROM derived ORDER BY hit_at ASC"
        ).fetchall():
            if total <= self._max_bytes:
                break
            self._conn.execute(
                "DELETE FROM derived WHERE kind = ? AND key = ? AND code_version = ?",
                (key_row[0], key_row[1], key_row[2]),
            )
            total -= int(key_row[3])
            removed += 1
        if removed:
            self._conn.commit()
            self._log.info("Evicted %d derived artifact(s) to stay under budget", removed)
        return removed

    def _touch(self, kind: str, key: str) -> None:
        self._conn.execute(
            "UPDATE derived SET hit_at = ? WHERE kind = ? AND key = ? AND code_version = ?",
            (time.time(), kind, key, code_version(kind)),
        )
        self._conn.commit()

    def _touch_many(self, kind: str, keys: Sequence[str]) -> None:
        now = time.time()
        version = code_version(kind)
        self._conn.executemany(
            "UPDATE derived SET hit_at = ? WHERE kind = ? AND key = ? AND code_version = ?",
            [(now, kind, key, version) for key in keys],
        )
        self._conn.commit()
