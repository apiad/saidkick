"""Pins: human-placed, agent-addressable references to DOM elements.

A human clicks or drags on the live cockpit view to say "this is the thing."
The registry resolves that point to an element, stamps it with a
``data-saidkick-pin`` attribute, and records a bundle the agent can act on:

- a **live handle** — the agent acts on ``el_x7f2`` with no selector work. The
  handle is the stamp attribute, resolved as a locator; it survives re-renders
  as long as the node does, and is invalidated by navigation.
- **semantic descriptors** and a **suggested locator** built from them;
- **durable css and xpath fallbacks** computed at mint time — deliberately NOT
  the stamp selector, which dies on navigation — for when the handle goes stale;
- a **clipped screenshot** of the element.

Pins are created only by humans. Agents list and read them.
"""

import base64
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from playwright.async_api import Error as PWError

from . import errors as E
from .locators import Locator

if TYPE_CHECKING:  # pragma: no cover
    from .engine import ManagedTab

STAMP_ATTR = "data-saidkick-pin"

# Describe an element and compute a durable css + absolute xpath in one pass.
_BUNDLE_JS = """
(el) => {
  const cssPath = (node) => {
    if (node.id) return '#' + CSS.escape(node.id);
    const parts = [];
    while (node && node.nodeType === 1 && node.tagName !== 'HTML') {
      let sel = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (sibs.length > 1) sel += ':nth-of-type(' + (sibs.indexOf(node) + 1) + ')';
      }
      parts.unshift(sel);
      if (node.id) { parts[0] = '#' + CSS.escape(node.id); break; }
      node = parent;
    }
    return parts.join(' > ');
  };
  const xpath = (node) => {
    const parts = [];
    while (node && node.nodeType === 1) {
      let i = 1, sib = node.previousElementSibling;
      while (sib) { if (sib.tagName === node.tagName) i++; sib = sib.previousElementSibling; }
      parts.unshift(node.tagName.toLowerCase() + '[' + i + ']');
      node = node.parentElement;
    }
    return '/' + parts.join('/');
  };
  return {
    tag: el.tagName,
    text: (el.innerText || el.value || '').trim().slice(0, 120),
    role: el.getAttribute('role'),
    label: el.getAttribute('aria-label'),
    placeholder: el.getAttribute('placeholder'),
    id: el.id || null,
    css: cssPath(el),
    xpath: xpath(el),
  };
};
"""

# Leaf-most element fully enclosed by a viewport rectangle. Ancestors of another
# hit are dropped; the deepest enclosed element wins.
_RECT_JS = """
(r) => {
  const hits = [];
  for (const el of document.querySelectorAll('body *')) {
    const b = el.getBoundingClientRect();
    if (b.width > 0 && b.height > 0 &&
        b.left >= r.x && b.top >= r.y &&
        b.right <= r.x + r.w && b.bottom <= r.y + r.h) {
      hits.push(el);
    }
  }
  const leaves = hits.filter(el => !hits.some(other => other !== el && el.contains(other)));
  const target = (leaves[0] || hits[hits.length - 1]);
  if (!target) return false;
  target.setAttribute('__SK_ATTR__', '__SK_HANDLE__');
  return true;
};
"""


def _suggested(descriptor: dict) -> dict[str, Any]:
    role, text = descriptor.get("role"), descriptor.get("text")
    tag = (descriptor.get("tag") or "").lower()
    # Infer a role for the common form controls so the suggestion is useful even
    # without an explicit role attribute.
    if not role and tag == "button":
        role = "button"
    if role and text:
        return {"by_role": role, "by_text": text}
    if descriptor.get("label"):
        return {"by_label": descriptor["label"]}
    if descriptor.get("placeholder"):
        return {"by_placeholder": descriptor["placeholder"]}
    if text:
        return {"by_text": text}
    return {"css": descriptor.get("css")}


