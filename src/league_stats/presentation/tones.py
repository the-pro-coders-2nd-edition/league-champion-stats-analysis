"""Python mirror of frontend/src/tones.js (report-tones.js).

One file decides what "good" means, what a score is called, and how a career
node looks -- on both the JS side (design-tool preview + any future client
islands) and the Python side (resolving real report data into props for the
generated static templates, since the generation step only bakes in Jinja
tokens, not real numbers). Keep the two files in exact sync; tests/test_tones.py
checks worked examples against both.

Unlike frontend/src/tones.js's careerNode(), this module doesn't build literal
CSS gradient strings -- it returns a tone name and a percentage, and component
CSS resolves the gradient via `var(--tone-{tone}-line)` custom properties
(see templates/static/design-tokens.css). Keeps Python ignorant of hex values.
"""

from __future__ import annotations

from typing import Literal

Tone = Literal["good", "warn", "bad", "flat", "accent"]

WINDOW = 20


def delta_tone(delta: float | None, polarity: int = 1) -> Tone:
    """Tone for a delta value.

    Args:
        delta: The delta value, or ``None`` when there's no peer baseline.
        polarity: ``1`` when higher is better, ``-1`` when lower is better.
    """
    if delta is None or delta == 0:
        return "flat"
    good = delta * polarity
    if good > 0:
        return "good"
    return "warn" if good > -8 else "bad"


def delta_label(delta: float | None, polarity: int = 1, ref: str | None = None) -> str:
    """Human-readable delta string, e.g. ``'▲ 7% vs Emerald'``."""
    if delta is None:
        return "no peer baseline"
    arrow = "—" if delta == 0 else ("▲" if delta > 0 else "▼")
    suffix = f" vs {ref}" if ref else ""
    return f"{arrow} {abs(delta):g}%{suffix}"


def verdict(score: float) -> tuple[str, Tone]:
    """Verdict ``(label, tone)`` for a 0-100 score."""
    if score >= 70:
        return ("Strength", "good")
    if score >= 45:
        return ("Solid", "flat")
    if score >= 40:
        return ("Watch", "warn")
    return ("Focus", "bad")


def p_value(p: float | None) -> str:
    """Formatted p-value string, or ``'descriptive'`` when there's none."""
    if p is None:
        return "descriptive"
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def priority_tone(priority: str, side: str) -> Tone:
    """Tone for a recommendation card given its priority and keep/work side."""
    if side == "keep":
        return "good"
    if priority == "High":
        return "bad"
    if priority == "Medium":
        return "warn"
    return "flat"


def career_count(state: str, hit: int, window: int = WINDOW) -> str:
    """Count string for a CareerNode, e.g. ``'12 of 20'``."""
    if state == "Locked":
        return "blocked"
    return f"{hit} of {window}"


def career_node(state: str, hit: int, need: int) -> dict[str, object]:
    """Tone and ring-fill percentage for a CareerNode's five states."""
    pct = min(100, round((hit / need) * 100)) if need else 0
    tone_by_state: dict[str, Tone] = {
        "Cleared": "good",
        "Revoked": "bad",
        "In progress": "warn",
        "At risk": "warn",
        "Locked": "flat",
    }
    return {"tone": tone_by_state.get(state, "flat"), "pct": pct}
