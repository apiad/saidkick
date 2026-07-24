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
import time
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
from .profiles import ProfileStore

if TYPE_CHECKING:  # pragma: no cover
    from .config import Settings
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
        self.context.touch()
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

    def __init__(
        self,
        ctx_id: str,
        pw_context: BrowserContext,
        engine: "Engine",
        profile: str | None = None,
        mode: str = "ephemeral",
    ):
        self.id = ctx_id
        self.pw_context = pw_context
        self.engine = engine
        self.profile = profile
        self.mode = mode
        self._tabs: dict[str, ManagedTab] = {}
        self._cdp: dict[str, CDPSession] = {}
        self._next_tab = 1
        self.last_activity = time.monotonic()

    def touch(self) -> None:
        """Mark the context as used, so the reaper leaves it alone."""
        self.last_activity = time.monotonic()

    @property
    def idle_s(self) -> float:
        return time.monotonic() - self.last_activity

    def adopt_existing_pages(self) -> None:
        """Register pages the context was born with (a persistent context opens
        with one blank page) so list_tabs stays truthful and they are not leaked."""
        for page in self.pw_context.pages:
            tab_id = f"{self.id}:{self._next_tab}"
            self._next_tab += 1
            self._tabs[tab_id] = ManagedTab(tab_id, page, self)

    @property
    def controller(self):
        return self.engine.controller

    async def open_tab(self, url: str | None = None, wait: str = "load") -> ManagedTab:
        self.touch()
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
            "mode": self.mode,
            "profile": self.profile,
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

    def __init__(
        self,
        headless: bool = True,
        controller: "Controller | None" = None,
        store: "ProfileStore | None" = None,
        settings: "Settings | None" = None,
    ):
        self.headless = headless
        self.controller = controller
        self.store = store if store is not None else ProfileStore()
        # Settings are optional: the library path constructs an Engine with none.
        self.settings = settings
        self._crashed = False
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, ManagedContext] = {}
        # profile -> ctx_id of its live attached context. ProfileLocked is a
        # daemon invariant: Chromium does not lock a headless user-data-dir.
        self._attached: dict[str, str] = {}

    @property
    def is_running(self) -> bool:
        return self._browser is not None

    @property
    def crashed(self) -> bool:
        return self._crashed

    def _on_disconnected(self, _browser=None) -> None:
        """Chromium died. Mark it so the next open_context restarts rather than
        handing out contexts on a dead browser."""
        self._crashed = True

    def preflight(self) -> None:
        """Fail at startup, not at first use, if the browser is not installed.

        Without this the daemon binds happily and the first open_context dies
        with a Playwright stack trace that does not name the fix.
        """
        from playwright._impl._driver import compute_driver_executable  # noqa: PLC0415

        try:
            compute_driver_executable()
        except Exception as exc:  # noqa: BLE001
            raise E.EngineCrashed(
                "Playwright driver is unavailable. Run: uv run playwright install chromium"
            ) from exc

    async def _restart(self) -> None:
        """Bring Chromium back after a crash. Live contexts are gone regardless."""
        self._contexts.clear()
        self._attached.clear()
        try:
            if self._browser is not None:
                await self._browser.close()
        except PWError:
            pass
        if self._pw is None:  # pragma: no cover - start() guarantees this
            raise E.EngineCrashed("engine is not started")
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        self._browser.on("disconnected", self._on_disconnected)
        self._crashed = False

    async def start(self) -> None:
        if self._pw is not None:
            return
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        self._crashed = False
        self._browser.on("disconnected", self._on_disconnected)

    async def stop(self) -> None:
        for ctx in list(self._contexts.values()):
            try:
                await ctx.close()
            except PWError:
                pass
        self._contexts.clear()
        self._attached.clear()
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    def _new_ctx_id(self) -> str:
        ctx_id = f"ctx_{secrets.token_hex(2)}"
        while ctx_id in self._contexts:  # pragma: no cover - 1-in-65536
            ctx_id = f"ctx_{secrets.token_hex(2)}"
        return ctx_id

    async def open_context(
        self,
        profile: str | None = None,
        mode: str = "ephemeral",
        viewport: dict[str, int] | None = None,
    ) -> ManagedContext:
        if self._pw is None:
            raise E.EngineCrashed("engine is not started")
        if self._crashed:
            # Chromium died under us; bring it back rather than handing out
            # contexts on a dead browser.
            await self._restart()
        cap = self.settings.max_contexts if self.settings else None
        if cap is not None and len(self._contexts) >= cap:
            raise E.TooManyContexts(
                f"context cap reached ({cap}); close a context you are finished with"
            )
        viewport = viewport or DEFAULT_VIEWPORT
        ctx_id = self._new_ctx_id()

        if mode == "attached":
            if not profile:
                raise ValueError("attached mode requires a profile")
            if profile in self._attached:
                raise E.ProfileLocked(
                    f"profile {profile!r} already has a live attached context "
                    f"({self._attached[profile]}); close it first"
                )
            self.store.userdata(profile).mkdir(parents=True, exist_ok=True)
            pw_ctx = await self._pw.chromium.launch_persistent_context(
                str(self.store.userdata(profile)),
                headless=self.headless,
                viewport=viewport,
            )
            ctx = ManagedContext(ctx_id, pw_ctx, self, profile=profile, mode="attached")
            # A persistent context opens with one blank page; adopt it so it is
            # tracked rather than leaked.
            ctx.adopt_existing_pages()
            self._attached[profile] = ctx_id
        elif mode == "ephemeral":
            if self._browser is None:
                raise E.EngineCrashed("engine is not started")
            seed = None
            if profile and self.store.state_file(profile).is_file():
                seed = str(self.store.state_file(profile))
            pw_ctx = await self._browser.new_context(viewport=viewport, storage_state=seed)
            ctx = ManagedContext(ctx_id, pw_ctx, self, profile=profile, mode="ephemeral")
        else:
            raise ValueError(f"unknown context mode: {mode!r} (expected ephemeral or attached)")

        self._contexts[ctx_id] = ctx
        return ctx

    async def save_profile(self, ctx_id: str, name: str) -> dict:
        """Capture a context's storage state to disk, so the next context on this
        profile starts logged in. This is how a human-solved login becomes durable."""
        ctx = self.get_context(ctx_id)
        state = await ctx.pw_context.storage_state()
        self.store.save_state(name, state)
        return {
            "profile": name,
            "cookies": len(state.get("cookies", [])),
            "origins": len(state.get("origins", [])),
        }

    def get_context(self, ctx_id: str) -> ManagedContext:
        try:
            return self._contexts[ctx_id]
        except KeyError:
            raise E.NoSuchContext(f"no such context: {ctx_id}") from None

    async def close_context(self, ctx_id: str) -> None:
        ctx = self.get_context(ctx_id)
        await ctx.close()
        if ctx.profile and self._attached.get(ctx.profile) == ctx_id:
            del self._attached[ctx.profile]
        del self._contexts[ctx_id]

    def list_contexts(self) -> list[dict]:
        return [c.info() for c in self._contexts.values()]

    def find_tab(self, tab_id: str) -> ManagedTab:
        """Resolve a fully-qualified ``ctx_x:n`` tab id without knowing its context."""
        ctx_id = tab_id.split(":", 1)[0]
        return self.get_context(ctx_id).get_tab(tab_id)

    def info(self) -> dict[str, Any]:
        return {"contexts": self.list_contexts(), "headless": self.headless}