@dataclass
class Pin:
    id: str
    tab_id: str
    label: str | None
    descriptor: dict
    suggested: dict
    css: str
    xpath: str
    screenshot_b64: str
    created_at: float = field(default_factory=time.monotonic)

    def info(self, include_screenshot: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "handle": self.id,
            "tab": self.tab_id,
            "label": self.label,
            "descriptor": self.descriptor,
            "suggested_locator": self.suggested,
            "css": self.css,
            "xpath": self.xpath,
        }
        if include_screenshot:
            out["screenshot"] = self.screenshot_b64
        return out


class PinRegistry:
    def __init__(self):
        self._pins: dict[str, Pin] = {}

    async def _stamp_selector(self, tab: "ManagedTab", handle: str) -> str:
        return f'[{STAMP_ATTR}="{handle}"]'

    async def _build(self, tab: "ManagedTab", handle: str, label: str | None) -> Pin:
        loc = tab.page.locator(f'[{STAMP_ATTR}="{handle}"]')
        bundle = await loc.evaluate(_BUNDLE_JS)
        try:
            png = await loc.screenshot()
            shot = base64.b64encode(png).decode()
        except PWError:  # element not visible enough to clip
            shot = ""
        descriptor = {k: bundle[k] for k in ("tag", "text", "role", "label", "placeholder", "id")}
        pin = Pin(
            id=handle,
            tab_id=tab.id,
            label=label,
            descriptor=descriptor,
            suggested=_suggested({**descriptor, "css": bundle["css"]}),
            css=bundle["css"],
            xpath=bundle["xpath"],
            screenshot_b64=shot,
        )
        self._pins[handle] = pin
        return pin

    async def mint_point(
        self, tab: "ManagedTab", x: float, y: float, label: str | None = None
    ) -> Pin:
        handle = f"el_{secrets.token_hex(3)}"
        cdp = await tab.context.cdp(tab)
        await cdp.send("DOM.enable")
        await cdp.send("DOM.getDocument", {"depth": -1})
        hit = await cdp.send("DOM.getNodeForLocation", {"x": int(x), "y": int(y)})
        backend = hit.get("backendNodeId")
        if not backend:
            raise E.LocatorNotFound(f"no element at ({x}, {y})")
        resolved = await cdp.send("DOM.resolveNode", {"backendNodeId": backend})
        object_id = resolved["object"]["objectId"]
        await cdp.send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": f"function(){{this.setAttribute('{STAMP_ATTR}','{handle}');}}",
            },
        )
        return await self._build(tab, handle, label)

    async def mint_rect(
        self,
        tab: "ManagedTab",
        x: float,
        y: float,
        w: float,
        h: float,
        label: str | None = None,
    ) -> Pin:
        handle = f"el_{secrets.token_hex(3)}"
        js = _RECT_JS.replace("__SK_ATTR__", STAMP_ATTR).replace("__SK_HANDLE__", handle)
        ok = await tab.page.evaluate(js, {"x": x, "y": y, "w": w, "h": h})
        if not ok:
            raise E.LocatorNotFound("no element enclosed by the selection")
        return await self._build(tab, handle, label)

    def get(self, handle: str) -> Pin:
        try:
            return self._pins[handle]
        except KeyError:
            raise E.StaleHandle(f"unknown pin: {handle}") from None

    def list(self, tab_id: str | None = None) -> list[Pin]:
        pins = list(self._pins.values())
        return [p for p in pins if tab_id is None or p.tab_id == tab_id]

    async def resolve(self, tab: "ManagedTab", handle: str) -> Locator:
        """Return a locator for the pinned element, or raise StaleHandle.

        The agent's recovery from StaleHandle is to use the css/xpath/suggested
        locator in the pin's bundle — distinct from LocatorNotFound, which means
        'retry the same locator'.
        """
        selector = f'[{STAMP_ATTR}="{handle}"]'
        if await tab.page.locator(selector).count() == 0:
            raise E.StaleHandle(
                f"pin {handle} no longer resolves (the page changed); "
                "fall back to the css/xpath/suggested locator from read_pin"
            )
        return Locator(css=selector)
