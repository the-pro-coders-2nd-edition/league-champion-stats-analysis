"""Matchup analysis: per-lane-opponent outcomes and recommendations."""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from league_stats_common.core.champions import champion_display_name
from league_stats_runner.presentation.tones import focus_tone, verdict_tone
from league_stats_common.utils import wilson_lower_bound

MIN_GAMES_FOR_VERDICT: int = 3

_VERDICT_LABELS: dict[str, str] = {
    "favorable": "Favorable",
    "lean_favorable": "Lean win",
    "even": "Even",
    "lean_unfavorable": "Lean loss",
    "unfavorable": "Unfavorable",
    "thin_sample": "Thin sample",
}


def matchups_dataframe(matches_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate every lane matchup into one row per opposing champion.

    Args:
        matches_df: One row per game (from :meth:`models.MatchRecord.to_row`).

    Returns:
        Per-champion games, winrate, lane differentials and death profile,
        sorted by games played.
    """
    if matches_df.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for champion, group in matches_df.groupby("opponent"):
        wins = int(group["win"].sum())
        games = int(len(group))
        rows.append(
            {
                "opponent": str(champion),
                "games": games,
                "wins": wins,
                "winrate": round(wins / games, 3),
                "wilson_lb": round(wilson_lower_bound(wins, games), 3),
                "avg_gd10": _mean(group, "gd10"),
                "avg_gd15": _mean(group, "gd15"),
                "avg_xpd10": _mean(group, "xpd10"),
                "avg_csd10": _mean(group, "csd10"),
                "avg_dpm": _mean(group, "dpm"),
                "avg_deaths": _mean(group, "deaths", 2),
                "avg_deaths_pre14": _mean(group, "deaths_pre14", 2),
                "avg_kills": _mean(group, "kills", 2),
            }
        )
    return pd.DataFrame(rows).sort_values("games", ascending=False).reset_index(drop=True)


def _mean(group: pd.DataFrame, column: str, digits: int = 1) -> float | None:
    """Rounded column mean ignoring missing values.

    Args:
        group: Games against one champion.
        column: Column name.
        digits: Rounding digits.

    Returns:
        The mean or ``None`` when no data exists.
    """
    series = group[column].dropna() if column in group else pd.Series(dtype=float)
    return round(float(series.mean()), digits) if not series.empty else None


def _num(row: Mapping[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _matchup_verdict(winrate: float, games: int) -> str:
    """Classify outcome confidence from sample size and win rate."""
    if games < MIN_GAMES_FOR_VERDICT:
        return "thin_sample"
    if winrate >= 0.65:
        return "favorable"
    if winrate >= 0.55:
        return "lean_favorable"
    if winrate <= 0.35:
        return "unfavorable"
    if winrate <= 0.45:
        return "lean_unfavorable"
    return "even"


def _role_key(role: str | None) -> str:
    return (role or "").strip().upper()


def _snowball_tip(role: str | None) -> str:
    key = _role_key(role)
    if key == "MIDDLE":
        return "Crush lane then roam on the crash — deny their mid tempo."
    if key == "TOP":
        return "Take plates and look for a decisive TP/skirmish while ahead."
    if key in {"BOTTOM", "UTILITY"}:
        return "Keep the wave shoved and force plays with your numbers lead."
    if key == "JUNGLE":
        return "Invade and take their camps while you own tempo."
    return "Snowball the lead — force objectives before the lane equalizes."


def _scale_tip(role: str | None) -> str:
    key = _role_key(role)
    if key == "TOP":
        return "Concede plates, freeze near tower, and play for your first item spike."
    if key == "MIDDLE":
        return "Farm safely under tower and look for side-lane impact after 2 items."
    if key == "BOTTOM":
        return "Give up contested CS, sync with support, and play for mid-game spikes."
    if key == "UTILITY":
        return "Play to keep your carry alive — don't contest every trade window."
    if key == "JUNGLE":
        return "Avoid early skirmishes; farm to your spike and counter-gank later."
    return "Play for scaling — avoid pre-spike trades and stabilize to mid game."


def _survival_tip(role: str | None, deaths: float) -> str:
    key = _role_key(role)
    base = f"Averaging {deaths:.1f} deaths before 14 — respect all-in and gank windows."
    if key == "TOP":
        return f"{base} Ward deeper and give up CS when their gap-closer is up."
    if key == "MIDDLE":
        return f"{base} Hold a more conservative wave and track jungle more carefully."
    if key in {"BOTTOM", "UTILITY"}:
        return f"{base} Don't walk up without vision or summoners."
    return base


def _cs_tip(csd: float, role: str | None) -> str:
    key = _role_key(role)
    if key == "JUNGLE":
        return f"Down {abs(csd):.0f} CS@10 — tighten clear efficiency and avoid delayed paths."
    return f"Down {abs(csd):.0f} CS@10 — prioritize safe last-hits and cleaner recall timings."


def matchup_advice(row: Mapping[str, Any], *, role: str | None = None) -> dict[str, str]:
    """Build a verdict + single focused tip for one matchup row.

    Picks the highest-severity pattern instead of concatenating the same
    generic lane phrases for every losing matchup.

    Args:
        row: A row of :func:`matchups_dataframe` (Series or mapping).
        role: Optional Riot ``teamPosition`` for role-flavored copy.

    Returns:
        Dict with ``verdict``, ``verdict_label``, ``focus``, and ``recommendation``.
    """
    games = int(_num(row, "games") or 0)
    winrate = float(_num(row, "winrate") or 0.0)
    gd10 = _num(row, "avg_gd10")
    gd15 = _num(row, "avg_gd15")
    xpd10 = _num(row, "avg_xpd10")
    csd10 = _num(row, "avg_csd10")
    deaths_pre14 = _num(row, "avg_deaths_pre14")
    kills = _num(row, "avg_kills")
    dpm = _num(row, "avg_dpm")

    verdict = _matchup_verdict(winrate, games)
    candidates: list[tuple[float, str, str]] = []

    if games < MIN_GAMES_FOR_VERDICT:
        candidates.append(
            (
                1.0,
                "Sample",
                f"Only {games} game{'s' if games != 1 else ''} — treat this read as soft until you have more reps.",
            )
        )

    if deaths_pre14 is not None and deaths_pre14 >= 1.5:
        severity = 10.0 + deaths_pre14
        candidates.append((severity, "Survive", _survival_tip(role, deaths_pre14)))
    elif deaths_pre14 is not None and deaths_pre14 >= 1.0 and verdict in {
        "unfavorable",
        "lean_unfavorable",
    }:
        candidates.append(
            (
                7.5,
                "Survive",
                f"Early deaths ({deaths_pre14:.1f} pre-14) are dragging this matchup — play farther back early.",
            )
        )

    if gd10 is not None and gd15 is not None:
        swing = gd15 - gd10
        if gd10 <= -150 and swing >= 250:
            candidates.append(
                (
                    9.0,
                    "Stabilize",
                    "You bleed early but recover by 15 — survive the first wave crashes, then equalize.",
                )
            )
        elif gd10 >= 150 and swing <= -250:
            candidates.append(
                (
                    9.2,
                    "Protect lead",
                    "Leads evaporate after 10 — stop greeding and cash tempo into plates/objectives.",
                )
            )

    if (
        gd10 is not None
        and gd10 >= 200
        and games >= MIN_GAMES_FOR_VERDICT
        and winrate <= 0.45
    ):
        candidates.append(
            (
                9.5,
                "Convert",
                "You win lane but still lose too often — turn gold leads into towers, herald, and mid pressure.",
            )
        )

    if (
        gd10 is not None
        and gd10 <= -200
        and games >= MIN_GAMES_FOR_VERDICT
        and winrate >= 0.55
    ):
        candidates.append(
            (
                8.8,
                "Scale",
                "You lose lane but still win the game — keep conceding early and playing for your spike.",
            )
        )

    if gd10 is not None and gd10 <= -400:
        candidates.append((8.6, "Scale", _scale_tip(role)))
    elif gd10 is not None and gd10 <= -200:
        candidates.append(
            (
                7.2,
                "Lane",
                "You're consistently behind at 10 — shorten trade windows and take safer wave positions.",
            )
        )
    elif (
        gd10 is not None
        and gd10 >= 250
        and games >= MIN_GAMES_FOR_VERDICT
        and winrate >= 0.55
    ):
        candidates.append((8.0, "Snowball", _snowball_tip(role)))

    if xpd10 is not None and xpd10 <= -350 and (gd10 is None or gd10 > -200):
        candidates.append(
            (
                7.8,
                "XP",
                "Big XP deficit at 10 — avoid long recalls and contested fights that cost levels.",
            )
        )

    # Supports don't farm — never tip or rank matchups on CS differentials.
    if _role_key(role) != "UTILITY":
        if csd10 is not None and csd10 <= -10:
            candidates.append((7.0, "Farm", _cs_tip(csd10, role)))
        elif csd10 is not None and csd10 <= -6 and (gd10 is None or gd10 > -200):
            candidates.append(
                (
                    5.5,
                    "Farm",
                    f"Soft CS bleed ({csd10:+.0f}@10) — clean up crash waves instead of forcing trades.",
                )
            )

    if (
        kills is not None
        and deaths_pre14 is not None
        and kills >= 3.0
        and deaths_pre14 >= 1.2
        and winrate < 0.5
    ):
        candidates.append(
            (
                6.8,
                "Trades",
                "High-kill but messy lanes — take the same aggression with fewer death resets.",
            )
        )

    if (
        dpm is not None
        and dpm < 450
        and games >= MIN_GAMES_FOR_VERDICT
        and winrate <= 0.45
        and (gd10 is None or gd10 >= -100)
    ):
        candidates.append(
            (
                5.0,
                "Damage",
                "Low damage for this set of games — look for cleaner mid-game fight angles after lane.",
            )
        )

    if verdict == "favorable" and not any(focus == "Snowball" for _, focus, _ in candidates):
        candidates.append(
            (
                4.0,
                "Press",
                "Strong matchup overall — draft it when you can and look to set the tempo early.",
            )
        )
    elif verdict == "unfavorable" and not candidates:
        candidates.append(
            (
                4.0,
                "Respect",
                "Hard matchup in your data — consider a defensive setup or ban if it keeps showing up.",
            )
        )
    elif verdict == "even":
        candidates.append(
            (
                2.0,
                "Standard",
                "No clear pattern yet — play your normal plan and track jungle more than the matchup itself.",
            )
        )
    elif verdict.startswith("lean_") and not candidates:
        lean_win = verdict == "lean_favorable"
        candidates.append(
            (
                3.0,
                "Edge" if lean_win else "Caution",
                (
                    "Slight edge in your data — play proactively but don't force coin-flip fights."
                    if lean_win
                    else "Slight deficit in your data — take the safer lane plan until you find a window."
                ),
            )
        )

    if not candidates:
        candidates.append(
            (
                1.0,
                "Standard",
                "Even matchup in your data — play your default plan.",
            )
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, focus, recommendation = candidates[0]
    focus_key = focus.lower().replace(" ", "-")
    return {
        "verdict": verdict,
        "verdict_label": _VERDICT_LABELS[verdict],
        "verdict_tone": verdict_tone(verdict),
        "focus": focus,
        "focus_key": focus_key,
        "focus_tone": focus_tone(focus_key),
        "recommendation": recommendation,
    }


def matchup_recommendation(row: Mapping[str, Any], *, role: str | None = None) -> str:
    """Generate a short human recommendation for one matchup row.

    Args:
        row: A row of :func:`matchups_dataframe`.
        role: Optional Riot ``teamPosition`` for role-flavored copy.

    Returns:
        A one-sentence coaching hint.
    """
    return matchup_advice(row, role=role)["recommendation"]


def matchup_summary(matchups_df: pd.DataFrame) -> dict[str, Any]:
    """Detect the best and worst matchups with enough games.

    Ranking uses the Wilson lower bound so small samples don't dominate.

    Args:
        matchups_df: Output of :func:`matchups_dataframe`.

    Returns:
        Best/worst matchup names and their stats, or an empty dict.
    """
    if matchups_df.empty:
        return {}
    eligible = matchups_df[matchups_df["games"] >= MIN_GAMES_FOR_VERDICT]
    if eligible.empty:
        return {}
    best = eligible.loc[eligible["wilson_lb"].idxmax()]
    worst = eligible.loc[(eligible["winrate"] + (1 - eligible["wilson_lb"])).idxmin()]
    return {
        "best_matchup": champion_display_name(str(best["opponent"])),
        "best_matchup_winrate": float(best["winrate"]),
        "best_matchup_games": int(best["games"]),
        "worst_matchup": champion_display_name(str(worst["opponent"])),
        "worst_matchup_winrate": float(worst["winrate"]),
        "worst_matchup_games": int(worst["games"]),
        "worst_matchup_deaths_pre14": (
            float(worst["avg_deaths_pre14"]) if worst["avg_deaths_pre14"] is not None else None
        ),
    }
