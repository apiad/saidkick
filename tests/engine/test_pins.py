import pytest

from saidkick import errors as E
from saidkick.locators import resolve as resolve_locator
from saidkick.pins import PinRegistry

pytestmark = pytest.mark.browser


async def _pin_button(reg, tab):
    box = await tab.page.locator("#go").bounding_box()
    return await reg.mint_point(tab, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


async def test_mint_point_stamps_and_describes(tab):
    reg = PinRegistry()
    pin = await _pin_button(reg, tab)
    assert pin.id.startswith("el_")
    assert pin.descriptor["tag"] == "BUTTON"
    assert pin.descriptor["text"] == "Send"
    assert pin.suggested.get("by_role") == "button"
    assert pin.suggested.get("by_text") == "Send"
    assert pin.screenshot_b64


async def test_resolve_acts_on_the_pinned_element(tab):
    reg = PinRegistry()
    pin = await _pin_button(reg, tab)
    loc = await reg.resolve(tab, pin.id)  # a saidkick Locator (a stamp css selector)
    assert await resolve_locator(tab.page, loc).get_attribute("id") == "go"


async def test_stale_handle_after_navigation(tab, fixture_url):
    reg = PinRegistry()
    pin = await _pin_button(reg, tab)
    await tab.navigate(f"{fixture_url}/index.html")
    with pytest.raises(E.StaleHandle):
        await reg.resolve(tab, pin.id)


async def test_unknown_handle_is_stale(tab):
    with pytest.raises(E.StaleHandle):
        await PinRegistry().resolve(tab, "el_nope")


async def test_durable_css_is_not_the_stamp_selector(tab):
    """The fallback css/xpath must survive a stamp wipe, so it cannot be the stamp."""
    reg = PinRegistry()
    pin = await _pin_button(reg, tab)
    assert "data-saidkick-pin" not in pin.css
    assert pin.css == "#go"
    assert pin.xpath


async def test_durable_css_locates_the_element_after_stamp_wipe(tab, fixture_url):
    reg = PinRegistry()
    pin = await _pin_button(reg, tab)
    # Re-render wipes the stamp; the durable css must still find it.
    await tab.page.evaluate("() => document.querySelector('#go').removeAttribute('data-saidkick-pin')")
    assert await tab.page.locator(pin.css).get_attribute("id") == "go"


async def test_mint_rect_selects_an_enclosed_element(tab):
    reg = PinRegistry()
    box = await tab.page.locator("#f").bounding_box()
    pin = await reg.mint_rect(
        tab, box["x"] - 5, box["y"] - 5, box["width"] + 10, box["height"] + 10, label="the form"
    )
    assert pin.label == "the form"
    assert pin.descriptor["tag"] in ("FORM", "BUTTON", "DIV", "INPUT", "SELECT", "LABEL")


async def test_list_filters_by_tab(tab):
    reg = PinRegistry()
    await _pin_button(reg, tab)
    assert len(reg.list(tab.id)) == 1
    assert reg.list("ctx_other:1") == []


async def test_info_omits_screenshot_by_default(tab):
    reg = PinRegistry()
    pin = await _pin_button(reg, tab)
    assert "screenshot" not in pin.info()
    assert pin.info(include_screenshot=True)["screenshot"]
