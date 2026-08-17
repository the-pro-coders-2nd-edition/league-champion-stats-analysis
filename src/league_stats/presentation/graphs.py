"""Visualisation factory: interactive Plotly figures and matplotlib heatmaps.

Every Plotly figure uses the dark template and is exported as an embeddable
HTML div (plotly.js is loaded once from a CDN by the report template).
Matplotlib is used for static death heatmap PNGs saved to ``graphs/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import matplotlib
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

matplotlib.use("Agg")  # headless rendering; must precede pyplot import
import matplotlib.pyplot as plt

from league_stats.analysis.statistics import ModelResult, feature_label
from league_stats.core.champions import champion_display_name
from league_stats.pipeline.view_models import (
    _form_gap_display_and_score,
    form_delta_chart_value,
    form_delta_rank_magnitude,
)
from league_stats.presentation.metric_colors import (
    NEUTRAL_HEX,
    colors_for_winrates,
    interpolate_metric_color,
    score_peer_gap,
)
from league_stats.analysis.deaths import death_heatmap_coords
from league_stats.utils import MAP_SIZE, get_logger

PLOTLY_TEMPLATE = "plotly_dark"
ACCENT = "#7c6cf0"
WIN_COLOR = "#3fb68b"
LOSS_COLOR = "#e05563"
TEXT_COLOR = "#e8eaf2"
MUTED_COLOR = "#9aa0b5"
GRID_COLOR = "#2a2f40"
FONT_FAMILY = 'Manrope, -apple-system, "Segoe UI", sans-serif'
COLORWAY = [ACCENT, WIN_COLOR, "#7ed4c0", "#e0b155", "#b48cff", "#2a9f7a"]

# Characteristic unit for asinh-compressed bipolar % / delta bars.
# Values near this stay roughly linear; larger outliers get compressed.
_COMPRESS_SCALE = 5.0
_COMPRESS_TICK_MAGNITUDES = (5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000)


def _compress_signed(value: float) -> float:
    """Compress bipolar magnitudes so extreme outliers don't dominate bar length.

    Uses ``asinh(x / 5)`` — same shape as before, but the scale starts at 5
    instead of 1 (so ±1/±2 are tiny and the first meaningful unit is ±5).
    """
    return float(np.arcsinh(value / _COMPRESS_SCALE))


def _compressed_xaxis(*, raw_values: list[float], title: str, suffix: str = "") -> dict[str, Any]:
    """X-axis config with original-unit tick labels over an asinh-compressed scale."""
    max_abs = max((abs(float(v)) for v in raw_values), default=0.0)
    candidates: list[float] = [0.0]
    for mag in _COMPRESS_TICK_MAGNITUDES:
        if mag <= max_abs * 1.15:
            candidates.extend([-float(mag), float(mag)])
    ordered = sorted(set(candidates))
    ticktext = ["0" if c == 0 else f"{c:+.0f}{suffix}" for c in ordered]
    return {
        "title": title,
        "tickmode": "array",
        "tickvals": [_compress_signed(c) for c in ordered],
        "ticktext": ticktext,
    }


def _div(fig: go.Figure) -> str:
    """Serialise a figure as an embeddable HTML div (no plotly.js inline)."""
    margin = fig.layout.margin
    left = margin.l if margin.l is not None else 48
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT_FAMILY, color=TEXT_COLOR, size=13),
        title_font=dict(family=FONT_FAMILY, color=TEXT_COLOR, size=15),
        colorway=COLORWAY,
        legend=dict(font=dict(family=FONT_FAMILY, color=MUTED_COLOR, size=12)),
        margin=dict(t=48, b=40, l=left, r=24),
        autosize=True,
    )
    fig.update_xaxes(
        gridcolor=GRID_COLOR,
        zerolinecolor=GRID_COLOR,
        linecolor=GRID_COLOR,
        tickfont=dict(family=FONT_FAMILY, color=MUTED_COLOR, size=11),
        title_font=dict(family=FONT_FAMILY, color=MUTED_COLOR, size=12),
    )
    fig.update_yaxes(
        gridcolor=GRID_COLOR,
        zerolinecolor=GRID_COLOR,
        linecolor=GRID_COLOR,
        tickfont=dict(family=FONT_FAMILY, color=MUTED_COLOR, size=11),
        title_font=dict(family=FONT_FAMILY, color=MUTED_COLOR, size=12),
    )
    return fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        default_height=420,
        config={"responsive": True},
    )


@dataclass
class ChartIconResolver:
    """Resolve champion, item, rune and map assets for Plotly charts."""

    from_dir: Path
    champion_href: Callable[[str], str | None]
    item_href: Callable[[str], str | None]
    keystone_href: Callable[[str], str | None]
    map_source: Callable[[], str | None] | None = None
    map_path: Path | None = None


def _horizontal_icon_bar(
    *,
    values: list[float],
    labels: list[str],
    icon_hrefs: list[str | None],
    colors: list[str],
    text: list[str] | None,
    title: str,
    xaxis_title: str,
    vline: float | None = 50.0,
    height: int | None = None,
) -> go.Figure:
    """Horizontal bar chart with optional row icons instead of y-axis text."""
    y_indices = list(range(len(labels)))
    fig = go.Figure(
        go.Bar(
            x=values,
            y=y_indices,
            orientation="h",
            marker_color=colors,
            text=text,
            textposition="outside",
            hovertext=labels,
            hoverinfo="text+x",
        )
    )
    fig.update_yaxes(
        tickmode="array",
        tickvals=y_indices,
        ticktext=[""] * len(labels),
        showgrid=False,
    )
    has_icons = any(icon_hrefs)
    icon_slot = max(max(values) * 0.1, 10.0) if values else 10.0
    icon_x = -icon_slot * 0.55
    for index, href in enumerate(icon_hrefs):
        if not href:
            fig.add_annotation(
                x=icon_x,
                y=index,
                xref="x",
                yref="y",
                text=labels[index],
                showarrow=False,
                xanchor="right",
                font=dict(family=FONT_FAMILY, size=11, color=MUTED_COLOR),
            )
            continue
        fig.add_layout_image(
            dict(
                source=href,
                xref="x",
                yref="y",
                x=icon_x,
                y=index,
                sizex=icon_slot,
                sizey=0.85,
                sizing="contain",
                xanchor="center",
                yanchor="middle",
                layer="above",
            )
        )
    if vline is not None:
        fig.add_vline(x=vline, line_dash="dot", line_color="#888")
    x_max = max(values) if values else 100.0
    fig.update_xaxes(range=[-icon_slot if has_icons else 0, max(x_max * 1.12, 60)])
    fig.update_layout(
        title=title,
        xaxis_title=xaxis_title,
        height=height or max(360, 30 * len(labels)),
        margin=dict(l=56, t=48, b=40, r=48),
    )
    return fig


def _summoners_rift_layout_image(source: str) -> dict[str, Any]:
    """Plotly layout image covering the full Riot map coordinate space."""
    return dict(
        source=source,
        xref="x",
        yref="y",
        x=0,
        y=0,
        sizex=MAP_SIZE,
        sizey=MAP_SIZE,
        xanchor="left",
        yanchor="bottom",
        sizing="stretch",
        opacity=1.0,
        layer="below",
    )


_DEATH_HEATMAP_COLORSCALE: list[list[Any]] = [
    [0.0, "rgba(0,0,0,0)"],
    [0.15, "rgba(255,60,0,0.40)"],
    [0.45, "rgba(255,140,0,0.60)"],
    [1.0, "rgba(255,240,180,0.82)"],
]


class GraphFactory:
    """Builds every chart of the report from the aggregated dataframes."""

    def __init__(
        self,
        graphs_dir: Path,
        *,
        icon_resolver: ChartIconResolver | None = None,
    ) -> None:
        """Create the factory.

        Args:
            graphs_dir: Directory where static PNG assets are written.
            icon_resolver: Optional resolver for champion/item/rune chart icons.
        """
        self._graphs_dir = graphs_dir
        self._icons = icon_resolver
        self._log = get_logger("graphs")

    def _map_chart_source(self) -> str | None:
        if self._icons and self._icons.map_source:
            return self._icons.map_source()
        return None

    def _map_image_path(self) -> Path | None:
        if self._icons and self._icons.map_path and self._icons.map_path.is_file():
            return self._icons.map_path
        return None

    # -------------------------------------------------------------- Trends

    def gold_diff_timeline(self, records_series: list[tuple[bool, list[int], list[int]]]) -> str:
        """Average gold differential per minute in wins vs losses.

        Args:
            records_series: Tuples of (win, gold_series, opponent_gold_series).
        """
        fig = go.Figure()
        for label, color, flag in (("Wins", WIN_COLOR, True), ("Losses", LOSS_COLOR, False)):
            diffs: dict[int, list[int]] = {}
            for win, mine, theirs in records_series:
                if win != flag:
                    continue
                for minute, (g, og) in enumerate(zip(mine, theirs)):
                    if og:
                        diffs.setdefault(minute, []).append(g - og)
            if not diffs:
                continue
            minutes = sorted(diffs)
            fig.add_scatter(
                x=minutes,
                y=[float(np.mean(diffs[m])) for m in minutes],
                mode="lines",
                name=label,
                line=dict(color=color, width=3),
            )
        fig.add_hline(y=0, line_dash="dot", line_color="#888")
        fig.update_layout(title="Average gold diff vs lane opponent", xaxis_title="Minute", yaxis_title="Gold diff")
        return _div(fig)

    # -------------------------------------------------------- Distributions

    def gd10_histogram(self, matches_df: pd.DataFrame) -> str:
        """Gold-diff-at-10 distribution split by result."""
        frame = matches_df.dropna(subset=["gd10"]).copy()
        frame["Result"] = frame["win"].map({1: "Win", 0: "Loss"})
        fig = px.histogram(
            frame, x="gd10", color="Result", nbins=30, barmode="overlay", opacity=0.7,
            color_discrete_map={"Win": WIN_COLOR, "Loss": LOSS_COLOR},
        )
        fig.update_layout(title="Gold diff @10 distribution", xaxis_title="Gold diff @10")
        return _div(fig)

    def deaths_box(self, matches_df: pd.DataFrame) -> str:
        """Deaths per game box plot split by result."""
        frame = matches_df.copy()
        frame["Result"] = frame["win"].map({1: "Win", 0: "Loss"})
        fig = px.box(
            frame, x="Result", y="deaths", color="Result", points="all",
            color_discrete_map={"Win": WIN_COLOR, "Loss": LOSS_COLOR},
        )
        fig.update_layout(title="Deaths per game by result", showlegend=False)
        return _div(fig)

    def cs10_violin(self, matches_df: pd.DataFrame) -> str:
        """CS-at-10 violin plot split by result."""
        frame = matches_df.dropna(subset=["cs10"]).copy()
        frame["Result"] = frame["win"].map({1: "Win", 0: "Loss"})
        fig = px.violin(
            frame, x="Result", y="cs10", color="Result", box=True, points="all",
            color_discrete_map={"Win": WIN_COLOR, "Loss": LOSS_COLOR},
        )
        fig.update_layout(title="CS @10 by result", showlegend=False)
        return _div(fig)

    def dpm_scatter(self, matches_df: pd.DataFrame) -> str:
        """DPM vs GPM scatter coloured by result."""
        frame = matches_df.copy()
        frame["Result"] = frame["win"].map({1: "Win", 0: "Loss"})
        fig = px.scatter(
            frame, x="gpm", y="dpm", color="Result", hover_data=["match_id", "opponent"],
            color_discrete_map={"Win": WIN_COLOR, "Loss": LOSS_COLOR},
        )
        fig.update_layout(title="Damage per minute vs gold per minute", xaxis_title="GPM", yaxis_title="DPM")
        return _div(fig)

    def vision_trend(self, matches_df: pd.DataFrame) -> str:
        """Vision score per minute over the game history."""
        frame = matches_df.sort_values("game_creation_ms").reset_index(drop=True)
        fig = go.Figure()
        fig.add_scatter(y=frame["vspm"], mode="markers", name="VS/min", marker=dict(color=ACCENT))
        rolling = frame["vspm"].rolling(10, min_periods=3).mean()
        fig.add_scatter(y=rolling, mode="lines", name="10-game average", line=dict(color=WIN_COLOR, width=3))
        fig.update_layout(title="Vision score per minute over time", xaxis_title="Game #", yaxis_title="VS/min")
        return _div(fig)

    # ----------------------------------------------------------- Death maps

    def death_heatmap(self, deaths_df: pd.DataFrame) -> str:
        """Interactive 2-D death density heatmap on Summoner's Rift coordinates."""
        fig = go.Figure()
        map_source = self._map_chart_source()
        if map_source:
            fig.add_layout_image(_summoners_rift_layout_image(map_source))
        if not deaths_df.empty:
            hx, hy = death_heatmap_coords(deaths_df)
            fig.add_histogram2d(
                x=hx, y=hy,
                xbins=dict(start=0, end=MAP_SIZE, size=MAP_SIZE / 24),
                ybins=dict(start=0, end=MAP_SIZE, size=MAP_SIZE / 24),
                colorscale=_DEATH_HEATMAP_COLORSCALE,
                showscale=False,
            )
            fig.add_scatter(
                x=hx, y=hy, mode="markers",
                marker=dict(size=5, color="rgba(255,255,255,0.75)", line=dict(width=1, color="#111")),
                text=[f"min {m:.0f} by {k}" for m, k in zip(deaths_df["minute"], deaths_df["killer"])],
                hoverinfo="text", name="Deaths",
            )
        fig.update_layout(
            title="Death heatmap (normalized to blue side)",
            xaxis=dict(range=[0, MAP_SIZE], showgrid=False, visible=False),
            yaxis=dict(range=[0, MAP_SIZE], showgrid=False, scaleanchor="x", visible=False),
            height=560,
        )
        return _div(fig)

    def death_heatmap_png(self, deaths_df: pd.DataFrame, filename: str = "death_heatmap.png") -> Path | None:
        """Static death heatmap PNG per game phase, saved under ``graphs/``.

        Args:
            deaths_df: Per-death table.
            filename: Output file name.

        Returns:
            The written path, or ``None`` when there are no deaths.
        """
        if deaths_df.empty:
            return None
        hx, hy = death_heatmap_coords(deaths_df)
        map_path = self._map_image_path()
        map_image = plt.imread(map_path) if map_path else None
        phases = [("Early (<14)", deaths_df["minute"] < 14),
                  ("Mid (14-25)", (deaths_df["minute"] >= 14) & (deaths_df["minute"] < 25)),
                  ("Late (25+)", deaths_df["minute"] >= 25)]
        fig, axes = plt.subplots(1, 3, figsize=(15, 5), facecolor="#111")
        for ax, (title, mask) in zip(axes, phases):
            subset_x = hx[mask]
            subset_y = hy[mask]
            ax.set_facecolor("#181825")
            if map_image is not None:
                ax.imshow(
                    map_image,
                    extent=(0, MAP_SIZE, 0, MAP_SIZE),
                    origin="lower",
                    aspect="equal",
                    zorder=0,
                )
            if not subset_x.empty:
                ax.hexbin(
                    subset_x, subset_y, gridsize=18, cmap="inferno",
                    extent=(0, MAP_SIZE, 0, MAP_SIZE), alpha=0.72, mincnt=1, zorder=1,
                )
            ax.set_xlim(0, MAP_SIZE)
            ax.set_ylim(0, MAP_SIZE)
            ax.set_title(f"{title} - {int(mask.sum())} deaths", color="white")
            ax.tick_params(colors="#888", labelbottom=False, labelleft=False)
        path = self._graphs_dir / filename
        fig.tight_layout()
        fig.savefig(path, dpi=110, facecolor="#111")
        plt.close(fig)
        return path

    # ------------------------------------------------------------ Analytics

    def correlation_heatmap(self, corr: pd.DataFrame) -> str:
        """Correlation matrix heatmap."""
        if corr.empty:
            return _div(go.Figure().update_layout(title="Correlation matrix (no data)"))
        labeled = corr.copy()
        labeled.index = [feature_label(str(i)) for i in labeled.index]
        labeled.columns = [feature_label(str(c)) for c in labeled.columns]
        fig = px.imshow(
            labeled, color_continuous_scale="RdBu", zmin=-1, zmax=1, aspect="auto",
            text_auto=".2f",
        )
        fig.update_layout(title="Feature correlation matrix", height=620)
        return _div(fig)

    def win_correlation_bar(self, correlations: list[Any]) -> str:
        """Bar chart of feature correlations with winning."""
        if not correlations:
            return _div(go.Figure().update_layout(title="Win correlations (no data)"))
        features = [feature_label(c.feature) for c in correlations]
        values = [c.correlation for c in correlations]
        colors = [WIN_COLOR if v > 0 else LOSS_COLOR for v in values]
        fig = go.Figure(go.Bar(x=values, y=features, orientation="h", marker_color=colors))
        fig.update_layout(title="What correlates with winning", xaxis_title="Point-biserial r",
                          height=max(360, 30 * len(features)))
        return _div(fig)

    def feature_importance(self, model: ModelResult) -> str:
        """RandomForest feature importance bar chart."""
        if not model.trained or model.feature_importance.empty:
            return _div(go.Figure().update_layout(
                title=f"Win predictor not trained ({model.reason or 'insufficient data'})"))
        frame = model.feature_importance
        subtitle = (
            f"CV ROC-AUC {model.cv_auc_mean:.2f} +/- {model.cv_auc_std:.2f}"
            if model.cv_auc_mean is not None
            else f"{model.n_games} games"
        )
        labels = [feature_label(f) for f in frame["feature"]]
        fig = go.Figure(go.Bar(x=frame["importance"], y=labels, orientation="h",
                               marker_color=ACCENT))
        fig.update_layout(title=f"Early-game win predictor - feature importance ({subtitle})",
                          xaxis_title="Importance")
        return _div(fig)

    def cluster_scatter(self, clusters_df: pd.DataFrame) -> str:
        """PCA projection of game clusters with archetype labels."""
        if clusters_df.empty:
            return _div(go.Figure().update_layout(title="Game clusters (not enough games)"))
        fig = px.scatter(
            clusters_df, x="pca_x", y="pca_y", color="label",
            symbol=clusters_df["win"].map({1: "Win", 0: "Loss"}),
            hover_data=["match_id", "cluster"],
        )
        fig.update_layout(title="Game archetypes (KMeans clusters, PCA projection)")
        return _div(fig)

    # -------------------------------------------------------------- Domains

    def matchup_bar(self, matchups_df: pd.DataFrame, min_games: int = 2) -> str:
        """Win rate per lane opponent with game counts."""
        if matchups_df.empty:
            return _div(go.Figure().update_layout(title="Matchups (no data)"))
        frame = matchups_df[matchups_df["games"] >= min_games].sort_values("winrate")
        opponent_ids = frame["opponent"].astype(str).tolist()
        labels = [champion_display_name(opponent) for opponent in opponent_ids]
        icon_hrefs = (
            [self._icons.champion_href(opponent) for opponent in opponent_ids]
            if self._icons
            else [None] * len(opponent_ids)
        )
        fig = _horizontal_icon_bar(
            values=(frame["winrate"] * 100).tolist(),
            labels=labels,
            icon_hrefs=icon_hrefs,
            colors=colors_for_winrates(frame["winrate"].tolist()),
            text=[f"{g} games" for g in frame["games"]],
            title=f"Win rate by lane opponent (min {min_games} games)",
            xaxis_title="Win rate %",
            height=max(360, 26 * len(frame)),
        )
        return _div(fig)

    def item_winrate_bar(self, items_df: pd.DataFrame, slot: str = "first_item") -> str:
        """Win rate per item for a build slot."""
        if items_df.empty:
            return _div(go.Figure().update_layout(title="Items (no data)"))
        frame = items_df[items_df["slot"] == slot].sort_values("winrate")
        labels = frame["item"].astype(str).tolist()
        icon_hrefs = (
            [self._icons.item_href(label) for label in labels]
            if self._icons
            else [None] * len(labels)
        )
        fig = _horizontal_icon_bar(
            values=(frame["winrate"] * 100).tolist(),
            labels=labels,
            icon_hrefs=icon_hrefs,
            colors=[ACCENT] * len(frame),
            text=[f"{g} games" for g in frame["games"]],
            title=f"Win rate by {slot.replace('_', ' ')}",
            xaxis_title="Win rate %",
        )
        return _div(fig)

    def rune_winrate_bar(self, rune_stats: pd.DataFrame, dimension: str = "keystone") -> str:
        """Win rate per rune setup dimension."""
        if rune_stats.empty:
            return _div(go.Figure().update_layout(title="Runes (no data)"))
        labels = rune_stats[dimension].astype(str).tolist()
        icon_hrefs = (
            [self._icons.keystone_href(label) for label in labels]
            if self._icons and dimension == "keystone"
            else [None] * len(labels)
        )
        fig = _horizontal_icon_bar(
            values=(rune_stats["winrate"] * 100).tolist(),
            labels=labels,
            icon_hrefs=icon_hrefs,
            colors=[ACCENT] * len(rune_stats),
            text=[f"{g} games" for g in rune_stats["games"]],
            title=f"Win rate by {dimension.replace('_', ' ')}",
            xaxis_title="Win rate %",
        )
        return _div(fig)

    def objective_timing(self, objectives_df: pd.DataFrame) -> str:
        """Strip plot of objective take timings, split by presence."""
        if objectives_df.empty:
            return _div(go.Figure().update_layout(title="Objectives (no data)"))
        frame = objectives_df.copy()
        frame["Presence"] = frame["present"].map({True: "Present", False: "Absent"})
        fig = px.strip(
            frame, x="minute", y="kind", color="Presence",
            color_discrete_map={"Present": WIN_COLOR, "Absent": LOSS_COLOR},
        )
        fig.update_layout(title="Objective takes and your presence", xaxis_title="Minute")
        return _div(fig)

    def peer_comparison_chart(self, comparisons: list[Any], *, build_label: str) -> str:
        """Horizontal bar chart of % gap vs rank-peer averages."""
        if not comparisons:
            return _div(go.Figure().update_layout(title="Rank peer comparison (unavailable)"))
        labels = [c.label for c in comparisons]
        deltas = []
        colors = []
        for c in comparisons:
            if c.delta_pct is None:
                deltas.append(0.0)
                gap_score = score_peer_gap(
                    metric=c.metric,
                    delta_pct=None,
                    delta=c.delta,
                    direction=c.direction,
                )
                colors.append(interpolate_metric_color(gap_score) if gap_score is not None else NEUTRAL_HEX)
                continue
            if c.direction == "lower":
                pct = -c.delta_pct
            else:
                pct = c.delta_pct
            deltas.append(pct)
            gap_score = score_peer_gap(
                metric=c.metric,
                delta_pct=c.delta_pct,
                delta=c.delta,
                direction=c.direction,
            )
            colors.append(interpolate_metric_color(gap_score) if gap_score is not None else NEUTRAL_HEX)
        compressed = [_compress_signed(d) for d in deltas]
        fig = go.Figure(go.Bar(
            x=compressed,
            y=labels,
            orientation="h",
            marker_color=colors,
            text=[f"{d:+.0f}%" for d in deltas],
            textposition="outside",
            customdata=deltas,
            hovertemplate="%{y}: %{customdata:+.0f}%<extra></extra>",
        ))
        fig.add_vline(x=0, line_color="#888", line_dash="dot")
        fig.update_layout(
            title=f"Gap vs average {build_label} at your rank (% difference)",
            xaxis=_compressed_xaxis(
                raw_values=deltas,
                title="% above (+) or below (-) peers (compressed scale)",
                suffix="%",
            ),
            height=max(420, 28 * len(labels)),
        )
        return _div(fig)

    def form_rolling_wr(
        self,
        matches_df: pd.DataFrame,
        *,
        recent_n: int,
        baseline_m: int,
    ) -> str:
        """Rolling win rate for the Form Tracker window only (recent + baseline)."""
        if matches_df.empty:
            return _div(go.Figure().update_layout(title="Form win rate (unavailable)"))
        frame = matches_df.sort_values("game_creation_ms").reset_index(drop=True)
        # Only plot the games Form Tracker compares (e.g. last 100 = 20 recent + 80 baseline).
        span = max(0, int(recent_n) + int(baseline_m))
        if span > 0 and len(frame) > span:
            frame = frame.iloc[-span:].reset_index(drop=True)
        window = max(5, min(20, len(frame) // 5))
        rolling = frame["win"].rolling(window, min_periods=3).mean() * 100
        fig = go.Figure()
        fig.add_scatter(
            y=rolling,
            mode="lines",
            name=f"WR ({window}-game rolling)",
            line=dict(color=ACCENT, width=3),
        )
        if len(frame) > recent_n:
            baseline_wr = float(frame.iloc[:-recent_n]["win"].mean()) * 100
            fig.add_hrect(
                y0=baseline_wr - 5,
                y1=baseline_wr + 5,
                fillcolor="rgba(136,136,136,0.15)",
                line_width=0,
                annotation_text="Baseline band",
                annotation_position="top left",
            )
        if recent_n > 0:
            fig.add_vrect(
                x0=max(0, len(frame) - recent_n),
                x1=len(frame) - 1,
                fillcolor="rgba(0, 200, 150, 0.08)",
                line_width=0,
            )
        fig.add_hline(y=50, line_dash="dot", line_color="#888")
        fig.update_layout(
            title="Win rate trend (recent window shaded)",
            xaxis_title="Game #",
            yaxis_title="Win rate %",
        )
        return _div(fig)

    def form_metric_delta_bar(self, deltas: list[Any]) -> str:
        """Horizontal bar chart of top metric deltas (recent vs baseline)."""
        if not deltas:
            return _div(go.Figure().update_layout(title="Metric deltas (unavailable)"))
        ranked = sorted(
            deltas,
            key=lambda delta: form_delta_rank_magnitude(
                {
                    "metric": delta.metric,
                    "delta": delta.delta,
                    "direction": delta.direction,
                }
            ),
            reverse=True,
        )[:8]
        labels = [delta.label for delta in ranked]
        values = []
        colors = []
        bar_text = []
        for delta in ranked:
            row = {
                "metric": delta.metric,
                "delta": delta.delta,
                "direction": delta.direction,
                "baseline": delta.baseline,
                "delta_pct": delta.delta_pct,
            }
            gap_display, gap_score = _form_gap_display_and_score(row)
            value = 0.0 if delta.verdict == "inline" else form_delta_chart_value(row)
            values.append(value)
            if delta.verdict == "inline":
                colors.append(NEUTRAL_HEX)
            else:
                colors.append(interpolate_metric_color(gap_score) if gap_score is not None else NEUTRAL_HEX)
            bar_text.append(gap_display)
        compressed = [_compress_signed(v) for v in values]
        fig = go.Figure(go.Bar(
            x=compressed,
            y=labels,
            orientation="h",
            marker_color=colors,
            text=bar_text,
            textposition="outside",
            customdata=values,
            hovertemplate="%{y}: %{customdata:+.1f}<extra></extra>",
        ))
        fig.add_vline(x=0, line_color="#888", line_dash="dot")
        fig.update_layout(
            title="Largest recent vs baseline shifts",
            xaxis=_compressed_xaxis(
                raw_values=values,
                title="Change from baseline (% · lane diffs; compressed scale)",
            ),
            height=max(320, 28 * len(labels)),
        )
        return _div(fig)

    def game_gold_timeline(
        self,
        timeline_points: list[dict[str, float]],
        death_minutes: list[float] | None = None,
        *,
        metric: str = "gold",
        mode: str = "lane",
    ) -> str:
        """Two-line resource timeline (ally vs enemy) for one game.

        The Game Review Story tab builds this interactively in the browser;
        this helper remains for tests / ad-hoc exports.
        """
        _ = death_minutes
        if not timeline_points:
            return _div(go.Figure().update_layout(title="Game timeline (unavailable)"))
        metric_key = metric if metric in {"gold", "xp", "cs"} else "gold"
        if mode == "team":
            ally_key, enemy_key = f"ally_{metric_key}", f"enemy_{metric_key}"
            mode_label = "team"
        else:
            ally_key, enemy_key = metric_key, f"opp_{metric_key}"
            mode_label = "lane"
        minutes = [point.get("minute", 0) for point in timeline_points]
        diffs = [
            float(point.get(ally_key, 0) or 0) - float(point.get(enemy_key, 0) or 0)
            for point in timeline_points
        ]
        ahead = [value if value > 0 else 0 for value in diffs]
        behind = [value if value < 0 else 0 for value in diffs]
        title = {
            "gold": "Gold",
            "xp": "XP",
            "cs": "CS",
        }.get(metric_key, "Gold")
        fig = go.Figure()
        fig.add_scatter(
            x=minutes,
            y=ahead,
            mode="lines",
            line=dict(width=0, color=WIN_COLOR),
            fill="tozeroy",
            fillcolor="rgba(63, 182, 139, 0.28)",
            hoverinfo="skip",
            showlegend=False,
        )
        fig.add_scatter(
            x=minutes,
            y=behind,
            mode="lines",
            line=dict(width=0, color=LOSS_COLOR),
            fill="tozeroy",
            fillcolor="rgba(224, 85, 99, 0.28)",
            hoverinfo="skip",
            showlegend=False,
        )
        fig.add_scatter(
            x=minutes,
            y=diffs,
            mode="lines",
            name=f"{title} diff",
            line=dict(color=TEXT_COLOR, width=2.5),
        )
        fig.update_layout(
            title=f"{title} diff ({mode_label})",
            xaxis_title="Minute",
            yaxis_title=f"{title} diff",
            height=360,
            showlegend=False,
        )
        return _div(fig)


def structure_pressure_timeline(rows: list[dict[str, float | int]]) -> str:
    """Bar chart of tower damage / tower kills per time bucket for Game Review."""
    if not rows:
        return ""
    minutes = [float(row.get("minute", 0)) for row in rows]
    damage = [float(row.get("tower_damage", 0) or 0) for row in rows]
    towers = [float(row.get("towers", 0) or 0) for row in rows]
    fig = go.Figure()
    fig.add_bar(x=minutes, y=damage, name="Tower damage", marker_color=ACCENT, opacity=0.85)
    fig.add_scatter(
        x=minutes,
        y=[value * 500 for value in towers],
        mode="lines+markers",
        name="Towers taken",
        line=dict(color=WIN_COLOR, width=2),
    )
    fig.update_layout(
        title="Structure pressure by phase",
        xaxis_title="Minute",
        yaxis_title="Tower damage",
        height=280,
        barmode="overlay",
    )
    return _div(fig)
