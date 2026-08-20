"""Markdown export for Form Tracker progression summaries."""

from __future__ import annotations

from league_stats_common.core.models import ProgressionComparison


def progression_to_markdown(comparison: ProgressionComparison) -> str:
    """Render a human-readable progression summary."""
    snap = comparison.snapshot
    lines = [
        f"# Form Tracker — {comparison.build_label}",
        "",
        f"**Preset:** last {comparison.recent_n} vs baseline {comparison.baseline_m} "
        f"({comparison.overlap_mode})",
        "",
        "## Snapshot",
        "",
        f"- Form score: **{snap.form_score:+.1f}** ({snap.trend}, {snap.confidence} confidence)",
        f"- Win rate: {snap.recent_winrate * 100:.0f}% vs baseline {snap.baseline_winrate * 100:.0f}% "
        f"({snap.winrate_delta_pp:+.1f} percentage points)",
        f"- Streak: {snap.current_streak or '—'}",
        f"- {snap.headline}",
        "",
    ]

    if comparison.stories:
        lines.extend(["## Form stories", ""])
        for story in comparison.stories:
            label = "Keep" if story.tone == "keep" else "Fix"
            lines.append(f"### [{label}] {story.title}")
            lines.append(f"- Driver: {story.driver}")
            if story.habit:
                lines.append(f"- Habit: {story.habit}")
            lines.append(f"- Action: {story.action}")
            lines.append("")

    if comparison.top_improved:
        lines.extend(["## Top improvements", ""])
        for delta in comparison.top_improved:
            lines.append(f"- **{delta.label}**: {delta.baseline:.2f} → {delta.recent:.2f}")
        lines.append("")

    if comparison.top_regressed:
        lines.extend(["## Regressions", ""])
        for delta in comparison.top_regressed:
            lines.append(f"- **{delta.label}**: {delta.baseline:.2f} → {delta.recent:.2f}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"
