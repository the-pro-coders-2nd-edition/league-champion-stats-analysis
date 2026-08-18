"""Shape a Career snapshot into the props the generated templates expect."""

from __future__ import annotations

from typing import Any, Final

from league_stats.analysis.career.engine import CareerBlockState, CareerSnapshot
from league_stats.analysis.career.models import GOALS_PER_BLOCK, WINDOW, hold_bar
from league_stats.analysis.career.tracks import track_spec
from league_stats.presentation.tones import career_count, career_node

CAREER_RULES: Final[tuple[dict[str, str], ...]] = (
    {
        "key": "Window",
        "value": "20 games",
        "note": (
            "Every goal reads the same rolling window, and a new block only "
            "counts games played after it appeared."
        ),
    },
    {
        "key": "Clear bar",
        "value": "15 of 20",
        "note": (
            "Hit the target in 15 games of the window and the goal clears. All "
            "three goals count at once, so a strong run clears the block in one "
            "window."
        ),
    },
    {
        "key": "Hold bar",
        "value": "75% of the clear bar",
        "note": (
            "Drop below 11 of 20 and a cleared goal is revoked — the block "
            "cannot complete until you re-earn it."
        ),
    },
    {
        "key": "Target",
        "value": "your p50 toward peer p75",
        "note": (
            "Each rung is computed from your own distribution and capped at a "
            "reachable step, so clearing a block moves the next one up."
        ),
    },
)

_LEGEND_SOURCE: Final[tuple[tuple[str, int, int, str], ...]] = (
    (
        "Locked",
        0,
        15,
        "Not measured yet — this block sits to the right of the live one, so "
        "nothing in it counts until it becomes live.",
    ),
    (
        "In progress",
        9,
        15,
        "Live and counting. The ring fills as you hit the target in more of the "
        "last 20 games; reach the clear bar of 15 and it clears.",
    ),
    (
        "At risk",
        12,
        15,
        "Cleared, but drifting. You are still above the hold bar of 11 of 20, so it "
        "stays cleared — one or two bad games and it is revoked.",
    ),
    (
        "Cleared",
        16,
        15,
        "Held at or above the clear bar across the current window. Counts toward "
        "the block; the other goals in it are counting in parallel.",
    ),
    (
        "Revoked",
        8,
        15,
        "Was cleared, then fell below the hold bar. It goes back in play and the "
        "block cannot complete until you earn it again.",
    ),
)


def state_class(state: str) -> str:
    """CSS-safe slug for a goal state (``At risk`` → ``at-risk``)."""
    return state.lower().replace(" ", "-")


def _node(state: str, hit: int, need: int, *, window: int) -> dict[str, Any]:
    node = career_node(state, hit, need)
    return {
        "state": state,
        "state_class": state_class(state),
        "tone": node["tone"],
        "pct": node["pct"],
        "mark": "✓" if state == "Cleared" else "",
        "count": career_count(state, hit, window),
        "hit": hit,
        "need": need,
        "hold": hold_bar(need),
    }


def empty_career_view() -> dict[str, Any]:
    """The view a report renders when there is no ladder to show yet."""
    return {
        "has_career": False,
        "blocks": [],
        "widget": [],
        "rules": list(CAREER_RULES),
        "legend": _legend(WINDOW),
        "congrats": None,
    }


def _legend(window: int) -> list[dict[str, Any]]:
    return [
        {**_node(state, hit, need, window=window), "name": state, "text": text}
        for state, hit, need, text in _LEGEND_SOURCE
    ]


def _track_name(track_key: str) -> str:
    spec = track_spec(track_key)
    return spec.name if spec is not None else track_key.replace("_", " ").capitalize()


def _track_metric(track_key: str) -> str:
    spec = track_spec(track_key)
    return spec.metric_label if spec is not None else ""


def _active_block(block: CareerBlockState, *, window: int) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for index, goal in enumerate(block.goals):
        state = block.display_states[index]
        node = _node(state, block.hits[index], goal.rung.need, window=window)
        node["text"] = goal.rung.text
        node["note"] = ""
        node["last"] = index == len(block.goals) - 1
        items.append(node)
    done = sum(1 for state in block.display_states if state in {"Cleared", "At risk"})
    revoked = "Revoked" in block.display_states
    return {
        "position": f"Block {block.slot + 1}",
        "name": _track_name(block.track_key),
        "metric": _track_metric(block.track_key),
        "is_active": True,
        "is_locked": False,
        "opacity": "1",
        "tone": "bad" if revoked else "warn",
        "state_label": f"Live · {done} of {GOALS_PER_BLOCK} cleared",
        "unlock": "",
        "goals": items,
        "steps": [],
    }


def _locked_block(block: CareerBlockState, previous_name: str) -> dict[str, Any]:
    return {
        "position": f"Block {block.slot + 1}",
        "name": _track_name(block.track_key),
        "metric": _track_metric(block.track_key),
        "is_active": False,
        "is_locked": True,
        "opacity": "0.6",
        "tone": "flat",
        "state_label": "Locked",
        "unlock": f"Opens when {previous_name} is complete.",
        "goals": [],
        "steps": [goal.rung.text for goal in block.goals],
    }


def build_career_view(snapshot: CareerSnapshot, *, window: int = WINDOW) -> dict[str, Any]:
    """Blocks, sidebar widget, rules, legend and banner for the report templates."""
    if not snapshot.blocks:
        return empty_career_view()

    blocks: list[dict[str, Any]] = []
    for position, block in enumerate(snapshot.blocks):
        if position == 0:
            blocks.append(_active_block(block, window=window))
            continue
        blocks.append(_locked_block(block, _track_name(snapshot.blocks[position - 1].track_key)))

    live = blocks[0]
    track_name = live["name"]
    widget = [
        {**item, "note": f"{track_name} · {item['count']}"}
        for item in live["goals"]
    ]

    congrats = None
    if snapshot.pending_congrats:
        retired = _track_name(snapshot.pending_congrats)
        next_name = blocks[0]["name"]
        congrats = {
            "title": f"{retired} complete",
            "body": (
                "All three goals held across a full 20-game window. This block "
                f"retires, {next_name} moves left and becomes live, and a new "
                "queued block is generated from your numbers as they stand now."
            ),
        }

    return {
        "has_career": True,
        "blocks": blocks,
        "widget": widget,
        "rules": list(CAREER_RULES),
        "legend": _legend(window),
        "congrats": congrats,
    }
