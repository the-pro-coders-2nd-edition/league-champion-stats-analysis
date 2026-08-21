"""Tests for `JobEventBus`, the in-process pub/sub SSE handlers subscribe to.

No `pytest-asyncio` in this repo's dependencies -- each test is a plain
(sync) pytest function that drives its own coroutine via `asyncio.run(...)`,
matching how the rest of this suite avoids adding new test-only dependencies.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from league_stats_api_ui.job_events import JobEventBus


def test_publish_wakes_a_same_thread_subscriber() -> None:
    async def scenario() -> int:
        bus = JobEventBus()
        bus.bind_loop(asyncio.get_running_loop())
        updates = await bus.subscribe("hugros_euw")
        bus.publish("hugros_euw")
        return await asyncio.wait_for(updates.__anext__(), timeout=1)

    assert asyncio.run(scenario()) == 1


def test_publish_only_wakes_subscribers_of_that_slug_and_the_wildcard() -> None:
    async def scenario() -> None:
        bus = JobEventBus()
        bus.bind_loop(asyncio.get_running_loop())
        slug_updates = await bus.subscribe("hugros_euw")
        other_updates = await bus.subscribe("someone_else")
        wildcard_updates = await bus.subscribe(None)

        bus.publish("hugros_euw")

        await asyncio.wait_for(slug_updates.__anext__(), timeout=1)
        await asyncio.wait_for(wildcard_updates.__anext__(), timeout=1)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(other_updates.__anext__(), timeout=0.1)

    asyncio.run(scenario())


def test_publish_from_a_real_thread_wakes_the_loop_subscriber() -> None:
    """Exercises `call_soon_threadsafe`, not just a same-thread call."""

    async def scenario() -> None:
        bus = JobEventBus()
        bus.bind_loop(asyncio.get_running_loop())
        updates = await bus.subscribe("hugros_euw")

        def publish_from_worker_thread() -> None:
            bus.publish("hugros_euw")

        thread = threading.Thread(target=publish_from_worker_thread)
        thread.start()
        thread.join()

        await asyncio.wait_for(updates.__anext__(), timeout=1)

    asyncio.run(scenario())


def test_generation_increments_once_per_publish_for_that_slug() -> None:
    async def scenario() -> None:
        bus = JobEventBus()
        bus.bind_loop(asyncio.get_running_loop())
        assert bus.generation("hugros_euw") == 0

        bus.publish("hugros_euw")
        await asyncio.sleep(0)
        assert bus.generation("hugros_euw") == 1

        bus.publish("hugros_euw")
        await asyncio.sleep(0)
        assert bus.generation("hugros_euw") == 2
        assert bus.generation("someone_else") == 0

    asyncio.run(scenario())


def test_publish_before_bind_loop_is_a_harmless_no_op() -> None:
    bus = JobEventBus()
    bus.publish("hugros_euw")  # must not raise


def test_disconnect_removes_the_subscriber_from_the_registry() -> None:
    async def scenario() -> None:
        bus = JobEventBus()
        bus.bind_loop(asyncio.get_running_loop())
        assert bus.subscriber_count("hugros_euw") == 0

        async def consume() -> None:
            updates = await bus.subscribe("hugros_euw")
            async for _ in updates:
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        assert bus.subscriber_count("hugros_euw") == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert bus.subscriber_count("hugros_euw") == 0

    asyncio.run(scenario())


def test_multiple_subscribers_of_the_same_slug_all_wake_with_the_same_generation() -> None:
    async def scenario() -> tuple[int, int]:
        bus = JobEventBus()
        bus.bind_loop(asyncio.get_running_loop())
        first = await bus.subscribe("hugros_euw")
        second = await bus.subscribe("hugros_euw")

        bus.publish("hugros_euw")

        gen_first = await asyncio.wait_for(first.__anext__(), timeout=1)
        gen_second = await asyncio.wait_for(second.__anext__(), timeout=1)
        return gen_first, gen_second

    gen_first, gen_second = asyncio.run(scenario())
    assert gen_first == gen_second == 1
