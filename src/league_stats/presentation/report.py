"""Report generation support: improvement score and on-disk report/manifest metadata."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

import pandas as pd

from league_stats.analysis.improvement import column_mean, score_categories
from league_stats.core.champions import (
    build_label,
    champion_display_name,
    champion_slug,
    role_display,
)
from league_stats.core.models import Recommendation
from league_stats.presentation.brand_assets import refresh_saved_report_branding

if TYPE_CHECKING:
    from league_stats.infra.ddragon_assets import DDragonAssets


def utc_now_iso() -> str:
    """UTC timestamp for ``generated_at`` (ISO-8601, sortable, JS-parseable)."""
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def game_creation_ms_to_iso(game_creation_ms: int) -> str:
    """Convert a Riot match ``gameCreation`` millisecond timestamp to ISO-8601 UTC."""
    if game_creation_ms <= 0:
        return ""
    return (
        datetime.fromtimestamp(game_creation_ms / 1000, tz=timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def is_group_player_label(player: str) -> bool:
    """True when the label represents a pooled multi-account run."""
    return "," in player


@dataclass(frozen=True)
class ScoreComponent:
    """One category of the improvement score."""

    name: str
    score: float  # 0-100
    value: str
    hint: str


def improvement_score(
    matches_df: pd.DataFrame, *, role: str = "MIDDLE"
) -> tuple[float, list[ScoreComponent]]:
    """Compute the composite improvement score (0-100) and its category components.

    Each component is a lane-dependent category score (Economy, Fight, Laning, …)
    built from several underlying metrics. Benchmarks are role-aware targets
    derived from static tier data; the score tracks progress between runs for
    the same build, not cross-player comparison.

    Args:
        matches_df: Master per-game table.
        role: Riot team position (``TOP``, ``MIDDLE``, ``JUNGLE``, ...).

    Returns:
        Tuple of overall score and the per-category components.
    """
    if matches_df.empty:
        return 0.0, []

    from league_stats.analysis.combat import prefers_cc_over_dpm
    from league_stats.analysis.peer.benchmarks import try_role_benchmark
    from league_stats.core.role_metrics import role_profile

    profile = role_profile(role)
    gold = try_role_benchmark("GOLD", role) or {}
    use_cc = prefers_cc_over_dpm(
        role, avg_damage_share=column_mean(matches_df, "damage_share")
    )

    categories = score_categories(
        profile.score_components,
        matches_df,
        gold=gold,
        role=profile.role,
        use_cc=use_cc,
    )
    components = [
        ScoreComponent(name=c.name, score=c.score, value=c.value, hint=c.hint)
        for c in categories
        if math.isfinite(c.score)
    ]
    overall = (
        round(sum(c.score for c in components) / len(components), 1) if components else 0.0
    )
    return overall, components


def build_player_builds_nav(
    builds: list[dict[str, Any]],
    *,
    current_champion: str,
    current_role: str,
    assets: "DDragonAssets | None" = None,
    from_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Build sidebar champion links relative to the current report directory."""
    current_slug = champion_slug(current_champion, current_role)
    nav: list[dict[str, Any]] = []
    for build in builds:
        slug = champion_slug(str(build["champion"]), str(build["role"]))
        winrate = float(build.get("winrate", 0.0))
        riot_id = str(build["champion"])
        icon_href = None
        role_icon = None
        if assets is not None and from_dir is not None:
            icon_href = assets.champion_href(riot_id, from_dir=from_dir)
            role_icon = assets.role_href(str(build["role"]), from_dir=from_dir)
        nav.append(
            {
                "label": (
                    f'{build["build_label"]} · {build["games"]} games · '
                    f"{winrate * 100:.0f}% WR"
                ),
                "build_label": str(build["build_label"]),
                "champion": champion_display_name(riot_id),
                "role": str(build["role"]),
                "role_display": str(build.get("role_display", role_display(str(build["role"])))),
                "games": int(build.get("games", 0)),
                "winrate": winrate,
                "href": f"../{slug}/report.json",
                "selected": slug == current_slug,
                "champion_icon": icon_href,
                "role_icon": role_icon,
            }
        )
    return nav


def write_player_manifest(player_dir: Path, manifest: dict[str, Any]) -> Path:
    """Persist the player-level build manifest."""
    player_dir.mkdir(parents=True, exist_ok=True)
    path = player_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return path


def build_manifest_entry(
    *,
    champion: str,
    role: str,
    games: int,
    winrate: float,
) -> dict[str, Any]:
    """Create one manifest build entry with a report-relative href."""
    slug = champion_slug(champion, role)
    return {
        "champion": champion,
        "role": role,
        "role_display": role_display(role),
        "build_label": build_label(champion, role),
        "games": games,
        "winrate": round(winrate, 3),
        "href": f"{slug}/report.json",
    }


