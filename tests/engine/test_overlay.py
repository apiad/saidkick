import pytest

from saidkick import actions as A
from saidkick import errors as E
from saidkick import overlay
from saidkick.locators import Locator
from saidkick.snapshot import snapshot

pytestmark = pytest.mark.browser


async def test_overlay_is_invisible_to_the_agent(tab):
    """The most important test in VS2.

    If the agent can see the banner it will try to click saidkick's own UI
    instead of the page it was working on.
    """
    before = await snapshot(tab, mode="aria")
    await overlay.show(tab, "enter the 2FA code")
    after = await snapshot(tab, mode="aria")
    assert before == after


async def test_overlay_text_is_not_findable(tab):
    await overlay.show(tab, "enter the 2FA code")
    with pytest.raises(E.LocatorNotFound):
        await A.find(tab, Locator(by_text="enter the 2FA code", wait_ms=300))


async def test_overlay_is_actually_present_in_the_dom(tab):
    """Guard against 'invisible' being achieved by rendering nothing at all."""
    await overlay.show(tab, "help")
    assert (
        await tab.page.evaluate("() => !!document.querySelector('#saidkick-attention')")
        is True
    )


async def test_shadow_root_is_closed(tab):
    """A closed root keeps page scripts from reaching in."""
    await overlay.show(tab, "help")
    assert (
        await tab.page.evaluate(
            "() => document.querySelector('#saidkick-attention').shadowRoot"
        )
        is None
    )


async def test_overlay_does_not_intercept_clicks(tab):
    """pointer-events:none — the human must still be able to drive the page."""
    await overlay.show(tab, "help")
    await A.type_text(tab, Locator(css="#u"), "alice")
    await A.click(tab, Locator(css="#go"))
    assert await tab.page.locator("#out").text_content() == "submitted:alice"


async def test_title_restored_on_hide(tab):
    original = await tab.page.title()
    await overlay.show(tab, "help")
    assert await tab.page.title() != original
    await overlay.hide(tab)
    assert await tab.page.title() == original


async def test_show_is_idempotent(tab):
    await overlay.show(tab, "a")
    await overlay.show(tab, "b")
    assert (
        await tab.page.evaluate(
            "() => document.querySelectorAll('#saidkick-attention').length"
        )
        == 1
    )


async def test_hide_without_show_is_safe(tab):
    await overlay.hide(tab)


async def test_hide_is_idempotent(tab):
    await overlay.show(tab, "help")
    await overlay.hide(tab)
    await overlay.hide(tab)
    assert (
        await tab.page.evaluate("() => !!document.querySelector('#saidkick-attention')")
        is False
    )
