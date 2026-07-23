"""The browser engine: profiles, contexts, tabs.

This layer owns Playwright and nothing else. It has no notion that a human
might interrupt an agent, no HTTP, and no MCP. That separation is what lets
browser semantics be tested against a fixture server without simulating a
human, and arbitration be tested without a browser.

The one exception is a ``controller`` reference threaded through so that the
action layer can ask "may the agent act on this context right now?" — the
engine itself never consults it.
"""

import secrets
from typing import TYPE_CHECKING, Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import Error as PWError

from . import errors as E

if TYPE_CHECKING:  # pragma: no cover
    from .control import Controller

DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
NAV_WAITS = ("load", "domcontentloaded", "networkidle", "commit")


class ManagedTab:
    """A page inside a context, addressed as ``ctx_a1b2:3``."""

    def __init__(self, tab_id: str, page: Page, context: "ManagedContext"):
        self.id = tab_id
        self.page = page
        self.context = context

    async def navigate(self, url: str, wait: str = "load", timeout_ms: int = 15000) -> dict:
        if wait not in NAV_WAITS:
            raise ValueError(f"wait must be one of {NAV_WAITS}, got {wait!r}")
        try:
            await self.page.goto(url, wait_until=wait, timeout=timeout_ms)
        except PWError as exc:
            raise E.NavigationFailed(f"could not navigate to {url}: {exc.message}") from exc
        return self.info()

    def info(self) -> dict:
        return {"id": self.id, "url": self.page.url, "title": None}

    async def describe(self) -> dict:
        return {"id": self.id, "url": self.page.url, "title": await self.page.title()}


class ManagedContext:
    """A live browsing session. Owns its tabs and their CDP sessions."""

    def __init__(self, ctx_id: str, pw_context: BrowserContext, engine: "Engine"):
        self.id = ctx_id
        self.pw_context = pw_context
        self.engine = engine
        self._tabs: dict[str, ManagedTab] = {}
        self._cdp: dict[str, CDPSession] = {}
        self._next_tab = 1

    @property
    def controller(self):
        return self.engine.controller

    async def open_tab(self, url: str | None = None, wait: str = "load") -> ManagedTab:
        page = await self.pw_context.new_page()
        # Counter is never rewound, so a closed tab's id stays permanently
        # invalid instead of silently rebinding to a different page.
        tab_id = f"{self.id}:{self._next_tab}"
        self._next_tab += 1
        tab = ManagedTab(tab_id, page, self)
        self._tabs[tab_id] = tab
        if url:
            await tab.navigate(url, wait=wait)
        return tab

    def get_tab(self, tab_id: str) -> ManagedTab:
        try:
            return self._tabs[tab_id]
        except KeyError:
            raise E.NoSuchTab(f"no such tab: {tab_id}") from None

    async def close_tab(self, tab_id: str) -> None:
        tab = self.get_tab(tab_id)
        session = self._cdp.pop(tab_id, None)
        if session is not None:
            try:
                await session.detach()
            except PWError:
                pass  # page already gone
        await tab.page.close()
        del self._tabs[tab_id]

    def list_tabs(self) -> list[dict]:
        return [t.info() for t in self._tabs.values()]

    async def cdp(self, tab: ManagedTab) -> CDPSession:
        """A CDP session for this tab, created once and cached."""
        if tab.id not in self._cdp:
            self._cdp[tab.id] = await self.pw_context.new_cdp_session(tab.page)
        return self._cdp[tab.id]

    def info(self) -> dict:
        state = self.controller.state(self.id) if self.controller else "agent"
        return {
            "id": self.id,
            "mode": "ephemeral",
            "controller": state,
            "tabs": self.list_tabs(),
        }

    async def close(self) -> None:
        for session in self._cdp.values():
            try:
                await session.detach()
            except PWError:
                pass
        self._cdp.clear()
        self._tabs.clear()
        await self.pw_context.close()


class Engine:
    """Owns Playwright and the live contexts."""

    def __init__(self, headless: bool = True, controller: "Controller | None" = None):
        self.headless = headless
        self.controller = controller
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, ManagedContext] = {}

    @property
    def is_running(self) -> bool:
        return self._browser is not None

    async def start(self) -> None:
        if self._pw is not None:
            return
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)

    async def stop(self) -> None:
        for ctx in list(self._contexts.values()):
            try:
                await ctx.close()
            except PWError:
                pass
        self._contexts.clear()
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def open_context(self, viewport: dict[str, int] | None = None) -> ManagedContext:
        if self._browser is None:
            raise E.EngineCrashed("engine is not started")
        pw_ctx = await self._browser.new_context(viewport=viewport or DEFAULT_VIEWPORT)
        ctx_id = f"ctx_{secrets.token_hex(2)}"
        while ctx_id in self._contexts:  # pragma: no cover - 1-in-65536
            ctx_id = f"ctx_{secrets.token_hex(2)}"
        ctx = ManagedContext(ctx_id, pw_ctx, self)
        self._contexts[ctx_id] = ctx
        return ctx

    def get_context(self, ctx_id: str) -> ManagedContext:
        try:
            return self._contexts[ctx_id]
        except KeyError:
            raise E.NoSuchContext(f"no such context: {ctx_id}") from None

    async def close_context(self, ctx_id: str) -> None:
        ctx = self.get_context(ctx_id)
        await ctx.close()
        del self._contexts[ctx_id]

    def list_contexts(self) -> list[dict]:
        return [c.info() for c in self._contexts.values()]

    def find_tab(self, tab_id: str) -> ManagedTab:
        """Resolve a fully-qualified ``ctx_x:n`` tab id without knowing its context."""
        ctx_id = tab_id.split(":", 1)[0]
        return self.get_context(ctx_id).get_tab(tab_id)

    def info(self) -> dict[str, Any]:
        return {"contexts": self.list_contexts(), "headless": self.headless}
