"""CDP screencast: JPEG frames from a tab to any number of viewers.

Two things about this are worth knowing before changing it.

**The ack is the transport.** Chromium emits a frame and then waits for
``Page.screencastFrameAck`` before emitting the next one. A session that never
acks receives exactly one frame, forever — which looks identical to a working
screenshot, so the failure is silent. Ack first, unconditionally, before any
work that could raise.

**JPEG, not PNG.** On localhost bandwidth is free, so the cost is CPU in
Chromium's encoder on every compositor update, and PNG's lossless deflate is
far more expensive per frame. Resolution is an independent knob, so full native
resolution and JPEG are not in tension.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from playwright.async_api import Error as PWError

if TYPE_CHECKING:  # pragma: no cover
    from .engine import ManagedTab

log = logging.getLogger("saidkick.screencast")

#: Observing: cheap and readable. Human holds control: buy fidelity exactly
#: when precise work is about to happen.
OBSERVE_QUALITY, OBSERVE_MAX_WIDTH = 60, 1280
TAKEOVER_QUALITY, TAKEOVER_MAX_WIDTH = 95, 1920


class ScreencastPump:
    def __init__(self, tab: "ManagedTab"):
        self.tab = tab
        self.running = False
        self.quality = OBSERVE_QUALITY
        self.max_width = OBSERVE_MAX_WIDTH
        self._cdp: Any = None
        self._viewers: list[asyncio.Queue] = []
        self._listening = False

    @property
    def viewer_count(self) -> int:
        return len(self._viewers)

    def add_viewer(self, queue: asyncio.Queue) -> None:
        self._viewers.append(queue)

    async def remove_viewer(self, queue: asyncio.Queue) -> None:
        if queue in self._viewers:
            self._viewers.remove(queue)
        if not self._viewers and self.running:
            # Screencasting to nobody burns CPU for nothing.
            await self.stop()

    async def start(self, quality: int | None = None, max_width: int | None = None) -> None:
        if quality is not None:
            self.quality = quality
        if max_width is not None:
            self.max_width = max_width

        self._cdp = await self.tab.context.cdp(self.tab)
        if not self._listening:
            self._cdp.on("Page.screencastFrame", self._on_frame)
            self._listening = True

        await self._cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": self.quality,
                "maxWidth": self.max_width,
                "maxHeight": self.max_width,
            },
        )
        self.running = True

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        try:
            await self._cdp.send("Page.stopScreencast")
        except PWError:
            pass  # tab already gone

    async def set_quality(self, quality: int, max_width: int) -> None:
        """Change fidelity mid-stream. Chromium applies this on restart only."""
        was_running = self.running
        await self.stop()
        self.quality, self.max_width = quality, max_width
        if was_running:
            await self.start()

    def _on_frame(self, event: dict) -> None:
        # Ack FIRST and unconditionally. Chromium sends no further frames until
        # this lands, so any early return above it flatlines the stream.
        asyncio.get_running_loop().create_task(self._ack(event["sessionId"]))

        frame = {"data": event["data"], "metadata": event["metadata"]}
        for queue in list(self._viewers):
            if queue.full():
                try:
                    queue.get_nowait()  # drop oldest; a slow viewer must not stall the pump
                except asyncio.QueueEmpty:  # pragma: no cover
                    pass
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:  # pragma: no cover
                pass

    async def _ack(self, session_id: str) -> None:
        try:
            await self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
        except PWError:
            pass  # tab closed mid-stream
        except Exception as exc:  # noqa: BLE001 - never let an ack kill the pump
            log.debug("screencast ack failed: %s", exc)
