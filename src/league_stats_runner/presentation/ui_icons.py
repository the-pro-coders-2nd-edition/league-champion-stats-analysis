"""Map dashboard labels to Iconify icon ids (Lucide + game-icons)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Internal keys -> Iconify ids (https://icon-sets.iconify.design/)
ICONIFY_ICONS: dict[str, str] = {
    "coin": "lucide:coins",
    "minion": "lucide:wheat",
    "skull": "lucide:skull",
    "eye": "lucide:eye",
    "ward": "lucide:shield",
    "trophy": "lucide:trophy",
    "combat": "lucide:swords",
    "flame": "lucide:flame",
    "clock": "lucide:clock",
    "dragon": "game-icons:dragon-head",
    "teamfight": "lucide:users",
    "tower": "lucide:castle",
    "target": "lucide:target",
    "roam": "lucide:footprints",
    "recall": "lucide:home",
    "level": "lucide:trending-up",
    "lane": "lucide:map",
    "chart": "lucide:bar-chart-2",
    "wand": "lucide:wand-sparkles",
    "items": "lucide:wand-sparkles",
    "rune": "lucide:sparkles",
    "bulb": "lucide:lightbulb",
    "kp": "lucide:users-round",
}

# Internal keys backed by local PNG assets instead of Iconify.
ICON_ASSET_FILES: dict[str, str] = {
    "cs": "minions.png",
    "tower": "tower.png",
}

METRIC_ICONS: dict[str, str] = {
    # Overview
    "Win rate": "trophy",
    "KDA": "combat",
    "DPM": "flame",
    "CC/min": "target",
    "CS/min": "cs",
    "Damage share": "flame",
    "Deaths/game": "skull",
    "Vision/min": "eye",
    "Game length": "clock",
    "Split push": "tower",
    "Tower damage": "tower",
    "At objective": "target",
    "Split pushing": "tower",
    "Defending split": "tower",
    "Unproductive absence": "skull",
    "Split balance": "chart",
    "Towers taken": "tower",
    "Kill participation": "kp",
    "Objective presence": "target",
    "Gold @10": "coin",
    "Early ganks": "roam",
    "Gank assists": "teamfight",
    "Kill participation @15": "kp",
    "Roam conversions": "roam",
    "Dist to ADC": "lane",
    "Grouped share": "teamfight",
    "Vision/min @10": "eye",
    "Bot lane presence": "lane",
    "Assists/game": "teamfight",
    "Healing/min": "teamfight",
    "Shielding/min": "teamfight",
    "Caught while farming": "skull",
    "Overextended roams": "roam",
    "Facechecks / deep wards": "ward",
    # Lane
    "Gold diff @10": "coin",
    "CS diff @10": "cs",
    "XP diff @10": "level",
    "Lane win rate": "trophy",
    "WR when ahead @10": "trophy",
    "WR when behind @10": "trophy",
    "Deaths pre-14": "skull",
    "Gank deaths (lane)": "skull",
    "Under own tower (lane)": "tower",
    "Under enemy tower (lane)": "tower",
    "Roams pre-15": "roam",
    # Positioning
    "Grouped with team": "teamfight",
    "Solo on map": "roam",
    "Side-lane time": "lane",
    "Allies nearby": "teamfight",
    "Avg teammate dist": "roam",
    "Dist to top": "lane",
    "Dist to jungle": "roam",
    "Dist to mid": "lane",
    "Dist to bot": "lane",
    "Dist to support": "teamfight",
    # Economy
    "GPM": "coin",
    "Gold share": "coin",
    "Damage per gold": "flame",
    "Unspent gold/recall": "coin",
    "First recall": "recall",
    "Time dead/game": "clock",
    # Vision
    "Vision score": "eye",
    "Control wards": "ward",
    "Control ward lifespan": "ward",
    "Vision/min in wins": "eye",
    "Vision/min in losses": "eye",
    # Deaths
    "Total deaths": "skull",
    "Solo deaths": "skull",
    "Greed deaths": "skull",
    "Side-lane deaths": "skull",
    "Deaths before objectives": "target",
    "Gold at death": "coin",
    "Outnumbered deaths": "skull",
    "Avg death minute": "clock",
    # Teamfights
    "Fights detected": "teamfight",
    "Participation": "kp",
    "Fight win rate": "trophy",
    "Damage/fight": "flame",
    "Death rate in fights": "skull",
    "Forward positioning": "teamfight",
    "Unspent gold/fight": "coin",
    "Advantaged fights": "kp",
    "Disadvantaged fights": "skull",
    "WR advantaged fights": "trophy",
    "WR disadvantaged fights": "trophy",
    # Peer / score categories
    "Laning": "lane",
    "Early game": "roam",
    "Setup": "roam",
    "Economy": "coin",
    "Fight": "flame",
    "Farming": "cs",
    "Survival": "skull",
    "Damage": "flame",
    "CC impact": "target",
    "Utility": "teamfight",
    "Impact": "kp",
    "Map control": "target",
    "Clear @10": "cs",
    "Early ganks": "roam",
    "Vision": "eye",
    "Objectives": "target",
    "Strengths": "trophy",
    "Weaknesses": "skull",
    "Kill participation": "kp",
}

METRIC_TOOLTIPS: dict[str, str] = {
    # Overview
    "Win rate": "Wins divided by total games in the selected window.",
    "KDA": "Average (kills + assists) ÷ deaths per game. Deaths are floored at 1.",
    "DPM": "Average damage to champions per minute: total champ damage ÷ game length.",
    "CC/min": "Average crowd-control time per minute: total seconds CCing enemies ÷ game length.",
    "CS/min": "Average creep score per minute (lane + jungle minions from the match summary).",
    "Damage share": "Average share of your team's total damage to champions each game.",
    "Deaths/game": "Average deaths per game in the window.",
    "Vision/min": "Average vision score per minute (Riot vision score ÷ game length).",
    "Game length": "Average match duration in minutes.",
    "Split push": (
        "Share of epic objectives where you applied offensive sidelane pressure "
        "instead of joining the pit (post-14 min or after a lane tower falls)."
    ),
    "Tower damage": "Average damage dealt to turrets per game.",
    "At objective": "Share of epic objectives where you were present at the pit.",
    "Split pushing": (
        "Offensive sidelane pressure during an objective window — absent at the pit "
        "but threatening structures on your assignment."
    ),
    "Defending split": (
        "Holding your tower under enemy split pressure while the team contests an objective."
    ),
    "Unproductive absence": (
        "Share of objectives where you were absent without sidelane pressure, a defend, "
        "or a successful trade."
    ),
    "Split balance": (
        "Offensive split-push rate minus defend-split rate — positive means more pressure "
        "than babysitting towers."
    ),
    "Towers taken": "Average enemy towers you helped destroy per game (present or last-hit).",
    # Lane
    "Gold diff @10": "Your total gold minus your lane opponent's at the 10-minute timeline frame, averaged across games.",
    "CS diff @10": "Your CS minus your lane opponent's at minute 10 (lane + jungle minions).",
    "XP diff @10": "Your XP minus your lane opponent's at the 10-minute timeline frame.",
    "Lane win rate": "Share of games where gold diff @10 is positive.",
    "WR when ahead @10": "Win rate in games where you were ahead in gold at 10 minutes.",
    "WR when behind @10": "Win rate in games where gold diff @10 was negative at 10 minutes.",
    "Deaths pre-14": "Average deaths before minute 14 (end of the laning phase).",
    "Gank deaths (lane)": "Deaths before 14 min in a lane where the killer or an assist was not your lane opponent (e.g. jungler gank).",
    "Under own tower (lane)": "Deaths during the laning phase while near your lane tower.",
    "Under enemy tower (lane)": "Deaths during the laning phase while near the enemy lane tower.",
    "Roams pre-15": "Average roams detected before minute 15 (timeline position shifts away from your lane).",
    # Positioning
    "Grouped with team": (
        "Share of mid/late frames (after 14 min, excluding base) where at least two allies "
        "are within about one screen (≈3000, or ~7–8 flashes)."
    ),
    "Solo on map": (
        "Share of those frames with no allies within about one screen (≈3000, or ~7–8 flashes)."
    ),
    "Side-lane time": "Share of mid/late frames spent in a side lane (top or bot, not mid).",
    "Allies nearby": (
        "Average number of allies within about one screen (≈3000) per mid/late frame."
    ),
    "Avg teammate dist": (
        "Mean distance to all teammates per mid/late frame. Shown as X.Xk with screen "
        "landmarks (1 screen ≈ 3000, Flash ≈ 400). Lower means closer."
    ),
    # Economy
    "GPM": "Gold per minute: total gold earned ÷ game length.",
    "Gold share": "Your gold as a share of team total gold each game.",
    "Damage per gold": "Damage to champions divided by gold earned, averaged per game.",
    "Unspent gold/recall": "Average gold banked on the timeline frame before each inferred recall (burst of item purchases).",
    "First recall": "Average game minute of your first inferred recall.",
    "Time dead/game": "Average seconds spent on death timers per game.",
    # Vision
    "Vision score": "Average Riot vision score per game.",
    "Control wards": "Average control wards bought per game.",
    "Control ward lifespan": "Average seconds each control ward stayed alive until cleared or game end.",
    "Vision/min in wins": "Vision score per minute averaged over wins only.",
    "Vision/min in losses": "Vision score per minute averaged over losses only.",
    # Objectives
    "Died in setup window (45–10s)": (
        "Share of epic objectives where you died in the 45–10s setup window before the take "
        "(caught before the fight; deaths in the last 10s are excluded as teamfight deaths)."
    ),
    "Wards before": "Average wards you placed in the 2 minutes before each objective take. Any ward type counts; map location is not filtered.",
    # Deaths
    "Total deaths": "Total death count across all games in the window (not an average).",
    "Solo deaths": (
        "Share of deaths with no allies within roughly three-quarters of a screen "
        "(≈2200, or ~5–6 flashes)."
    ),
    "Greed deaths": "Share of deaths shortly after deep side-lane pushing without nearby allies.",
    "Side-lane deaths": "Share of deaths while isolated in a side lane after minute 14.",
    "Before dragon": (
        "Share of deaths in the 60–10s setup window before a dragon take "
        "(excludes the last 10s, which are usually the objective fight)."
    ),
    "Deaths before objectives": (
        "Share of deaths in the 60–10s setup window before a dragon, elder, or baron take "
        "(excludes the last 10s, which are usually the objective fight)."
    ),
    "Gold at death": "Average gold in your inventory at the moment of death.",
    "Outnumbered deaths": "Share of deaths where nearby enemies outnumbered nearby allies.",
    "Avg death minute": "Mean game minute when you died.",
    # Teamfights
    "Fights detected": (
        "Clusters of at least three kills within 25 seconds and a bit more than one screen "
        "(≈4000) of each other."
    ),
    "Participation": (
        "Share of detected fights where you killed, assisted, died, or were within about "
        "one screen (≈3000) of the fight."
    ),
    "Fight win rate": "Share of joined fights your team won (more ally than enemy kills in the cluster).",
    "Damage/fight": "Average damage to champions you dealt in fights you joined.",
    "Death rate in fights": "Share of joined fights where you died.",
    "Forward positioning": "How far forward you stand when fights start compared with your teammates; positive means you're in front.",
    "Unspent gold/fight": "Average gold in your inventory when a joined fight started.",
    "Advantaged fights": "Joined fights where your team had more nearby champions than the enemy.",
    "Disadvantaged fights": "Joined fights where the enemy had more nearby champions.",
    "WR advantaged fights": "Win rate in joined fights where you had a manpower advantage nearby.",
    "WR disadvantaged fights": "Win rate in joined fights where you were outnumbered nearby.",
    # Improvement score categories
    "Laning": (
        "0–100 category score from gold/CS diff @10 and early-lane deaths "
        "(role-calibrated ingredient bands)."
    ),
    "Early game": (
        "0–100 category score from clear speed @10, early ganks, and pre-14 deaths."
    ),
    "Setup": (
        "0–100 category score from early roams, bot-lane presence, and pre-14 deaths."
    ),
    "Economy": (
        "0–100 category score from CS @10 (laners), gold share, gold usage before recalls, "
        "and first-item timing — ingredients vary by role."
    ),
    "Fight": (
        "0–100 category score from damage/CC share, kill participation, and fight "
        "presence/win rate — ingredients vary by role."
    ),
    "Farming": "0–100 score from CS @10 against a role-specific benchmark band.",
    "Survival": "0–100 score from deaths normalized to a 30-minute game; fewer deaths score higher.",
    "Damage": "0–100 score from your average share of team damage to champions.",
    "Vision": (
        "0–100 category score from vision score per minute and control-ward buys "
        "against role benchmarks."
    ),
    "Objectives": "0–100 category score from your presence rate at epic monster takes.",
    "Utility": (
        "0–100 composite of CC/min, damage share, damage taken share, and ally "
        "healing/shielding per minute vs support benchmarks. Low CC/heal/shield "
        "output is omitted to avoid noise."
    ),
    "Impact": "0–100 score from kill participation vs role benchmarks.",
    "Map control": "0–100 score from objective presence at epic monster takes.",
    "Clear @10": "0–100 score from jungle CS @10 vs role clear-speed benchmarks.",
    "Early ganks": "0–100 score from successful early ganks before minute 15.",
    "CC impact": "0–100 score from crowd control time per minute.",
    "Kill participation @15": "Share of your team's kills and assists before minute 15 that you took part in.",
    "Vision/min @10": "Vision score per minute over the first 10 minutes.",
    "Objective presence": "Share of epic monster takes (dragon, herald, baron) where you were near the pit.",
    "Game score": "Personal performance vs your baseline for this single game (0–100, letter tier). Independent of win/loss.",
}

DIST_TO_TOOLTIP = (
    "Average distance to your {role} teammate during mid/late game (after 14 min), "
    "from 60-second timeline frames. Shown as X.Xk (1 screen ≈ 3000, Flash ≈ 400). "
    "Lower means you stay closer."
)

SECTION_ICONS: dict[str, str] = {
    "overview": "chart",
    "score": "chart",
    "rank-peers": "chart",
    "lane": "lane",
    "economy": "coin",
    "vision": "eye",
    "deaths": "skull",
    "positioning": "roam",
    "teamfights": "teamfight",
    "objectives": "target",
    "items": "wand",
    "runes": "rune",
    "matchups": "combat",
    "graphs": "chart",
    "recommendations": "bulb",
}


def icon_for_label(label: str) -> str | None:
    """Return an internal icon key for a metric card label, if mapped."""
    return METRIC_ICONS.get(label)


def tooltip_for_label(label: str) -> str | None:
    """Return a short calculation note for a metric card label, if defined."""
    if label in METRIC_TOOLTIPS:
        return METRIC_TOOLTIPS[label]
    if label.startswith("Dist to "):
        role = label.removeprefix("Dist to ")
        return DIST_TO_TOOLTIP.format(role=role)
    return None


def icon_for_objective(kind: str) -> str | None:
    """Return an internal objective icon key when a scoreboard asset exists."""
    normalized = str(kind).strip().lower()
    if normalized in {"dragon", "elder", "baron", "herald", "grubs"}:
        return normalized
    return None


def icon_for_section(section_id: str) -> str | None:
    """Return an internal icon key for a report section id."""
    return SECTION_ICONS.get(section_id)


def icon_fields_for_label(label: str) -> dict[str, Any]:
    """Resolve icon metadata for a metric label."""
    icon_key = icon_for_label(label)
    if not icon_key:
        return {"icon": None, "iconify": None}
    if icon_key in ICON_ASSET_FILES:
        return {
            "icon": icon_key,
            "icon_asset": ICON_ASSET_FILES[icon_key],
            "iconify": None,
        }
    return {
        "icon": icon_key,
        "iconify": iconify_for_key(icon_key),
    }


def attach_metric_icon_hrefs(
    entries: list[dict[str, Any]],
    assets: Any,
    *,
    from_dir: Path,
) -> list[dict[str, Any]]:
    """Attach relative ``icon_href`` URLs for metric rows using local PNG assets."""
    for entry in entries:
        asset_file = entry.get("icon_asset")
        if not asset_file or assets is None:
            continue
        href = assets.ui_icon_href(str(asset_file), from_dir=from_dir)
        if href:
            entry["icon_href"] = href
    return entries


def iconify_for_key(icon_key: str | None) -> str | None:
    """Resolve an internal icon key to an Iconify icon id."""
    if not icon_key:
        return None
    return ICONIFY_ICONS.get(icon_key)


def with_icon(card: dict[str, Any]) -> dict[str, Any]:
    """Attach icon metadata and an optional calculation tooltip to a card dict."""
    enriched = dict(card)
    label = str(enriched.get("label", ""))
    enriched.update(icon_fields_for_label(label))
    tooltip = tooltip_for_label(label)
    if tooltip:
        enriched["tooltip"] = tooltip
    return enriched


def with_icons(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach icon metadata to every card dict."""
    return [with_icon(card) for card in cards]
