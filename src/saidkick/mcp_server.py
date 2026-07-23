"""The MCP agent surface.

The descriptions in this file are a deliverable, not documentation. They are
the only mechanism saidkick has to influence agent behaviour, and for the
human-in-the-loop flow they are the most reliable notification channel in the
whole system: the agent already has a channel to its human, and telling it to
use that channel is how a request for help reaches a person when nobody is
watching the terminal or the cockpit.

So the descriptions carry obligations, not just signatures. They are reviewed
and revised like code, and there are tests asserting the obligations survive.
"""

import base64

from mcp.server.fastmcp import FastMCP

from . import actions as A
from .control import Controller
from .engine import Engine
from .events import EventBus
from .locators import Locator
from .snapshot import snapshot

LOCATOR_DOC = """
Locators target elements the way a user sees them. Set exactly ONE of:
`css`, `xpath`, `by_text`, `by_label`, `by_placeholder`, `by_role`
(`by_role` may be combined with `by_text`, which then means the accessible
name). Refine with `within_css` to scope, `nth` to disambiguate, `exact` or
`regex` to control matching, and `wait_ms` to poll until the element appears.

Prefer semantic locators over `css`: they survive redesigns, and the values
come straight out of `snapshot`.
""".strip()


def _loc(
    css: str | None = None,
    xpath: str | None = None,
    by_text: str | None = None,
    by_label: str | None = None,
    by_placeholder: str | None = None,
    by_role: str | None = None,
    within_css: str | None = None,
    nth: int | None = None,
    exact: bool = False,
    regex: bool = False,
    wait_ms: int = 0,
) -> Locator:
    return Locator(
        css=css, xpath=xpath, by_text=by_text, by_label=by_label,
        by_placeholder=by_placeholder, by_role=by_role, within_css=within_css,
        nth=nth, exact=exact, regex=regex, wait_ms=wait_ms,
    )


