"""Bounded in-process live bus: fan-out, overflow and cleanup."""
from __future__ import annotations

from app.logs.bus import MAX_SUBSCRIBER_QUEUE, EventBus


def test_bus_fans_out_and_bounds_each_subscriber_queue() -> None:
    bus = EventBus()
    first = bus.subscribe()
    second = bus.subscribe()
    assert bus.subscriber_count == 2
    for index in range(MAX_SUBSCRIBER_QUEUE + 10):
        bus.publish({"id": index})

    assert first.qsize() == MAX_SUBSCRIBER_QUEUE
    assert second.qsize() == MAX_SUBSCRIBER_QUEUE
    # Overflow drops the oldest queued events, never grows without bound.
    assert first.get_nowait()["id"] == 10
    assert second.get_nowait()["id"] == 10

    bus.unsubscribe(first)
    assert bus.subscriber_count == 1
    bus.publish({"id": "after-unsubscribe"})
    assert second.qsize() == MAX_SUBSCRIBER_QUEUE
    assert second.get_nowait()["id"] == 11

    bus.unsubscribe(second)
    assert bus.subscriber_count == 0
    bus.publish({"id": "no-subscribers"})
    assert bus.subscriber_count == 0


def test_bus_reset_removes_all_subscribers() -> None:
    bus = EventBus()
    bus.subscribe()
    bus.subscribe()
    bus.reset()
    assert bus.subscriber_count == 0
