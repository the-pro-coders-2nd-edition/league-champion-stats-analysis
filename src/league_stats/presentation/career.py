"""Shape a Career snapshot into the props the generated templates expect."""

from __future__ import annotations

from typing import Any, Final

from league_stats.analysis.career.engine import CareerBlockState, CareerSnapshot
from league_stats.analysis.career.models import (
    CLEAR_BAR,
    GOALS_PER_BLOCK,
    HOLD_RATIO,
    SETUP_CLEAR_BAR,
    WINDOW,
    hold_bar,
)
from league_stats.analysis.career.steps import (
    ANCHOR_QUANTILE,
    BASELINE_GAMES,
    MAX_STEP_STRETCH,
)
from league_stats.analysis.career.tracks import track_spec
from league_stats.presentation.tones import career_count, career_node

# Every number here is derived from the constants that actually drive the engine.
# This panel drifted badly once -- it still described a median-anchored target long
# after that changed -- and tests/test_career_rules_copy.py now pins it.
_ANCHOR_PCT: Final[int] = int(ANCHOR_QUANTILE * 100)
_MIRROR_PCT: Final[int] = int((1 - ANCHOR_QUANTILE) * 100)
_STRETCH_PCT: Final[int] = int(MAX_STEP_STRETCH * 100)

CAREER_RULES: Final[tuple[dict[str, str], ...]] = (
    {
        "key": "Scope",
        "value": "all ranked games",
        "note": (
            "Solo/Duo and Flex together. Career does not follow the queue or "
            "game-window filters above, so it shows the same ladder whichever "
            "you pick."
        ),
    },
    {
        "key": "Blocks",
        "value": f"one category, up to {GOALS_PER_BLOCK} goals",
        "note": (
            "A block is a category — Survival, Vision, Objectives and so on — and "
            "its goals are the steps that category says you most need right now. "
            "Two players weak at the same category get different goals."
        ),
    },
    {
        "key": "Window",
        "value": f"{WINDOW} games",
        "note": (
            f"Progress is measured over your last {WINDOW} ranked games, and a new "
            "block only counts games played after it appeared."
        ),
    },
    {
        "key": "Clear bar",
        "value": f"{CLEAR_BAR} of {WINDOW}",
        "note": (
            f"Hit the target in {CLEAR_BAR} games of the window and the goal "
            f"clears. Goals about one narrow moment ask {SETUP_CLEAR_BAR} of "
            f"{WINDOW} instead. Every goal in the live block counts at once, so a "
            "strong run can clear the whole block in one window."
        ),
    },
    {
        "key": "Hold bar",
        "value": f"{int(HOLD_RATIO * 100)}% of the clear bar",
        "note": (
            f"Fall under {hold_bar(CLEAR_BAR)} of {WINDOW} and a cleared goal is "
            "revoked — the block cannot complete until you earn it back."
        ),
    },
    {
        "key": "Target",
        "value": f"P{_ANCHOR_PCT} of your last {BASELINE_GAMES} games, +{_STRETCH_PCT}%",
        "note": (
            f"A stepped target is anchored at a level you already reach in most "
            f"games, then stretched {_STRETCH_PCT}%; lower-is-better goals mirror "
            f"it at P{_MIRROR_PCT} minus {_STRETCH_PCT}%. Peer data can only pull a "
            "target down, never up. Some goals need no target at all — even gold at "
            "10 minutes, or no greed deaths."
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


def career_scope_view() -> dict[str, Any]:
    """The view a queue-filtered slice renders instead of the ladder.

    Career is one ladder over every ranked game, so a Solo/Duo or Flex slice has
    no ladder of its own to show. ``tracks_all_ranked`` tells the report to
    explain that rather than claim the player has no ladder at all.
    """
    return {**empty_career_view(), "tracks_all_ranked": True}


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
        node["why"] = goal.rung.why
        node["note"] = ""
        node["last"] = index == len(block.goals) - 1
        items.append(node)
    done = sum(1 for state in block.display_states if state in {"Cleared", "At risk"})
    revoked = "Revoked" in block.display_states
    return {
        "slot": block.slot,
        "position": f"Block {block.slot + 1}",
        "name": _track_name(block.track_key),
        "metric": _track_metric(block.track_key),
        "is_active": True,
        "is_locked": False,
        "opacity": "1",
        "tone": "bad" if revoked else "warn",
        "state_label": f"Live · {done} of {len(block.goals)} cleared",
        "unlock": "",
        "goals": items,
        "steps": [],
    }


def _locked_block(block: CareerBlockState, previous_name: str) -> dict[str, Any]:
    return {
        "slot": block.slot,
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
