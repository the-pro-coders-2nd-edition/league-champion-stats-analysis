"""Shared Mongo connection-string helper.

Previously duplicated byte-for-byte in `web/worker.py`, `peers/service.py`
and `analysis/peer/benchmark_cache.py` -- all three parse the same `MONGO_URI`/
`RUNNER_MONGO_URI` shape to get a database name. Consolidated here so the
three call sites can't drift again.
"""

from __future__ import annotations

from pymongo import uri_parser as mongo_uri_parser


def db_name_from_uri(mongo_uri: str) -> str:
    """Extract the database name from a Mongo connection URI.

    Uses pymongo's own URI parser rather than a naive `rsplit`, which breaks
    on query params (`?retryWrites=true`) or a bare host with no db path.
    """
    return mongo_uri_parser.parse_uri(mongo_uri).get("database") or "league_stats"
