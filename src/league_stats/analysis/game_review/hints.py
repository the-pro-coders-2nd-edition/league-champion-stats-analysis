"""Tooltip and label helpers for Game Review UI."""

from __future__ import annotations

from league_stats.core.champions import normalize_role
from league_stats.presentation.ui_icons import METRIC_TOOLTIPS, tooltip_for_label

GAME_REVIEW_KEY_STATS: dict[str, tuple[str, str]] = {
    "gd10": ("Gold diff @10", "Gold diff @10"),
    "gd15": ("Gold diff @15", "Your total gold minus lane opponent's at 15 minutes."),
    "deaths": ("Deaths", "Deaths/game"),
    "deaths_pre14": ("Deaths pre-14", "Deaths pre-14"),
    "dpm": ("DPM", "DPM"),
    "kill_participation": ("Kill participation", "Kill participation"),
    "damage_share": ("Damage share", "Damage share"),
    "gold_share": ("Gold share", "Gold share"),
    "vspm": ("Vision/min", "Vision/min"),
    "control_wards": ("Control wards", "Control wards"),
    "objectives_present_rate": ("Objective presence", "Objective presence"),
    "solo_deaths": ("Solo deaths", "Solo deaths"),
    "greed_deaths": ("Greed deaths", "Greed deaths"),
    "fights_disadvantaged": ("Disadvantaged fights", "Disadvantaged fights"),
}

TOP_GAME_REVIEW_KEY_STATS: dict[str, tuple[str, str]] = {
    "objectives_split_push_rate": ("Split push", "Split pushing"),
    "objectives_defend_split_rate": ("Defending split", "Defending split"),
    "unproductive_absence_rate": ("Unproductive absence", "Unproductive absence"),
    "structure_tower_damage": ("Tower damage", "Tower damage"),
    "towers_taken": ("Towers taken", "Towers taken"),
}

TOP_GAME_REVIEW_KEY_STAT_DIRECTIONS: dict[str, str] = {
    "objectives_split_push_rate": "higher",
    "objectives_defend_split_rate": "higher",
    "unproductive_absence_rate": "lower",
    "structure_tower_damage": "higher",
    "towers_taken": "higher",
}

TOP_GAME_REVIEW_KEY_STAT_GROUP: tuple[str, str, tuple[str, ...]] = (
    "Split push",
    "lucide:castle",
    (
        "objectives_split_push_rate",
        "objectives_defend_split_rate",
        "unproductive_absence_rate",
        "structure_tower_damage",
        "towers_taken",
    ),
)

# higher = more is better for the player; lower = fewer is better.
GAME_REVIEW_KEY_STAT_DIRECTIONS: dict[str, str] = {
    "gd10": "higher",
    "gd15": "higher",
    "dpm": "higher",
    "kill_participation": "higher",
    "damage_share": "higher",
    "gold_share": "higher",
    "vspm": "higher",
    "control_wards": "higher",
    "objectives_present_rate": "higher",
    "deaths": "lower",
    "deaths_pre14": "lower",
    "solo_deaths": "lower",
    "greed_deaths": "lower",
    "fights_disadvantaged": "lower",
}

# Ordered groups for the Overview tab (label, iconify id, metric keys).
GAME_REVIEW_KEY_STAT_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Lane", "lucide:map", ("gd10", "gd15")),
    (
        "Combat",
        "lucide:swords",
        ("dpm", "kill_participation", "damage_share", "gold_share", "fights_disadvantaged"),
    ),
    ("Survival", "lucide:skull", ("deaths", "deaths_pre14", "solo_deaths", "greed_deaths")),
    (
        "Vision & objectives",
        "lucide:eye",
        ("vspm", "control_wards", "objectives_present_rate"),
    ),
)

OBJECTIVE_DEAD_SETUP_LABEL = "Died in setup window (45–10s)"

