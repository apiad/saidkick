"""Idle-context reaping.

Contexts are cheap to open and expensive to leave open: each one is live
Chromium state. An agent that opens contexts and then crashes leaks them
forever, and on a small host a handful of leaks is an OOM.

Two rules make reaping safe rather than hostile:

- never reap a context a human currently controls;
- never reap a context with a pending human request.

Both mean somebody is mid-rescue, and yanking the page away would destroy
exactly the work the human was doing.
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from .config import Settings

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Engine
    from .events import EventBus

log = logging.getLogger("saidkick.reaper")


def idle_candidates(engine: "Engine", ttl_s: float, now: float | None = None) -> list[str]:
    """Context ids eligible for reaping. Pure: no I/O, so it is unit-testable."""
    if ttl_s <= 0:
        return []
    now = now if now is not None else time.monotonic()
    controller = engine.controller
    out = []
    for ctx in list(engine._contexts.values()):
        if now - ctx.last_activity < ttl_s:
            continue
        if controller is not None:
            if controller.state(ctx.id) == "human":
                continue  # a human is driving
            if controller.pending(ctx.id) is not None:
                continue  # a human was asked for help and has not answered
        out.append(ctx.id)
    return out


async def reap_idle(
    engine: "Engine", ttl_s: float, events: "EventBus | None" = None
) -> list[str]:
    reaped = []
    for ctx_id in idle_candidates(engine, ttl_s):
        try:
            await engine.close_context(ctx_id)
        except Exception as exc:  # noqa: BLE001 - a reap must never kill the daemon
            log.warning("failed to reap %s: %s", ctx_id, exc)
            continue
        reaped.append(ctx_id)
        log.info("reaped idle context %s", ctx_id)
        if events is not None:
            events.emit(ctx_id, "context_reaped", reason="idle")
    return reaped


async def run_reaper(
    engine: "Engine", settings: Settings, events: "EventBus | None" = None
) -> None:
    """Periodic reaper task. Cancelled with the daemon."""
    if settings.idle_ttl_s <= 0:
        return
    while True:
        await asyncio.sleep(settings.reap_interval_s)
        try:
            await reap_idle(engine, settings.idle_ttl_s, events)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("reaper cycle failed: %s", exc)
