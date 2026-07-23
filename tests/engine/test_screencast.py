import asyncio

import pytest

from saidkick.screencast import ScreencastPump

pytestmark = pytest.mark.browser


async def test_pump_delivers_more_than_one_frame(tab):
    """Without Page.screencastFrameAck Chromium sends exactly ONE frame, forever.

    The ack loop is the transport, not an optimization, so a bug there does not
    degrade the stream — it flatlines it after the first image, which looks
    exactly like a working screenshot. This is the regression guard.
    """
    pump = ScreencastPump(tab)
    q: asyncio.Queue = asyncio.Queue()
    pump.add_viewer(q)
    await pump.start(quality=60, max_width=800)
    for i in range(5):
        await tab.page.evaluate(f"document.body.style.background='#{i}{i}{i}{i}{i}{i}'")
        await asyncio.sleep(0.15)
    await pump.stop()
    assert q.qsize() > 1, "only one frame arrived: the ack loop is broken"


async def test_frame_carries_metadata_for_coordinate_mapping(tab):
    pump = ScreencastPump(tab)
    q: asyncio.Queue = asyncio.Queue()
    pump.add_viewer(q)
    await pump.start(quality=60, max_width=800)
    frame = await asyncio.wait_for(q.get(), timeout=5)
    await pump.stop()
    for key in ("deviceWidth", "deviceHeight", "pageScaleFactor", "scrollOffsetX", "scrollOffsetY"):
        assert key in frame["metadata"]
    assert frame["data"]


async def test_stops_when_last_viewer_leaves(tab):
    pump = ScreencastPump(tab)
    q: asyncio.Queue = asyncio.Queue()
    pump.add_viewer(q)
    await pump.start(quality=60, max_width=800)
    assert pump.running is True
    await pump.remove_viewer(q)
    assert pump.running is False


async def test_does_not_stop_while_a_second_viewer_remains(tab):
    pump = ScreencastPump(tab)
    a: asyncio.Queue = asyncio.Queue()
    b: asyncio.Queue = asyncio.Queue()
    pump.add_viewer(a)
    pump.add_viewer(b)
    await pump.start(quality=60, max_width=800)
    await pump.remove_viewer(a)
    assert pump.running is True
    await pump.remove_viewer(b)
    assert pump.running is False


async def test_slow_viewer_does_not_stall_the_pump(tab):
    """A full viewer queue must drop its oldest frame, not block the stream."""
    pump = ScreencastPump(tab)
    slow: asyncio.Queue = asyncio.Queue(maxsize=2)
    fast: asyncio.Queue = asyncio.Queue()
    pump.add_viewer(slow)
    pump.add_viewer(fast)
    await pump.start(quality=60, max_width=800)
    for i in range(6):
        await tab.page.evaluate(f"document.body.style.background='#{i}{i}{i}'")
        await asyncio.sleep(0.12)
    await pump.stop()
    assert slow.qsize() <= 2
    assert fast.qsize() > 1


async def test_set_quality_restarts_the_stream(tab):
    pump = ScreencastPump(tab)
    q: asyncio.Queue = asyncio.Queue()
    pump.add_viewer(q)
    await pump.start(quality=60, max_width=800)
    await pump.set_quality(quality=95, max_width=1280)
    assert pump.quality == 95 and pump.max_width == 1280
    assert pump.running is True
    await pump.stop()
