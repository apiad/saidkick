import asyncio

from saidkick.events import EventBus


def test_seq_is_monotonic_across_contexts():
    b = EventBus()
    assert b.emit("ctx_a", "nav", url="x") == 1
    assert b.emit("ctx_b", "nav", url="y") == 2


def test_since_filters_by_context_and_seq():
    b = EventBus()
    b.emit("ctx_a", "one")
    s = b.emit("ctx_a", "two")
    b.emit("ctx_b", "other")
    assert [e["kind"] for e in b.since("ctx_a", 0)] == ["one", "two"]
    assert [e["kind"] for e in b.since("ctx_a", s - 1)] == ["two"]


def test_ring_buffer_caps_at_500():
    b = EventBus()
    for i in range(600):
        b.emit("ctx_a", "e", i=i)
    kept = b.since("ctx_a", 0)
    assert len(kept) == 500 and kept[0]["i"] == 100


def test_event_carries_kind_and_context():
    b = EventBus()
    b.emit("ctx_a", "navigated", url="http://x/")
    e = b.since("ctx_a", 0)[0]
    assert e["ctx"] == "ctx_a" and e["kind"] == "navigated" and e["url"] == "http://x/"


async def test_wait_returns_immediately_when_events_pending():
    b = EventBus()
    b.emit("ctx_a", "one")
    assert len(await b.wait("ctx_a", 0, timeout_s=5)) == 1


async def test_wait_blocks_then_wakes_on_emit():
    b = EventBus()
    task = asyncio.create_task(b.wait("ctx_a", 0, timeout_s=5))
    await asyncio.sleep(0.05)
    assert not task.done()
    b.emit("ctx_a", "late")
    assert (await task)[0]["kind"] == "late"


async def test_wait_is_not_woken_by_another_context():
    b = EventBus()
    task = asyncio.create_task(b.wait("ctx_a", 0, timeout_s=0.3))
    await asyncio.sleep(0.05)
    b.emit("ctx_b", "unrelated")
    assert await task == []


async def test_wait_returns_empty_on_timeout():
    assert await EventBus().wait("ctx_a", 0, timeout_s=0.1) == []