OBJECTIVE_COLUMN_TOOLTIPS: dict[str, str] = {
    "Taken": "Whether your team secured this epic monster.",
    "Present": "You were within range when the objective was taken.",
    "Split pushing": (
        "Absent at the pit but applying offensive sidelane tower pressure. "
        "Only counts after 14 minutes, or earlier once a tower has fallen on your lane."
    ),
    "Defending split": "Holding your tower under enemy split pressure while the team contests.",
    "Absent without pressure": "Absent at the objective without sidelane pressure or a defend.",
    "Traded for": "Your team traded this objective for structures on the map.",
    "Held tower": "You successfully held a tower during this objective contest.",
    "Bad trade": "Structures lost outweighed the objective value.",
    "Manpower at pit": "Ally vs enemy count near the objective pit at take time.",
    "TP available": "You had Teleport in range but stayed on your sidelane assignment.",
    "No TP": "No Teleport available — absence may be excused by summoners.",
    OBJECTIVE_DEAD_SETUP_LABEL: (
        "You died in the 45–10s setup window before this objective was taken "
        "(caught before the fight; last-10s teamfight deaths are excluded)."
    ),
    "Wards before": METRIC_TOOLTIPS["Wards before"],
    "Wards during setup": (
        "Wards you placed within 2 minutes before the objective was taken, "
        "only shown when you were present at the pit."
    ),
}


def _tooltip(label: str) -> str | None:
    return tooltip_for_label(label) or METRIC_TOOLTIPS.get(label)


def game_review_key_stats_for_role(role: str) -> dict[str, tuple[str, str]]:
    """Key stats shown in Game Review overview, including TOP split-push metrics."""
    stats = dict(GAME_REVIEW_KEY_STATS)
    if normalize_role(role) == "TOP":
        stats.update(TOP_GAME_REVIEW_KEY_STATS)
    return stats


def game_review_key_stat_directions_for_role(role: str) -> dict[str, str]:
    """Whether higher or lower is better for each Game Review key stat."""
    directions = dict(GAME_REVIEW_KEY_STAT_DIRECTIONS)
    if normalize_role(role) == "TOP":
        directions.update(TOP_GAME_REVIEW_KEY_STAT_DIRECTIONS)
    return directions


def game_review_key_stat_groups_for_role(
    role: str,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Ordered stat groups for the Game Review overview tab."""
    groups: list[tuple[str, str, tuple[str, ...]]] = list(GAME_REVIEW_KEY_STAT_GROUPS)
    if normalize_role(role) == "TOP":
        groups.append(TOP_GAME_REVIEW_KEY_STAT_GROUP)
    return tuple(groups)


def game_review_tooltips(*, role: str = "MIDDLE") -> dict[str, object]:
    """Tooltip map embedded in the report for Game Review panels."""
    score = {
        "Game score": (
            "Personal performance vs your baseline for this single game (0–100, letter tier). "
            "Independent of win/loss. Expand a category to see the metrics behind it."
        ),
    }
    key_stats: dict[str, str] = {}
    key_stats_labels: dict[str, str] = {}
    for column, (label, tooltip_key) in game_review_key_stats_for_role(role).items():
        key_stats_labels[column] = label
        hint = _tooltip(tooltip_key) or _tooltip(label)
        if hint:
            key_stats[column] = hint
    return {
        "score": score,
        "key_stats": key_stats,
        "key_stats_labels": key_stats_labels,
        "key_stats_groups": [
            {"label": label, "iconify": iconify, "keys": list(keys)}
            for label, iconify, keys in game_review_key_stat_groups_for_role(role)
        ],
        "objectives": OBJECTIVE_COLUMN_TOOLTIPS,
        "key_moments": {
            "interpolation": (
                "Drag the scrubber to step through minute snapshots from just before "
                "the action through just after. Riot records all ten players once per "
                "minute. Objective icons are bright when up, dim when taken."
            ),
            "gold_given": (
                "Kill bounty + shutdown gold from the Riot kill event — the gold pot "
                "awarded for your death before assist sharing."
            ),
        },
    }
