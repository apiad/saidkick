import pytest

from saidkick import actions as A
from saidkick import errors as E
from saidkick.locators import Locator

pytestmark = pytest.mark.browser


async def test_click_submits_form(tab):
    await A.type_text(tab, Locator(by_label="Username"), "alice")
    await A.click(tab, Locator(css="#go"))
    assert await tab.page.locator("#out").text_content() == "submitted:alice"


async def test_type_into_contenteditable(tab):
    await A.type_text(tab, Locator(by_label="Message"), "hello")
    assert "hello" in await tab.page.locator("#editor").inner_text()


async def test_type_with_submit(tab):
    await A.type_text(tab, Locator(by_label="Username"), "bob", submit=True)
    assert await tab.page.locator("#out").text_content() == "submitted:bob"


async def test_select_option(tab):
    await A.select(tab, Locator(css="#c"), ["es"])
    assert await tab.page.locator("#c").input_value() == "es"


async def test_not_found_raises(tab):
    with pytest.raises(E.LocatorNotFound):
        await A.click(tab, Locator(by_text="NoSuchButton"), timeout_ms=500)


async def test_ambiguous_raises_with_candidates(tab):
    with pytest.raises(E.LocatorAmbiguous) as exc:
        await A.click(tab, Locator(css="label"), timeout_ms=500)
    assert len(exc.value.extra["candidates"]) == 2


async def test_nth_resolves_an_otherwise_ambiguous_locator(tab):
    """The documented recovery from LocatorAmbiguous must actually work."""
    out = await A.find(tab, Locator(css="label", nth=1))
    assert out[0]["text"] == "Country"


async def test_find_returns_descriptors(tab):
    out = await A.find(tab, Locator(css="#go"))
    assert out[0]["tag"] == "BUTTON" and out[0]["text"] == "Send"


async def test_wait_ms_waits_for_late_element(engine, fixture_url):
    ctx = await engine.open_context()
    t = await ctx.open_tab(f"{fixture_url}/delayed.html")
    out = await A.find(t, Locator(css="#late", wait_ms=3000))
    assert out[0]["text"] == "ready"


async def test_missing_late_element_without_wait_raises(engine, fixture_url):
    ctx = await engine.open_context()
    t = await ctx.open_tab(f"{fixture_url}/delayed.html")
    with pytest.raises(E.LocatorNotFound):
        await A.find(t, Locator(css="#late"), timeout_ms=100)


async def test_screenshot_returns_png_bytes(tab):
    png = await A.screenshot(tab)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


async def test_screenshot_of_an_element_is_smaller_than_the_page(tab):
    assert len(await A.screenshot(tab, Locator(css="#go"))) < len(await A.screenshot(tab))


async def test_press_dispatches_a_real_key(tab):
    await A.type_text(tab, Locator(by_label="Username"), "carol")
    await A.press(tab, "Enter", Locator(by_label="Username"))
    assert await tab.page.locator("#out").text_content() == "submitted:carol"


async def test_highlight_leaves_no_trace_after_expiry(tab):
    await A.highlight(tab, Locator(css="#go"), duration_ms=100)
    import asyncio

    await asyncio.sleep(0.3)
    assert await tab.page.evaluate(
        "() => document.querySelector('#go').style.outline || ''"
    ) in ("", "none")