def write_report_meta(report_dir: Path, meta: dict[str, Any]) -> Path:
    """Persist report metadata beside ``report.json``.

    Args:
        report_dir: Directory for this player/champion/lane run.
        meta: Serializable metadata (player, champion, lane, stats...).

    Returns:
        Path of ``meta.json``.
    """
    path = report_dir / "meta.json"
    path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return path


def discover_player_builds(player_dir: Path) -> list[dict[str, Any]]:
    """Scan a player directory for completed build reports.

    Args:
        player_dir: ``output/reports/{player}/`` directory.

    Returns:
        Build metadata dicts sorted by game count (most played first).
        Each entry includes an ``href`` relative to ``player_dir``.
    """
    if not player_dir.is_dir():
        return []

    builds: list[dict[str, Any]] = []
    for meta_path in sorted(player_dir.glob("*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        report_json = meta_path.parent / "report.json"
        if not report_json.is_file():
            continue
        slug = meta_path.parent.name
        meta["href"] = f"{slug}/report.json"
        builds.append(meta)

    builds.sort(key=lambda entry: (entry.get("games", 0), entry.get("generated_at", "")), reverse=True)
    return builds


def refresh_player_hub(
    player_dir: Path,
    template_dir: Path,
    *,
    player_label: str | None = None,
    assets: "DDragonAssets | None" = None,
) -> Path | None:
    """Rebuild ``output/reports/{player}/manifest.json`` from on-disk build metadata.

    Args:
        player_dir: Player reports root.
        template_dir: Unused; kept for call-site compatibility (the player hub
            page is now rendered client-side by the SPA, not from a template).
        player_label: Display label (``Name#TAG``); inferred from builds when omitted.

    Returns:
        Path of the player hub, or ``None`` when no builds exist yet.
    """
    builds = discover_player_builds(player_dir)
    if not builds:
        return None

    label = player_label or str(builds[0].get("player", ""))
    if assets is not None:
        for build in builds:
            riot_id = str(build.get("champion", ""))
            build["champion_icon"] = assets.champion_href(
                riot_id,
                from_dir=player_dir,
            )
            build["champion"] = champion_display_name(riot_id)
            build["role_icon"] = assets.role_href(
                str(build.get("role", "")),
                from_dir=player_dir,
            )
    manifest = {
        "player": label,
        "builds": builds,
        "default_href": builds[0]["href"],
    }
    return write_player_manifest(player_dir, manifest)


def refresh_all_player_hubs(
    output_dir: Path,
    template_dir: Path,
    *,
    assets: "DDragonAssets | None" = None,
) -> list[Path]:
    """Rebuild every player hub under ``output/reports/``."""
    reports_root = output_dir / "reports"
    if not reports_root.is_dir():
        return []

    hubs: list[Path] = []
    for player_dir in sorted(reports_root.iterdir()):
        if not player_dir.is_dir():
            continue
        hub = refresh_player_hub(player_dir, template_dir, assets=assets)
        if hub is not None:
            hubs.append(hub)
    return hubs


def refresh_report_indexes(
    output_dir: Path,
    template_dir: Path,
    *,
    player_dir: Path | None = None,
    player_label: str | None = None,
    assets: "DDragonAssets | None" = None,
) -> Path | None:
    """Refresh saved-report branding and optionally rebuild a player hub.

    Call after each report is written so hubs stay current during batch runs.

    Args:
        output_dir: Root output directory.
        template_dir: Template directory.
        player_dir: Optional player reports root for the player hub.
        player_label: Optional player display label for the hub.

    Returns:
        Optional player hub path.
    """
    refresh_saved_report_branding(output_dir)
    if player_dir is None:
        return None
    return refresh_player_hub(
        player_dir, template_dir, player_label=player_label, assets=assets
    )


def discover_reports(output_dir: Path) -> list[dict[str, Any]]:
    """Scan ``output/reports/`` for saved report metadata.

    Args:
        output_dir: Root output directory.

    Returns:
        Report metadata dicts sorted by ``generated_at`` (newest first).
        Each entry includes an ``href`` relative to ``output_dir``.
    """
    reports_root = output_dir / "reports"
    if not reports_root.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for meta_path in reports_root.glob("*/*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        report_json = meta_path.parent / "report.json"
        if not report_json.is_file():
            continue
        meta["href"] = report_json.relative_to(output_dir).as_posix()
        entries.append(meta)

    entries.sort(key=lambda entry: entry.get("generated_at", ""), reverse=True)
    return entries


def score_badge(recommendation: Recommendation) -> str:
    """CSS badge class for a recommendation's priority.

    Args:
        recommendation: The recommendation.

    Returns:
        One of ``high``/``medium``/``low``.
    """
    if recommendation.priority >= 2.0:
        return "high"
    if recommendation.priority >= 1.2:
        return "medium"
    return "low"
