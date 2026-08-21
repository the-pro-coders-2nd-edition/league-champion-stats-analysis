"""Server-side Gemini proxy for the report chatbot.

Mirrors the client-side chat in the Report SPA page: same models, same system
instruction built from the report's ``summary.json`` — but the API key stays
on the server.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import requests

GEMINI_MODEL: Final[str] = "gemini-3.5-flash"
GEMINI_FALLBACK_MODEL: Final[str] = "gemini-3.1-flash-lite"
REQUEST_TIMEOUT_S: Final[float] = 60.0
# Gemini 3.x thinking tokens count toward maxOutputTokens; keep thinking minimal
# for short coaching replies and leave room for the visible answer.
MAX_OUTPUT_TOKENS: Final[int] = 4096

MAX_HISTORY_MESSAGES: Final[int] = 40
MAX_MESSAGE_CHARS: Final[int] = 4000
MAX_CONTEXT_CHARS: Final[int] = 20000

TAB_LABELS: Final[dict[str, str]] = {
    "summary": "Summary",
    "games": "Games",
    "career": "Career",
    "performance": "Performance",
    "champion": "Champion",
    "deepdive": "Deepdive",
}


class ChatError(RuntimeError):
    """Raised when a chat request cannot be served."""


def _gemini_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def build_system_instruction(
    stats: dict[str, Any],
    build_label: str,
    player_name: str,
    tab: str | None = None,
) -> str:
    """Build the coaching system prompt (kept in sync with the Report SPA page)."""
    tab_label = TAB_LABELS.get(tab or "")
    scope_note = (
        f"The JSON below is scoped to the '{tab_label}' tab the player is currently "
        "viewing, not the full report. Answer from it, but say so plainly if the "
        "player asks about something outside that scope. "
        if tab_label
        else ""
    )
    return (
        "You are a League of Legends coaching assistant. Answer questions about the "
        f"player's stats using ONLY the JSON data below for {build_label} "
        f"({player_name}). Be concise, cite specific numbers when relevant, and "
        "say so plainly if the data does not cover something asked. "
        f"{scope_note}"
        "When the player asks about a specific recent game, use recent_games.games[]. "
        "Identify games by index (1 = most recent), date, opponent, or W/L. "
        "Cite game_score, archetype, and highlights when explaining a single game. "
        "If recent_games is empty or missing, say so plainly."
        "\n\nSTATS JSON:\n" + json.dumps(stats)
    )


def resolve_chat_stats(summary: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """Pick the stats blob to send to Gemini: a tab-scoped context if valid, else the full summary."""
    if context is None:
        return summary
    if len(json.dumps(context)) > MAX_CONTEXT_CHARS:
        return summary
    return context


def validate_history(history: Any) -> list[dict[str, Any]]:
    """Validate and normalise the chat history sent by the browser."""
    if not isinstance(history, list) or not history:
        raise ChatError("history must be a non-empty list")
    if len(history) > MAX_HISTORY_MESSAGES:
        history = history[-MAX_HISTORY_MESSAGES:]
    cleaned: list[dict[str, Any]] = []
    for message in history:
        if not isinstance(message, dict):
            raise ChatError("invalid history entry")
        role = message.get("role")
        if role not in ("user", "model"):
            raise ChatError(f"invalid history role: {role!r}")
        parts = message.get("parts")
        if not isinstance(parts, list):
            raise ChatError("invalid history parts")
        texts: list[dict[str, str]] = []
        for part in parts:
            text = str((part or {}).get("text", ""))[:MAX_MESSAGE_CHARS]
            texts.append({"text": text})
        cleaned.append({"role": role, "parts": texts})
    if cleaned[-1]["role"] != "user":
        raise ChatError("last history message must be from the user")
    return cleaned


def load_report_summary(reports_dir: Path, report_ref: str) -> dict[str, Any]:
    """Load ``summary.json`` for a ``{player_slug}/{build_slug}`` report ref."""
    parts = report_ref.split("/")
    if len(parts) != 2 or not all(_is_slug(part) for part in parts):
        raise ChatError("invalid report reference")
    summary_path = reports_dir / parts[0] / parts[1] / "summary.json"
    if not summary_path.is_file():
        raise ChatError("report not found")
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChatError("report summary unreadable") from exc


def _is_slug(value: str) -> bool:
    return bool(value) and all(ch.isalnum() or ch == "_" for ch in value)


def _extract_reply_text(parts: list[dict[str, Any]]) -> str:
    """Join visible model output, skipping internal thought summaries."""
    chunks: list[str] = []
    for part in parts:
        if part.get("thought"):
            continue
        text = part.get("text")
        if text:
            chunks.append(str(text))
    return "".join(chunks)


def _generation_config() -> dict[str, Any]:
    return {
        "temperature": 0.4,
        "maxOutputTokens": MAX_OUTPUT_TOKENS,
        # Coaching replies should be fast and concise; default "medium" thinking
        # on Gemini 3.5 Flash can consume most of the output token budget.
        "thinkingConfig": {"thinkingLevel": "minimal"},
    }


def _call_gemini(
    api_key: str, model: str, system_instruction: str, history: list[dict[str, Any]]
) -> str:
    response = requests.post(
        _gemini_url(model),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json={
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": history,
            "generationConfig": _generation_config(),
        },
        timeout=REQUEST_TIMEOUT_S,
    )
    if response.status_code != 200:
        try:
            detail = response.json().get("error", {}).get("message", "")
        except ValueError:
            detail = ""
        raise ChatError(detail or f"Gemini returned HTTP {response.status_code}")
    payload = response.json()
    candidates = payload.get("candidates") or []
    candidate = candidates[0] if candidates else {}
    parts = (candidate.get("content") or {}).get("parts") or []
    text = _extract_reply_text(parts)
    if not text:
        raise ChatError("Gemini returned an empty response")
    finish_reason = candidate.get("finishReason")
    if finish_reason == "MAX_TOKENS":
        text = text.rstrip() + "…"
    return text


def gemini_reply(
    api_key: str,
    *,
    stats: dict[str, Any],
    build_label: str,
    player_name: str,
    history: list[dict[str, Any]],
    tab: str | None = None,
) -> str:
    """Ask Gemini for a coaching reply, retrying once on the fallback model."""
    instruction = build_system_instruction(stats, build_label, player_name, tab)
    try:
        return _call_gemini(api_key, GEMINI_MODEL, instruction, history)
    except ChatError:
        return _call_gemini(api_key, GEMINI_FALLBACK_MODEL, instruction, history)
