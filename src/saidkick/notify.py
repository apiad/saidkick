"""Optional outbound webhook.

Deliberately unbranded: saidkick ships no integration with any chat, mail or
push provider. A user points ``notify.webhook_url`` at whatever they use.

The built-in announcement channels are the terminal dashboard, the obligations
written into the MCP tool descriptions, and the in-page attention overlay. This
is only an escape hatch, and it is off by default.
"""

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("saidkick.notify")


async def post_webhook(url: str, payload: dict[str, Any]) -> None:
    """POST the payload. Never raises: a dead webhook must not break a rescue."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json=payload)
    except Exception as exc:  # noqa: BLE001 - deliberately total
        log.warning("webhook to %s failed: %s", url, exc)


def fire_and_forget(url: str, payload: dict[str, Any]) -> None:
    """Schedule a webhook without waiting for it."""
    try:
        asyncio.get_running_loop().create_task(post_webhook(url, payload))
    except RuntimeError:
        log.debug("no running loop; webhook to %s skipped", url)
