"""A tab the PAGE opens must be drivable, not just one saidkick opened.

Without this, `target="_blank"` and `window.open` are invisible: `list_tabs`
never shows them and every locator against them 404s with "unknown tab". The
page is live, the context owns it, and nothing can reach it.
"""
import asyncio

from saidkick.locators import Locator


async def _new_tab_id(ctx, before, timeout_s=5.0):
    """Poll until a tab id appears that was not in `before`."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        fresh = {t["id"] for t in ctx.list_tabs()} - before
        if fresh:
            return next(iter(fresh))
        await asyncio.sleep(0.05)
    return None


async def test_target_blank_link_produces_a_listed_tab(ctx, fixture_url):
    tab = await ctx.open_tab(f"{fixture_url}/popup.html")
    before = {t["id"] for t in ctx.list_tabs()}

    await tab.page.click("#blank")

    assert await _new_tab_id(ctx, before), "the target=_blank tab was never adopted"


async def test_window_open_produces_a_listed_tab(ctx, fixture_url):
    tab = await ctx.open_tab(f"{fixture_url}/popup.html")
    before = {t["id"] for t in ctx.list_tabs()}

    await tab.page.click("#wopen")

    assert await _new_tab_id(ctx, before), "the window.open tab was never adopted"


async def test_adopted_tab_is_addressable_and_drivable(ctx, fixture_url):
    tab = await ctx.open_tab(f"{fixture_url}/popup.html")
    before = {t["id"] for t in ctx.list_tabs()}
    await tab.page.click("#blank")
    new_id = await _new_tab_id(ctx, before)
    assert new_id

    adopted = ctx.get_tab(new_id)              # must not raise
    assert adopted.page is not tab.page
    await adopted.page.wait_for_load_state()
    # Addressable is not enough — it has to be drivable through the normal
    # locator path, which is what a caller will actually use.
    from saidkick.actions import find
    els = await find(adopted, Locator(css="#here"))
    assert els, "the adopted tab is listed but its DOM is not reachable"


async def test_adopted_tab_captures_traffic_from_adoption_onwards(ctx, fixture_url):
    """An adopted tab records console and network like any other.

    LIMITATION, asserted rather than glossed: the popup's OWN first navigation
    is not reliably captured. `context.on("page")` is the earliest hook
    Playwright offers, and the browser may already have the response for
    `opened.html` in flight by the time the handler constructs the ManagedTab
    and `capture.install` attaches its listeners. Everything from adoption
    onwards is recorded, which is what an oracle running after a handoff needs.

    If you ever need that first response, assert it on the SOURCE tab (which
    was being captured all along) or re-navigate the adopted tab.
    """
    tab = await ctx.open_tab(f"{fixture_url}/popup.html")
    before = {t["id"] for t in ctx.list_tabs()}
    await tab.page.click("#blank")
    new_id = await _new_tab_id(ctx, before)
    adopted = ctx.get_tab(new_id)
    await adopted.page.wait_for_load_state()

    assert adopted.capture is not None
    # Traffic caused after adoption must land in the adopted tab's capture.
    await adopted.navigate(f"{fixture_url}/index.html")
    assert any("index.html" in e["url"] for e in adopted.capture.read_network()), \
        "the adopted tab records no network traffic even after adoption"


async def test_a_closed_popup_leaves_the_registry(ctx, fixture_url):
    """list_tabs must stay truthful: a tab the browser closed is not a tab."""
    tab = await ctx.open_tab(f"{fixture_url}/popup.html")
    before = {t["id"] for t in ctx.list_tabs()}
    await tab.page.click("#blank")
    new_id = await _new_tab_id(ctx, before)

    await ctx.get_tab(new_id).page.close()
    deadline = asyncio.get_event_loop().time() + 3.0
    while asyncio.get_event_loop().time() < deadline:
        if new_id not in {t["id"] for t in ctx.list_tabs()}:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{new_id} is still listed after the browser closed it")
