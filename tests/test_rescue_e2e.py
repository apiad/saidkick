import asyncio

import pytest

from saidkick import actions as A
from saidkick import errors as E
from saidkick import overlay
from saidkick.input_bridge import forward
from saidkick.locators import Locator
from saidkick.snapshot import snapshot

pytestmark = pytest.mark.browser

META = {
    "deviceWidth": 1280,
    "deviceHeight": 800,
    "pageScaleFactor": 1,
    "scrollOffsetX": 0,
    "scrollOffsetY": 0,
}


async def test_agent_asks_for_help_and_is_rescued(engine, controller, fixture_url):
    """VS1 + VS2 in one test.

    The agent hits a wall, asks for a human, is locked out while the human
    drives, and resumes with the human's note once control comes back.
    """
    ctx = await engine.open_context()
    tab = await ctx.open_tab(f"{fixture_url}/form.html")

    async def agent():
        out = await controller.request_human(
            ctx.id, "enter the 2FA code", deadline_s=10, poll_s=10
        )
        assert out["status"] == "resolved"
        assert out["note"] == "code entered"
        await A.click(tab, Locator(css="#go"))
        return await tab.page.locator("#out").text_content()

    task = asyncio.create_task(agent())
    await asyncio.sleep(0.2)

    # The request is open and carries something the agent could relay verbatim.
    pending = controller.pending(ctx.id)
    assert pending is not None and pending.reason == "enter the 2FA code"

    # The page asks for attention without becoming visible to the agent.
    before = await snapshot(tab, mode="aria")
    await overlay.show(tab, "enter the 2FA code")
    assert await snapshot(tab, mode="aria") == before

    # Human takes the wheel; the agent is locked out of mutations...
    controller.take(ctx.id)
    with pytest.raises(E.HumanHoldsControl):
        await A.click(tab, Locator(css="#go"))
    # ...but can still watch what is happening.
    assert await A.find(tab, Locator(css="#u"))

    # Human types the code through the takeover path, not the agent API.
    await tab.page.locator("#u").focus()
    await forward(tab, {"type": "paste", "text": "123456"}, META)

    await overlay.hide(tab)
    controller.release(ctx.id, note="code entered")

    assert await asyncio.wait_for(task, timeout=5) == "submitted:123456"
    assert controller.pending(ctx.id) is None
    assert controller.state(ctx.id) == "agent"


async def test_agent_polls_and_relays_while_waiting(engine, controller, fixture_url):
    """An agent that waits longer than one poll must see still_waiting, and the
    request must not be duplicated by the repeated calls."""
    ctx = await engine.open_context()
    await ctx.open_tab(f"{fixture_url}/form.html")

    first = await controller.request_human(ctx.id, "captcha", deadline_s=5, poll_s=0.1)
    assert first["status"] == "still_waiting"
    assert "captcha" in first["human_message"]

    second = await controller.request_human(ctx.id, "captcha", deadline_s=5, poll_s=0.1)
    assert second["status"] == "still_waiting"
    assert second["request_id"] == first["request_id"]
    assert len(controller.list_pending()) == 1

    controller.take(ctx.id)
    controller.release(ctx.id, note="done")
    assert controller.pending(ctx.id) is None


async def test_timeout_leaves_the_page_untouched(engine, controller, fixture_url):
    """A deadline must not reap the context; the half-finished state survives."""
    ctx = await engine.open_context()
    tab = await ctx.open_tab(f"{fixture_url}/form.html")
    await A.type_text(tab, Locator(css="#u"), "half-finished")

    out = await controller.request_human(ctx.id, "help", deadline_s=0.2, poll_s=5)
    assert out["status"] == "timeout"

    assert await tab.page.locator("#u").input_value() == "half-finished"
    assert controller.state(ctx.id) == "agent"
    await A.click(tab, Locator(css="#go"))
