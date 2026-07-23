"""Translate cockpit input messages into CDP input events.

:func:`to_cdp` is pure — no I/O, no browser — because this is where coordinate
bugs live and they are miserable to debug through a video stream.

Coordinates arrive in canvas space and must be scaled to viewport space. They
are *not* offset by the scroll position: CDP's ``Input.dispatchMouseEvent``
takes viewport-relative coordinates, and the screencast frame is already the
scrolled viewport, so adding ``scrollOffset`` would double-count it.
"""

from typing import TYPE_CHECKING, Any

from . import errors as E

if TYPE_CHECKING:  # pragma: no cover
    from .engine import ManagedTab

#: CDP modifier bitmask.
MODIFIERS = {"alt": 1, "ctrl": 2, "control": 2, "meta": 4, "cmd": 4, "shift": 8}

MOUSE_TYPES = {
    "mousemove": "mouseMoved",
    "mousedown": "mousePressed",
    "mouseup": "mouseReleased",
}


def _modifier_mask(names: list[str] | None) -> int:
    mask = 0
    for name in names or []:
        mask |= MODIFIERS.get(name.lower(), 0)
    return mask


def _scale(msg: dict, metadata: dict) -> tuple[float, float]:
    canvas = msg.get("canvas") or {}
    device_w = metadata.get("deviceWidth") or canvas.get("width") or 1
    device_h = metadata.get("deviceHeight") or canvas.get("height") or 1
    canvas_w = canvas.get("width") or device_w
    canvas_h = canvas.get("height") or device_h
    return msg["x"] * (device_w / canvas_w), msg["y"] * (device_h / canvas_h)


def to_cdp(msg: dict, metadata: dict) -> tuple[str, dict[str, Any]]:
    """Map one cockpit message to a (CDP method, params) pair."""
    kind = msg.get("type")

    if kind in MOUSE_TYPES:
        x, y = _scale(msg, metadata)
        params = {
            "type": MOUSE_TYPES[kind],
            "x": x,
            "y": y,
            "modifiers": _modifier_mask(msg.get("modifiers")),
            "button": msg.get("button", "none" if kind == "mousemove" else "left"),
            "clickCount": msg.get("clickCount", 0 if kind == "mousemove" else 1),
        }
        return "Input.dispatchMouseEvent", params

    if kind == "wheel":
        x, y = _scale(msg, metadata)
        return "Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": x,
            "y": y,
            "deltaX": msg.get("deltaX", 0),
            "deltaY": msg.get("deltaY", 0),
            "modifiers": _modifier_mask(msg.get("modifiers")),
        }

    if kind in ("keydown", "keyup"):
        key = msg.get("key", "")
        params = {
            "type": "keyDown" if kind == "keydown" else "keyUp",
            "key": key,
            "code": msg.get("code", ""),
            "modifiers": _modifier_mask(msg.get("modifiers")),
        }
        # Printable single characters need text, or the page sees a bare
        # keydown with no character and nothing is inserted.
        if kind == "keydown" and len(key) == 1:
            params["text"] = key
        return "Input.dispatchKeyEvent", params

    if kind == "paste":
        # A 2FA code is inserted, not synthesised as thirty keystrokes.
        return "Input.insertText", {"text": msg.get("text", "")}

    raise ValueError(f"unknown input message type: {kind!r}")


async def forward(tab: "ManagedTab", msg: dict, metadata: dict) -> None:
    """Send one cockpit input message to the page. Only while the human holds control."""
    controller = tab.context.controller
    if controller is not None:
        controller.assert_human_may_act(tab.context.id)
    else:  # pragma: no cover - the API always wires a controller
        raise E.HumanHoldsControl("no controller configured for this context")

    method, params = to_cdp(msg, metadata)
    cdp = await tab.context.cdp(tab)
    await cdp.send(method, params)
