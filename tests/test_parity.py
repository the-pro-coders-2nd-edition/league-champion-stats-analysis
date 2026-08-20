"""Cross-language parity check: Python metric-color interpolation vs. its JS mirror.

`interpolate_metric_color` (Python) and `interpolateMetricColor` (JS, used by the
Svelte frontend) must agree pixel-for-pixel, including at exact .5 rounding-tie
boundaries where Python's builtin ``round()`` (banker's rounding) can disagree
with JS's ``Math.round()`` (round-half-away-from-zero). See
`metric_colors._round_half_up` for the fix that keeps them in sync.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from league_stats_runner.presentation.metric_colors import interpolate_metric_color

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "parity_cases.json"
METRIC_COLORS_JS = REPO_ROOT / "frontend" / "src" / "lib" / "metricColors.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required for JS parity checks")


def _js_interpolate_metric_color(scores: list[float]) -> list[str]:
    module_url = METRIC_COLORS_JS.resolve().as_uri()
    script = (
        f"import {{ interpolateMetricColor }} from '{module_url}';\n"
        f"const scores = {json.dumps(scores)};\n"
        "console.log(JSON.stringify(scores.map(interpolateMetricColor)));\n"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_interpolate_metric_color_matches_js_at_rounding_boundaries() -> None:
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    scores = cases["scores"]

    js_colors = _js_interpolate_metric_color(scores)
    python_colors = [interpolate_metric_color(score) for score in scores]

    assert python_colors == js_colors
