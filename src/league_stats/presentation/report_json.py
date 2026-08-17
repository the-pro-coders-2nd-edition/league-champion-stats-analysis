"""Serialize a report render context (built for Jinja) into a clean JSON payload."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

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
        return str(value)
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
