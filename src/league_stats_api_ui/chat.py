"""Server-side Gemini proxy for the report chatbot.

Mirrors the client-side chat in the Report SPA page: same models, same system
instruction built from the report's ``summary.json`` — but the API key stays
on the server.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Final

import requests
from prometheus_client import Histogram

from league_stats_common.utils import get_logger

log = get_logger("chat")

GEMINI_MODEL: Final[str] = "gemini-3.5-flash"
GEMINI_FALLBACK_MODEL: Final[str] = "gemini-3.1-flash-lite"
REQUEST_TIMEOUT_S: Final[float] = 60.0

# API-UI's outbound-dependency latency metric. Defined here (a leaf module
# with no imports back into `app.py`/`worker.py`) rather than in `app.py`
# itself, so both `app.py` (`resolve_puuid`, a synchronous in-request Riot
# call) and `league_stats_runner.worker` (RUNNER's `EnqueueJob`/
# `StreamJobProgress`/`RequestBaseline` gRPC calls, made from API-UI's/
# CronWatch's background `AnalysisWorker` thread) can import one shared
# collector without either direction creating an import cycle -- `app.py`
# already imports from `worker.py`, so `worker.py` importing back from
# `app.py` would cycle; importing from this leaf module instead does not.
OUTBOUND_RPC_DURATION = Histogram(
    "api_ui_outbound_call_duration_seconds",
    "Time API-UI (or its background worker) waited on one outbound dependency call.",
    ["target", "operation", "outcome"],
)

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


def _call_gemini(
    api_key: str, model: str, system_instruction: str, history: list[dict[str, Any]]
) -> str:
    log.info("Calling Gemini: model=%s, history=%d message(s)", model, len(history))
    start = time.perf_counter()
    outcome = "error"
    try:
        try:
            response = requests.post(
                _gemini_url(model),
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                json={
                    "systemInstruction": {"parts": [{"text": system_instruction}]},
                    "contents": history,
                    "generationConfig": {"temperature": 0.4, "maxOutputTokens": 800},
                },
                timeout=REQUEST_TIMEOUT_S,
            )
        except requests.Timeout:
            outcome = "timeout"
            raise
        except requests.RequestException:
            log.warning(
                "Gemini call failed: model=%s, took=%.1fs", model, time.perf_counter() - start
            )
            raise
        if response.status_code != 200:
            log.warning(
                "Gemini call returned HTTP %d: model=%s, took=%.1fs",
                response.status_code,
                model,
                time.perf_counter() - start,
            )
            try:
                detail = response.json().get("error", {}).get("message", "")
            except ValueError:
                detail = ""
            raise ChatError(detail or f"Gemini returned HTTP {response.status_code}")
        payload = response.json()
        candidates = payload.get("candidates") or []
        parts = ((candidates[0] if candidates else {}).get("content") or {}).get("parts") or []
        text = "".join(str(part.get("text", "")) for part in parts)
        if not text:
            raise ChatError("Gemini returned an empty response")
        outcome = "ok"
        log.info(
            "Gemini call succeeded: model=%s, took=%.1fs", model, time.perf_counter() - start
        )
        return text
    finally:
        OUTBOUND_RPC_DURATION.labels(
            target="gemini", operation="generateContent", outcome=outcome
        ).observe(time.perf_counter() - start)


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
