"""Serialize a report render context (built for Jinja) into a clean JSON payload."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    from pydantic import BaseModel
except ImportError:
    BaseModel = None  # type: ignore


def _convert(value: Any) -> Any:
    if BaseModel is not None and isinstance(value, BaseModel):
        return {k: _convert(v) for k, v in value.model_dump().items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _convert(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return _convert(value.tolist())
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _convert(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_convert(v) for v in value]
    return value


def context_to_json(context: dict[str, Any]) -> dict[str, Any]:
    """Turn a Jinja render context into a plain, JSON-serializable dict.

    Keys ending in ``_json`` are assumed to already hold a JSON-encoded string
    (as produced by ``serialize_report_views_json``); they are decoded back
    into real nested objects under the key with the suffix stripped, so the
    API returns structured data instead of a double-encoded string.
    """
    result: dict[str, Any] = {}
    for key, value in context.items():
        if key.endswith("_json") and isinstance(value, str):
            result[key[: -len("_json")]] = json.loads(value) if value else None
            continue
        result[key] = _convert(value)

    try:
        json.dumps(result)
    except TypeError as exc:
        bad_keys = [k for k, v in result.items() if not _is_json_safe(v)]
        raise TypeError(f"context_to_json: unserializable value(s) at key(s) {bad_keys}") from exc

    return result


def _is_json_safe(value: Any) -> bool:
    try:
        json.dumps(value)
    except TypeError:
        return False
    return True


def _rewrite_asset_href(href: str) -> str:
    """Map on-disk relative ``../assets/...`` paths to ``/out/assets/...`` URLs."""
    if href.startswith("/out/"):
        return href
    marker = "assets/"
    index = href.find(marker)
    if index == -1:
        return href
    prefix = href[:index]
    if prefix and not all(part == ".." for part in prefix.split("/") if part):
        return href
    return "/out/" + href[index:]


def prepare_web_report_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Rewrite asset hrefs and refresh score verdicts for SPA/API consumers."""
    from league_stats.pipeline.bundles import refresh_score_verdicts_in_report

    refresh_score_verdicts_in_report(payload)
    return rewrite_web_asset_hrefs(payload)


def rewrite_web_asset_hrefs(value: Any) -> Any:
    """Recursively rewrite report asset paths for SPA/API consumers."""
    if isinstance(value, dict):
        return {key: rewrite_web_asset_hrefs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite_web_asset_hrefs(item) for item in value]
    if isinstance(value, str) and "assets/" in value:
        return _rewrite_asset_href(value)
    return value
