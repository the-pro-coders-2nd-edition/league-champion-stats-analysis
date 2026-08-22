"""Unit tests for the Gemini chat proxy."""

from __future__ import annotations

from league_stats.web.chat import (
    MAX_OUTPUT_TOKENS,
    _extract_reply_text,
    _generation_config,
)


def test_extract_reply_text_skips_thought_parts() -> None:
    parts = [
        {"text": "internal reasoning", "thought": True},
        {"text": "Visible answer."},
    ]
    assert _extract_reply_text(parts) == "Visible answer."


def test_extract_reply_text_joins_multiple_visible_parts() -> None:
    parts = [
        {"text": "First "},
        {"text": "second."},
    ]
    assert _extract_reply_text(parts) == "First second."


def test_generation_config_limits_thinking_and_raises_output_cap() -> None:
    config = _generation_config()
    assert config["maxOutputTokens"] == MAX_OUTPUT_TOKENS
    assert config["thinkingConfig"] == {"thinkingLevel": "minimal"}
