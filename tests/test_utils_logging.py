"""Tests for utils.py's structured logging: service/version/trace_id tagging."""

from __future__ import annotations

import logging

import pytest

from league_stats.utils import current_trace_id, get_logger, set_trace_id, setup_logging


@pytest.fixture(autouse=True)
def _reset_logger_state():
    """Undo `setup_logging`'s handler/level side effects and trace id between tests.

    `setup_logging` only adds a handler once per logger (see its `if not
    logger.handlers` guard) -- without resetting, later tests would silently
    reuse the first test's service/version-baked filter instead of getting
    their own.
    """
    logger = logging.getLogger("league_champion_analyzer")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    set_trace_id("")
    yield
    logger.handlers = original_handlers
    logger.setLevel(original_level)
    set_trace_id("")


def test_setup_logging_tags_records_with_service_version_and_trace_id():
    logger = setup_logging(service="test-service", version="v1.2.3")
    set_trace_id("abc123")

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = _Capture()
    logger.addHandler(capture)
    try:
        logger.info("hello")
    finally:
        logger.removeHandler(capture)

    assert len(records) == 1
    record = records[0]
    assert record.service == "test-service"
    assert record.version == "v1.2.3"
    assert record.trace_id == "abc123"


def test_trace_id_is_read_fresh_on_every_log_call_not_cached():
    logger = setup_logging(service="test-service", version="v1.2.3")

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = _Capture()
    logger.addHandler(capture)
    try:
        set_trace_id("first-trace")
        logger.info("first message")

        set_trace_id("second-trace")
        logger.info("second message")
    finally:
        logger.removeHandler(capture)

    assert len(records) == 2
    assert records[0].trace_id == "first-trace"
    assert records[1].trace_id == "second-trace"
    assert records[0].trace_id != records[1].trace_id


def test_current_trace_id_defaults_to_empty_string():
    assert current_trace_id() == ""


def test_set_trace_id_updates_current_trace_id():
    set_trace_id("xyz")
    assert current_trace_id() == "xyz"


def test_get_logger_child_inherits_parent_handlers():
    setup_logging(service="test-service", version="v1.2.3")
    child = get_logger("some_child")
    assert child.name == "league_champion_analyzer.some_child"
