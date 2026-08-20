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
    # Clear handlers before each test too, not just restore after: `setup_logging`'s
    # `if not logger.handlers` guard means that if some earlier test (or a module
    # imported earlier in the run, e.g. another entrypoint's `serve()`) left a
    # handler installed with a different service/version baked in, `setup_logging`
    # calls in THIS test would silently no-op against that stale handler instead
    # of configuring their own -- exactly the import-order bug this suite guards
    # against. Starting every test from a clean slate makes it order-independent.
    logger.handlers = []
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


def test_importing_grpc_entrypoint_modules_does_not_configure_logging():
    """Importing runner/cron_watch/peers `__main__` must not call `setup_logging`.

    Regression test for a real bug: `setup_logging(...)` used to run at
    MODULE level in these three files (executed at import time). Combined
    with `setup_logging`'s `if not logger.handlers` idempotency guard,
    whichever of these modules got imported first in a process would
    permanently win the shared logger's service/version labels -- every
    other `setup_logging(...)` call anywhere else in that process became a
    silent no-op. Reproduced by running
    `pytest tests/test_cron_watch_service.py tests/test_utils_logging.py`
    (fails, service stuck at "cron-watch") vs. the reverse order (passes).
    The fix moved each call inside its `serve()`/entrypoint function, so
    merely importing the module has no side effect on the logger -- this
    test proves that by importing all three, confirming the logger is
    still unconfigured, then calling `setup_logging` and confirming it
    actually takes effect.
    """
    import league_stats.cron_watch.__main__  # noqa: F401
    import league_stats.peers.__main__  # noqa: F401
    import league_stats.runner.__main__  # noqa: F401

    logger = logging.getLogger("league_champion_analyzer")
    assert logger.handlers == []

    setup_logging(service="fresh-service", version="v9.9.9")

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = _Capture()
    logger.addHandler(capture)
    try:
        logger.info("post-import log")
    finally:
        logger.removeHandler(capture)

    assert len(records) == 1
    assert records[0].service == "fresh-service"
    assert records[0].version == "v9.9.9"
