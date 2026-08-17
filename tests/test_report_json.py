from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from league_stats.presentation.report_json import context_to_json


@dataclass
class _FakeScoreComponent:
    label: str
    value: float


def test_context_to_json_unwraps_pre_serialized_json_strings() -> None:
    context = {
        "champion": "Viktor",
        "report_views_json": json.dumps({"solo": {"total_games": 42}}),
    }

    result = context_to_json(context)

    assert result["champion"] == "Viktor"
    assert "report_views_json" not in result
    assert result["report_views"] == {"solo": {"total_games": 42}}


def test_context_to_json_converts_dataclasses_to_dicts() -> None:
    context = {"score_components": [_FakeScoreComponent(label="cs", value=0.8)]}

    result = context_to_json(context)

    assert result["score_components"] == [{"label": "cs", "value": 0.8}]


def test_context_to_json_converts_paths_to_strings() -> None:
    context = {"stylesheet": Path("static/report.css")}

    result = context_to_json(context)

    assert result["stylesheet"] == "static/report.css"


def test_context_to_json_raises_on_unserializable_value() -> None:
    context = {"bad": object()}

    with pytest.raises(TypeError, match="bad"):
        context_to_json(context)


def test_context_to_json_output_is_actually_json_dumpable() -> None:
    context = {
        "champion": "Viktor",
        "game_review_json": json.dumps({"available": True}),
        "score_components": [_FakeScoreComponent(label="cs", value=0.8)],
    }

    result = context_to_json(context)

    json.dumps(result)  # must not raise
