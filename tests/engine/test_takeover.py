import pytest

from saidkick import errors as E
from saidkick.input_bridge import forward

pytestmark = pytest.mark.browser

META = {
    "deviceWidth": 1280,
    "deviceHeight": 800,
    "pageScaleFactor": 1,
    "scrollOffsetX": 0,
    "scrollOffsetY": 0,
}
CANVAS = {"width": 1280, "height": 800}


async def test_forwarded_click_actually_clicks(tab, controller):
    controller.take(tab.context.id)
    box = await tab.page.locator("#go").bounding_box()
    pt = {
        "x": box["x"] + box["width"] / 2,
        "y": box["y"] + box["height"] / 2,
        "canvas": CANVAS,
        "button": "left",
    }
    await forward(tab, {"type": "mousemove", **pt}, META)
    await forward(tab, {"type": "mousedown", "clickCount": 1, **pt}, META)
    await forward(tab, {"type": "mouseup", "clickCount": 1, **pt}, META)
    assert "submitted:" in await tab.page.locator("#out").text_content()


async def test_forwarded_paste_lands_in_the_focused_field(tab, controller):
    """The 2FA path: focus, then insertText."""
    controller.take(tab.context.id)
    await tab.page.locator("#u").focus()
    await forward(tab, {"type": "paste", "text": "123456"}, META)
    assert await tab.page.locator("#u").input_value() == "123456"


async def test_forwarded_keystrokes_type_characters(tab, controller):
    controller.take(tab.context.id)
    await tab.page.locator("#u").focus()
    for ch in "abc":
        await forward(tab, {"type": "keydown", "key": ch, "code": f"Key{ch.upper()}"}, META)
        await forward(tab, {"type": "keyup", "key": ch, "code": f"Key{ch.upper()}"}, META)
    assert await tab.page.locator("#u").input_value() == "abc"


async def test_input_refused_when_agent_holds_control(tab, controller):
    """Forwarding human input while the agent holds the wheel is also a violation."""
    with pytest.raises(E.HumanHoldsControl):
        await forward(tab, {"type": "paste", "text": "x"}, META)


async def test_input_refused_again_after_release(tab, controller):
    controller.take(tab.context.id)
    await forward(tab, {"type": "paste", "text": "ok"}, META)
    controller.release(tab.context.id)
    with pytest.raises(E.HumanHoldsControl):
        await forward(tab, {"type": "paste", "text": "nope"}, META)