def build_mcp(
    engine: Engine, controller: Controller | None = None, events: EventBus | None = None
) -> FastMCP:
    controller = controller or engine.controller or Controller()
    engine.controller = controller
    events = events or EventBus()
    mcp = FastMCP("saidkick")

    # -- contexts ---------------------------------------------------------

    @mcp.tool(
        description=(
            "List every live browsing context and its tabs, with the id you pass to other "
            "tools and who currently holds control ('agent' or 'human'). Call this first if "
            "you do not already have a context id."
        )
    )
    async def list_contexts() -> list[dict]:
        return engine.list_contexts()

    @mcp.tool(
        description=(
            "Open a new browsing context and return its id. A context is an isolated cookie "
            "jar and storage partition: two contexts cannot see each other's logins, and "
            "tabs inside one context share session state.\n\n"
            "Contexts are EPHEMERAL — they start with empty storage and are discarded when "
            "closed. That is the correct default: it is reproducible and it cannot damage "
            "real logged-in state. Open one context per task rather than reusing one for "
            "unrelated work."
        )
    )
    async def open_context(viewport_width: int = 1280, viewport_height: int = 800) -> dict:
        ctx = await engine.open_context(
            viewport={"width": viewport_width, "height": viewport_height}
        )
        events.emit(ctx.id, "context_opened")
        return ctx.info()

    @mcp.tool(
        description=(
            "Close a context and every tab inside it, discarding its storage. Do this when "
            "your task is finished — abandoned contexts keep a browser window alive."
        )
    )
    async def close_context(context: str) -> dict:
        await engine.close_context(context)
        events.emit(context, "context_closed")
        return {"ok": True}

    # -- tabs -------------------------------------------------------------

    @mcp.tool(description="List the tabs inside a context, with their ids and current URLs.")
    async def list_tabs(context: str) -> list[dict]:
        return engine.get_context(context).list_tabs()

    @mcp.tool(
        description=(
            "Open a tab inside a context, optionally navigating to a URL, and return its id "
            "(formatted 'ctx_xxxx:n'). Use several tabs in one context when a task spans "
            "pages that should share a session."
        )
    )
    async def open_tab(context: str, url: str | None = None, wait: str = "load") -> dict:
        tab = await engine.get_context(context).open_tab(url, wait=wait)
        events.emit(context, "tab_opened", tab=tab.id, url=url)
        return tab.info()

    @mcp.tool(
        description=(
            "Close one tab, leaving its context and other tabs alive. The id is never "
            "reused, so a stale reference fails loudly rather than silently acting on a "
            "different page. To discard the whole session, use close_context instead."
        )
    )
    async def close_tab(tab: str) -> dict:
        ctx = engine.get_context(tab.split(":", 1)[0])
        await ctx.close_tab(tab)
        return {"ok": True}

    @mcp.tool(
        description=(
            "Navigate a tab to a URL. `wait` is one of 'load' (default), 'domcontentloaded', "
            "'networkidle' or 'commit'. Use 'networkidle' for single-page apps that fetch "
            "their content after the initial load."
        )
    )
    async def navigate(tab: str, url: str, wait: str = "load") -> dict:
        t = engine.find_tab(tab)
        out = await t.navigate(url, wait=wait)
        events.emit(t.context.id, "navigated", tab=tab, url=url)
        return out

    # -- reading ----------------------------------------------------------

    @mcp.tool(
        description=(
            "Read the page. THIS IS THE TOOL TO REACH FOR FIRST — call it before acting so "
            "you know what is actually on screen.\n\n"
            "mode='aria' (the default, and strongly preferred) returns an accessibility "
            "snapshot: a compact outline where every entry carries a role and an accessible "
            "name, e.g. 'button \"Send\"' or 'textbox \"Username\"'. Those map DIRECTLY onto "
            "locators — see 'button \"Send\"', then click with by_role='button', "
            "by_text='Send'.\n\n"
            "mode='text' returns visible text, useful for reading content rather than acting "
            "on it. mode='html' returns raw HTML and is a LAST RESORT: it is enormous, it "
            "will flood your context, and it tells you nothing 'aria' does not. Scope any "
            "mode with within_css to read one region."
        )
    )
    async def snapshot_page(tab: str, mode: str = "aria", within_css: str | None = None) -> str:
        return await snapshot(engine.find_tab(tab), mode=mode, within_css=within_css)

    @mcp.tool(
        description=(
            "Describe every element matching a locator, without acting on it. Use this to "
            "check a locator resolves the way you expect, or to recover from an ambiguous "
            "locator error by seeing the candidates.\n\n" + LOCATOR_DOC
        )
    )
    async def find(
        tab: str,
        css: str | None = None,
        xpath: str | None = None,
        by_text: str | None = None,
        by_label: str | None = None,
        by_placeholder: str | None = None,
        by_role: str | None = None,
        within_css: str | None = None,
        nth: int | None = None,
        exact: bool = False,
        regex: bool = False,
        wait_ms: int = 0,
    ) -> list[dict]:
        return await A.find(engine.find_tab(tab), _loc(css, xpath, by_text, by_label, by_placeholder, by_role, within_css, nth, exact, regex, wait_ms))

    @mcp.tool(
        description=(
            "Capture a PNG of the tab, or of one element if you pass a locator. Returns "
            "base64. Use it when layout or a visual detail matters; for deciding what to "
            "click, 'snapshot' is cheaper and more precise.\n\n" + LOCATOR_DOC
        )
    )
    async def screenshot(
        tab: str,
        full_page: bool = False,
        css: str | None = None,
        xpath: str | None = None,
        by_text: str | None = None,
        by_label: str | None = None,
        by_placeholder: str | None = None,
        by_role: str | None = None,
        within_css: str | None = None,
        nth: int | None = None,
        exact: bool = False,
        regex: bool = False,
        wait_ms: int = 0,
    ) -> str:
        png = await A.screenshot(engine.find_tab(tab), _loc(css, xpath, by_text, by_label, by_placeholder, by_role, within_css, nth, exact, regex, wait_ms), full_page=full_page)
        return base64.b64encode(png).decode()

    # -- acting -----------------------------------------------------------

    @mcp.tool(description="Click an element.\n\n" + LOCATOR_DOC)
    async def click(
        tab: str,
        css: str | None = None,
        xpath: str | None = None,
        by_text: str | None = None,
        by_label: str | None = None,
        by_placeholder: str | None = None,
        by_role: str | None = None,
        within_css: str | None = None,
        nth: int | None = None,
        exact: bool = False,
        regex: bool = False,
        wait_ms: int = 0,
    ) -> dict:
        return await A.click(engine.find_tab(tab), _loc(css, xpath, by_text, by_label, by_placeholder, by_role, within_css, nth, exact, regex, wait_ms))

    @mcp.tool(
        description=(
            "Type text into a field. Works on ordinary inputs and on rich-text editors "
            "(contenteditable, Lexical, ProseMirror, Quill). Set submit=true to press Enter "
            "afterwards.\n\n" + LOCATOR_DOC
        )
    )
    async def type(
        tab: str,
        text: str,
        submit: bool = False,
        css: str | None = None,
        xpath: str | None = None,
        by_text: str | None = None,
        by_label: str | None = None,
        by_placeholder: str | None = None,
        by_role: str | None = None,
        within_css: str | None = None,
        nth: int | None = None,
        exact: bool = False,
        regex: bool = False,
        wait_ms: int = 0,
    ) -> dict:
        return await A.type_text(
            engine.find_tab(tab), _loc(css, xpath, by_text, by_label, by_placeholder, by_role, within_css, nth, exact, regex, wait_ms), text, submit=submit
        )

    @mcp.tool(
        description=(
            "Press a key, dispatched as a real keyboard event so frameworks treat it as "
            "genuine input. Pass a locator to focus an element first, or omit it to send to "
            "whatever has focus. modifiers is a list like ['ctrl','shift'].\n\n" + LOCATOR_DOC
        )
    )
    async def press(
        tab: str,
        key: str,
        modifiers: list[str] | None = None,
        css: str | None = None,
        xpath: str | None = None,
        by_text: str | None = None,
        by_label: str | None = None,
        by_placeholder: str | None = None,
        by_role: str | None = None,
        within_css: str | None = None,
        nth: int | None = None,
        exact: bool = False,
        regex: bool = False,
        wait_ms: int = 0,
    ) -> dict:
        return await A.press(engine.find_tab(tab), key, _loc(css, xpath, by_text, by_label, by_placeholder, by_role, within_css, nth, exact, regex, wait_ms), modifiers)

    @mcp.tool(description="Choose one or more options in a <select>.\n\n" + LOCATOR_DOC)
    async def select(
        tab: str,
        values: list[str],
        css: str | None = None,
        xpath: str | None = None,
        by_text: str | None = None,
        by_label: str | None = None,
        by_placeholder: str | None = None,
        by_role: str | None = None,
        within_css: str | None = None,
        nth: int | None = None,
        exact: bool = False,
        regex: bool = False,
        wait_ms: int = 0,
    ) -> dict:
        return await A.select(engine.find_tab(tab), _loc(css, xpath, by_text, by_label, by_placeholder, by_role, within_css, nth, exact, regex, wait_ms), values)

    @mcp.tool(
        description=(
            "Draw a temporary coloured ring around an element. This is how you POINT AT "
            "SOMETHING FOR THE HUMAN — pair it with a screenshot, or use it while asking for "
            "help so the person can see what you mean. Allowed even while a human holds "
            "control.\n\n" + LOCATOR_DOC
        )
    )
    async def highlight(
        tab: str,
        color: str = "#ef4444",
        duration_ms: int = 2000,
        css: str | None = None,
        xpath: str | None = None,
        by_text: str | None = None,
        by_label: str | None = None,
        by_placeholder: str | None = None,
        by_role: str | None = None,
        within_css: str | None = None,
        nth: int | None = None,
        exact: bool = False,
        regex: bool = False,
        wait_ms: int = 0,
    ) -> dict:
        return await A.highlight(
            engine.find_tab(tab), _loc(css, xpath, by_text, by_label, by_placeholder, by_role, within_css, nth, exact, regex, wait_ms),
            color=color, duration_ms=duration_ms,
        )

    # -- human loop -------------------------------------------------------

    @mcp.tool(
        description=(
            "Ask a human to take over this context — for a CAPTCHA, a 2FA code, a login, or "
            "anything you should not decide alone.\n\n"
            "YOU MUST RELAY THE REQUEST YOURSELF. Take the `human_message` from the result "
            "and TELL YOUR OPERATOR, in your own reply, that you are waiting and what you "
            "need. You are the only channel guaranteed to reach them: nobody may be watching "
            "the terminal or the cockpit. Do not wait silently.\n\n"
            "Returns status:\n"
            "  'resolved'      — a human handled it; `note` may explain what they did. Continue.\n"
            "  'still_waiting' — nobody has answered yet. SAY SO to your operator, then call "
            "again to keep waiting. Do not loop silently and do not give up on the first poll.\n"
            "  'timeout'       — the deadline passed unanswered. Decide whether to abort, "
            "retry, or report back.\n\n"
            "`deadline_s` (default 600) is how long the request stays open for a human. "
            "`poll_s` (default 120) is only how long THIS CALL blocks. They are independent. "
            "While a human holds control your other actions on this context will fail with "
            "HumanHoldsControl — that is expected; keep reading the page if you want to watch."
        )
    )
    async def request_human(
        context: str, reason: str, deadline_s: float = 600, poll_s: float = 120
    ) -> dict:
        return await controller.request_human(
            context, reason, deadline_s=deadline_s, poll_s=poll_s
        )

    @mcp.tool(
        description=(
            "Report who currently controls a context: 'agent' means you may act, 'human' "
            "means a person is driving and your actions will be refused. Check this if an "
            "action failed with HumanHoldsControl and you want to know whether to wait."
        )
    )
    async def control_state(context: str) -> dict:
        pending = controller.pending(context)
        return {
            "context": context,
            "controller": controller.state(context),
            "pending_request": pending.info() if pending else None,
        }

    # -- diagnostics ------------------------------------------------------

    @mcp.tool(
        description=(
            "Read what has happened in a context since a sequence number: navigations, tabs "
            "opening and closing, control changes. Pass since=0 for everything, then pass "
            "back the highest seq you saw. Use this to notice that a human took over, or "
            "that a page navigated under you."
        )
    )
    async def get_events(context: str, since: int = 0) -> list[dict]:
        return events.since(context, since)

    return mcp
