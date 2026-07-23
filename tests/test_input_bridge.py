import pytest

from saidkick.input_bridge import to_cdp

# The canvas is 640 wide for a 1280-wide device: everything scales by 2.
META = {
    "deviceWidth": 1280,
    "deviceHeight": 800,
    "pageScaleFactor": 1,
    "scrollOffsetX": 0,
    "scrollOffsetY": 0,
}
CANVAS = {"width": 640, "height": 400}


def test_mouse_coordinates_scale_from_canvas_to_viewport():
    method, params = to_cdp(
        {"type": "mousedown", "x": 100, "y": 50, "button": "left", "canvas": CANVAS}, META
    )
    assert method == "Input.dispatchMouseEvent"
    assert params["x"] == 200 and params["y"] == 100


def test_mousemove_maps_to_moved_with_no_button():
    _, params = to_cdp({"type": "mousemove", "x": 10, "y": 10, "canvas": CANVAS}, META)
    assert params["type"] == "mouseMoved" and params["button"] == "none"


def test_key_event_maps_to_dispatch_key_event():
    method, params = to_cdp(
        {"type": "keydown", "key": "Enter", "code": "Enter", "modifiers": []}, META
    )
    assert method == "Input.dispatchKeyEvent"
    assert params["key"] == "Enter" and params["type"] == "keyDown"


def test_printable_keydown_carries_text():
    """Without text, the page sees a keydown and inserts nothing."""
    _, params = to_cdp({"type": "keydown", "key": "a", "code": "KeyA"}, META)
    assert params["text"] == "a"


def test_non_printable_keydown_has_no_text():
    _, params = to_cdp({"type": "keydown", "key": "Enter", "code": "Enter"}, META)
    assert "text" not in params


def test_modifier_bitmask():
    _, params = to_cdp(
        {"type": "keydown", "key": "a", "code": "KeyA", "modifiers": ["ctrl", "shift"]}, META
    )
    assert params["modifiers"] == 2 | 8


def test_paste_uses_insert_text_not_synthesised_keystrokes():
    """A 2FA code is inserted, not typed thirty times."""
    method, params = to_cdp({"type": "paste", "text": "123456"}, META)
    assert method == "Input.insertText" and params["text"] == "123456"


def test_wheel_maps_to_mouse_wheel():
    method, params = to_cdp(
        {"type": "wheel", "x": 10, "y": 10, "deltaY": 120, "canvas": CANVAS}, META
    )
    assert method == "Input.dispatchMouseEvent" and params["type"] == "mouseWheel"
    assert params["deltaY"] == 120


def test_unknown_message_type_rejected():
    with pytest.raises(ValueError):
        to_cdp({"type": "levitate"}, META)


def test_scroll_offset_is_not_added_twice():
    """CDP viewport coords are already scroll-relative; adding scrollOffset double-counts."""
    meta = dict(META, scrollOffsetY=300)
    _, params = to_cdp(
        {"type": "mousedown", "x": 100, "y": 50, "button": "left", "canvas": CANVAS}, meta
    )
    assert params["y"] == 100


def test_unscaled_canvas_is_identity():
    meta = dict(META, deviceWidth=1280, deviceHeight=800)
    _, params = to_cdp(
        {"type": "mousedown", "x": 640, "y": 400, "canvas": {"width": 1280, "height": 800}}, meta
    )
    assert params["x"] == 640 and params["y"] == 400
