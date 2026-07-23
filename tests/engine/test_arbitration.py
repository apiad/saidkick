import asyncio

import pytest

from saidkick import actions as A
from saidkick import errors as E
from saidkick.locators import Locator
from saidkick.snapshot import snapshot

pytestmark = pytest.mark.browser


async def test_mutating_action_blocked_while_human_holds(tab, controller):
    controller.take(tab.context.id)
    with pytest.raises(E.HumanHoldsControl):
        await A.click(tab, Locator(css="#go"))


@pytest.mark.parametrize(
    "call",
    [
        lambda t: A.click(t, Locator(css="#go")),
        lambda t: A.type_text(t, Locator(css="#u"), "x"),
        lambda t: A.press(t, "Enter"),
        lambda t: A.select(t, Locator(css="#c"), ["es"]),
        lambda t: A.hover(t, Locator(css="#go")),
        lambda t: A.scroll(t, Locator(css="#go")),
    ],
)
async def test_every_mutating_action_is_gated(tab, controller, call):
    controller.take(tab.context.id)
    with pytest.raises(E.HumanHoldsControl):
        await call(tab)


async def test_read_only_still_allowed_while_human_holds(tab, controller):
    """The agent must be able to watch the rescue it asked for."""
    controller.take(tab.context.id)
    assert await A.find(tab, Locator(css="#go"))
    assert (await A.screenshot(tab))[:8] == b"\x89PNG\r\n\x1a\n"
    assert 'button "Send"' in await snapshot(tab, mode="aria")


async def test_highlight_allowed_while_human_holds(tab, controller):
    """Pointing at something is most useful exactly when the human is driving."""
    controller.take(tab.context.id)
    await A.highlight(tab, Locator(css="#go"), duration_ms=50)


async def test_fails_fast_rather_than_queueing(tab, controller):
    """The call must return immediately, not block until release."""
    controller.take(tab.context.id)
    with pytest.raises(E.HumanHoldsControl):
        await asyncio.wait_for(A.click(tab, Locator(css="#go")), timeout=0.5)


async def test_other_context_unaffected(engine, tab, controller, fixture_url):
    controller.take(tab.context.id)
    other = await engine.open_context()
    t2 = await other.open_tab(f"{fixture_url}/form.html")
    await A.click(t2, Locator(css="#go"))


async def test_action_resumes_after_release(tab, controller):
    controller.take(tab.context.id)
    controller.release(tab.context.id)
    await A.click(tab, Locator(css="#go"))
