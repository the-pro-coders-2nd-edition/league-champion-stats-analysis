from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from league_stats_common.core.models import PeerComparisonResult
from league_stats_runner.presentation.report_json import context_to_json, rewrite_web_asset_hrefs


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


def test_context_to_json_converts_pydantic_models_to_dicts() -> None:
    peer_comparison = PeerComparisonResult(
        rank_label="Gold II",
        tier="GOLD",
        source="solo queue",
        peer_games=100,
        peer_players=5,
    )
    context = {"peer_comparison": peer_comparison}

    result = context_to_json(context)

    assert "peer_comparison" in result
    assert isinstance(result["peer_comparison"], dict)
    assert result["peer_comparison"]["rank_label"] == "Gold II"
    assert result["peer_comparison"]["tier"] == "GOLD"
    assert result["peer_comparison"]["source"] == "solo queue"
    # Verify it's JSON-dumpable
    json.dumps(result)


def test_context_to_json_converts_numpy_float64() -> None:
    import numpy as np
    context = {"value": np.float64(3.14)}

    result = context_to_json(context)

    assert result["value"] == 3.14
    assert isinstance(result["value"], float)
    json.dumps(result)


def test_context_to_json_converts_numpy_int64() -> None:
    import numpy as np
    context = {"value": np.int64(42)}

    result = context_to_json(context)

    assert result["value"] == 42
    assert isinstance(result["value"], int)
    json.dumps(result)


def test_context_to_json_converts_numpy_array() -> None:
    import numpy as np
    context = {"array": np.array([1, 2, 3])}

    result = context_to_json(context)

    assert result["array"] == [1, 2, 3]
    assert isinstance(result["array"], list)
    assert all(isinstance(x, int) for x in result["array"])
    json.dumps(result)


def test_rewrite_web_asset_hrefs_maps_relative_paths() -> None:
    payload = {
        "champion_icon": "../../../assets/champions/Viktor.png",
        "nested": [{"icon_href": "../../assets/ui/tower.png"}],
        "already_web": "/out/assets/champions/Ahri.png",
        "plain": "no rewrite",
    }

    result = rewrite_web_asset_hrefs(payload)

    assert result["champion_icon"] == "/out/assets/champions/Viktor.png"
    assert result["nested"][0]["icon_href"] == "/out/assets/ui/tower.png"
    assert result["already_web"] == "/out/assets/champions/Ahri.png"
    assert result["plain"] == "no rewrite"


def test_context_to_json_converts_pandas_timestamp() -> None:
    import pandas as pd
    context = {"timestamp": pd.Timestamp("2024-01-15T12:30:45")}

    result = context_to_json(context)

    assert isinstance(result["timestamp"], str)
    assert "2024-01-15" in result["timestamp"]
    json.dumps(result)
