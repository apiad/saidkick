"""Per-tab console and network capture.

When a page misbehaves, the agent's options without this are guessing or
screenshotting. A console error or a 500 on an XHR is usually the actual
answer, and it is cheap to keep a bounded ring of both.

Bounded on purpose: an unbounded buffer on a chatty page is a memory leak in a
long-lived daemon.
"""

import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .engine import ManagedTab

RING = 200


class TabCapture:
    """Console messages and network results for one tab."""

    def __init__(self, size: int = RING):
        self.console: deque[dict] = deque(maxlen=size)
        self.network: deque[dict] = deque(maxlen=size)

    def install(self, tab: "ManagedTab") -> None:
        page = tab.page
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)

    # -- handlers ---------------------------------------------------------

    def _on_console(self, msg) -> None:
        self.console.append(
            {"level": msg.type, "text": msg.text, "ts": time.time()}
        )

    def _on_page_error(self, error) -> None:
        # An uncaught exception never reaches page.on("console"), and it is
        # usually the most important thing that happened.
        self.console.append(
            {"level": "pageerror", "text": str(error), "ts": time.time()}
        )

    def _on_response(self, response) -> None:
        self.network.append(
            {
                "method": response.request.method,
                "url": response.url,
                "status": response.status,
                "ok": response.ok,
                "ts": time.time(),
            }
        )

    def _on_request_failed(self, request) -> None:
        self.network.append(
            {
                "method": request.method,
                "url": request.url,
                "status": None,
                "ok": False,
                "error": request.failure,
                "ts": time.time(),
            }
        )

    # -- readers ----------------------------------------------------------

    def read_console(self, grep: str | None = None, level: str | None = None) -> list[dict]:
        out = list(self.console)
        if level:
            out = [e for e in out if e["level"] == level]
        if grep:
            needle = grep.lower()
            out = [e for e in out if needle in e["text"].lower()]
        return out

    def read_network(self, failed_only: bool = False, grep: str | None = None) -> list[dict]:
        out = list(self.network)
        if failed_only:
            out = [e for e in out if not e["ok"]]
        if grep:
            needle = grep.lower()
            out = [e for e in out if needle in e["url"].lower()]
        return out
