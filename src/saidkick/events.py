"""In-memory event bus with a monotonic sequence number.

Agents read events by polling with ``since_seq`` rather than receiving pushes.
MCP has a notification mechanism but client support for it is uneven, and
betting the agent surface on push would make saidkick work well on some
runtimes and mysteriously not on others. A sequence number works everywhere.

The WebSocket stream in the API layer still offers real push for the cockpit
and for non-MCP clients.
"""

import asyncio
import time
from collections import defaultdict, deque
from typing import Any

RING_SIZE = 500


class EventBus:
    def __init__(self, ring_size: int = RING_SIZE):
        self._seq = 0
        self._ring_size = ring_size
        self._events: dict[str, deque] = defaultdict(lambda: deque(maxlen=ring_size))
        self._wakeups: dict[str, asyncio.Event] = defaultdict(asyncio.Event)

    def emit(self, ctx_id: str, kind: str, **data: Any) -> int:
        self._seq += 1
        self._events[ctx_id].append(
            {"seq": self._seq, "ts": time.time(), "ctx": ctx_id, "kind": kind, **data}
        )
        wake = self._wakeups[ctx_id]
        wake.set()
        wake.clear()
        return self._seq

    def since(self, ctx_id: str, seq: int) -> list[dict]:
        return [e for e in self._events[ctx_id] if e["seq"] > seq]

    async def wait(self, ctx_id: str, seq: int, timeout_s: float = 25.0) -> list[dict]:
        """Return events after ``seq``, blocking up to ``timeout_s`` for the first."""
        pending = self.since(ctx_id, seq)
        if pending:
            return pending
        try:
            await asyncio.wait_for(self._wakeups[ctx_id].wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return []
        return self.since(ctx_id, seq)

    def latest_seq(self) -> int:
        return self._seq
