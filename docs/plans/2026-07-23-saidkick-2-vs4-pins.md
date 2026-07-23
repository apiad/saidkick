# saidkick 2.0 — VS4 (pins) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Let a human point at a DOM element — by clicking or dragging on the live cockpit view —
and hand the agent an addressable reference to it: "*this* is the thing."

**Architecture:** A `PinRegistry` (peer of `Controller`/`EventBus`, created in `serve`, shared by
API/MCP/cockpit) mints pins. Minting resolves a viewport point via CDP `DOM.getNodeForLocation`,
stamps the element with `data-saidkick-pin="<handle>"`, and records a bundle: semantic
descriptors, a suggested locator, durable css/xpath fallbacks, and a clipped screenshot. Agents
act on a handle directly; a dead handle raises `StaleHandle` so they fall back to the selectors in
the same bundle. Pins are created only by humans.

**Tech Stack:** unchanged — Playwright 1.61, FastAPI, mcp 1.28, pytest.


**Status:** COMPLETE — 2026-07-23. All 5 tasks on `main`; gate green (ruff clean,
93 non-browser + 111 browser tests). Released as 2.1.0. Smoke-tested end to end
against the running daemon: rect pin → BUTTON with by_role/by_text suggestion →
`saidkick pins` lists it → click via handle fires the onclick.

**Deviations:**
1. Two route-ordering / param bugs the plan did not foresee, both caught by
   tests: `/tabs/{tid}/pin` had to be registered *before* the `/{action}`
   catch-all (which was eating "pin" as an unknown action), and the `handle`
   param on `type`/`press`/`select` had to go *after* their required positional
   args (a defaulted param cannot precede a required one).
2. `_target` (handle-or-locator) is duplicated in both `api.py` and
   `mcp_server.py` rather than shared — each closes over its own `pins`/`engine`
   and the two call shapes differ enough that a shared helper would be more
   indirection than it saves.

**Spec:** `docs/specs/2026-07-23-saidkick-2-agent-native-browser-design.md` §6 (Pins). Every
mechanism below was verified against a live Playwright 1.61 probe before this plan was written.

## Global Constraints

Inherits VS1+VS2's constraints (English only, no vendor coupling, closed error set, `@browser`
marker, commit per task). New:

- **Handle format is `el_<hex>`.** The stamp attribute is `data-saidkick-pin="<handle>"`.
- **Acting on a handle checks the stamp resolves first; if not, raise `StaleHandle`** (not
  `LocatorNotFound`) — the agent's recovery is different (use the fallback selectors, don't retry
  the handle).
- **Pins are human-placed only.** No MCP tool mints a pin; the agent lists and reads them.
- **Placing a pin does NOT require human control.** You point while watching; the agent uses the
  pin without a takeover.

## File Structure

- Create `src/saidkick/pins.py` — `Pin`, `PinRegistry`, minting, resolution, staleness.
- Modify `src/saidkick/api.py` — pin routes + control-socket `pin` message.
- Modify `src/saidkick/mcp_server.py` — `list_pins`, `read_pin`, `handle=` on acting tools.
- Modify `src/saidkick/cockpit/static/cockpit.js` + `session.html` — drag/click-to-pin.
- Modify `src/saidkick/client.py`, `src/saidkick/cli.py` — `pins` command.
- Tests: `tests/engine/test_pins.py`, `tests/api/test_pins_api.py`, extend `tests/api/test_mcp.py`.

---

### Task 1: PinRegistry — mint, resolve, staleness

**Files:** Create `src/saidkick/pins.py`; Test `tests/engine/test_pins.py`.

**Interfaces (Produces):**
- `@dataclass Pin`: `id, tab_id, label, descriptor: dict, suggested: dict, css: str, xpath: str,
  screenshot_b64: str, created_at: float`; `.info(include_screenshot=False) -> dict`.
- `class PinRegistry`:
  - `async mint_point(tab, x, y, label=None) -> Pin` — CDP hit-test at viewport (x,y), stamp,
    build bundle.
  - `async mint_rect(tab, x, y, w, h, label=None) -> Pin` — leaf-most element fully enclosed by
    the rect (their common ancestor if several), stamp, build bundle.
  - `get(handle) -> Pin` (raises `NoSuchTab`-style? no: raises `StaleHandle` if unknown).
  - `list(tab_id=None) -> list[Pin]`.
  - `async resolve(tab, handle) -> Locator` — returns the stamp locator, raising `StaleHandle` if
    the stamp no longer resolves in the page.

- [x] **Step 1: failing tests**

```python
import pytest
from saidkick import errors as E
from saidkick.pins import PinRegistry

pytestmark = pytest.mark.browser


async def test_mint_point_stamps_and_describes(tab):
    reg = PinRegistry()
    box = await tab.page.locator("#go").bounding_box()
    pin = await reg.mint_point(tab, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    assert pin.id.startswith("el_")
    assert pin.descriptor["tag"] == "BUTTON"
    assert pin.descriptor["text"] == "Send"
    assert pin.suggested.get("by_role") == "button" and pin.suggested.get("by_text") == "Send"
    assert pin.screenshot_b64


async def test_resolve_acts_on_the_pinned_element(tab):
    reg = PinRegistry()
    box = await tab.page.locator("#go").bounding_box()
    pin = await reg.mint_point(tab, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    loc = await reg.resolve(tab, pin.id)
    assert await loc.get_attribute("id") == "go"


async def test_stale_handle_after_navigation(tab, fixture_url):
    reg = PinRegistry()
    box = await tab.page.locator("#go").bounding_box()
    pin = await reg.mint_point(tab, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    await tab.navigate(f"{fixture_url}/index.html")
    with pytest.raises(E.StaleHandle):
        await reg.resolve(tab, pin.id)


async def test_unknown_handle_is_stale(tab):
    with pytest.raises(E.StaleHandle):
        await PinRegistry().resolve(tab, "el_nope")


async def test_durable_css_survives_a_stamp_wipe(tab, fixture_url):
    """The fallback css/xpath must not be the stamp selector — that dies on nav."""
    reg = PinRegistry()
    box = await tab.page.locator("#go").bounding_box()
    pin = await reg.mint_point(tab, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    assert "data-saidkick-pin" not in pin.css
    assert pin.css and pin.xpath


async def test_mint_rect_selects_enclosed_element(tab):
    reg = PinRegistry()
    # #f wraps the whole form; a rect around it encloses the button.
    box = await tab.page.locator("#f").bounding_box()
    pin = await reg.mint_rect(tab, box["x"], box["y"], box["width"], box["height"], label="the form")
    assert pin.label == "the form"
    assert pin.descriptor["tag"] in ("FORM", "BUTTON", "DIV", "INPUT")


async def test_list_filters_by_tab(tab):
    reg = PinRegistry()
    box = await tab.page.locator("#go").bounding_box()
    await reg.mint_point(tab, box["x"] + 1, box["y"] + 1)
    assert len(reg.list(tab.id)) == 1
    assert reg.list("ctx_other:1") == []
```

- [x] **Step 2:** run → fail (no module).
- [x] **Step 3: implement.** Minting: `cdp = await tab.context.cdp(tab)`, `DOM.enable`,
  `DOM.getNodeForLocation(x,y)` → `backendNodeId` → `DOM.resolveNode` → `Runtime.callFunctionOn`
  stamping `data-saidkick-pin`. Build bundle by evaluating on the stamp locator: descriptor
  (`tag,text,role,label,placeholder,id`), durable `css` (`#id` if present else a nth-of-type
  path), absolute `xpath`, and `screenshot_b64` = base64 of `loc.screenshot()`. `suggested` from
  the descriptor: role+text → `{by_role,by_text}`; label → `{by_label}`; placeholder →
  `{by_placeholder}`; else `{by_text}`. `mint_rect` runs the enclosure JS from the probe, drops
  ancestors of other hits (leaf-most), falls back to common ancestor if empty, then stamps.
  `resolve` returns `Locator(css=f'[data-saidkick-pin="{handle}"]')` after
  `await tab.page.locator(sel).count()` — 0 ⇒ `StaleHandle`.
- [x] **Step 4:** run → pass.
- [x] **Step 5:** commit `feat(pins): registry — mint, resolve, staleness`.

---

### Task 2: Act on a pin handle (REST + engine)

**Files:** Modify `src/saidkick/api.py`; Test `tests/api/test_pins_api.py`.

**Interfaces (Produces):**
- `GET /contexts/{cid}/pins`, `GET /pins/{handle}` (bundle, `?screenshot=1` includes the b64),
  `POST /tabs/{tid}/pin` body `{x,y}` or `{x,y,w,h}` + optional `label` → mints, returns the pin.
- Every acting route accepts `{"handle": "el_x"}` in the body as an alternative to a locator;
  when present it resolves through the registry (→ `StaleHandle` 410 if dead).

- [x] **Step 1: failing tests** (create context+tab, POST a pin at the button's centre, then
  `POST /tabs/{tid}/click {"handle": pin_id}` submits the form; `GET /pins/{h}` returns the
  bundle; a click on a stale handle after navigation returns 410 `StaleHandle`).
- [x] **Step 2–4:** run/implement/run. Thread the shared `PinRegistry` into `create_app`; add a
  `_target(tab, body)` helper: `handle` → `registry.resolve`, else `_locator(body)`.
- [x] **Step 5:** commit `feat(pins,api): mint via REST and act on a handle`.

---

### Task 3: Pin MCP tools + handle on acting tools

**Files:** Modify `src/saidkick/mcp_server.py`; extend `tests/api/test_mcp.py`.

**Interfaces (Produces):** `list_pins(context)`, `read_pin(handle)` (bundle incl. screenshot);
`handle: str | None = None` added to `click`, `type`, `press`, `select`, `hover`, `scroll`,
`highlight`, `find` — when set, resolve through the registry before acting.

- [x] **Step 1: failing tests** — both tools registered with real descriptions; `read_pin`
  description tells the agent the handle may be stale and to fall back to the bundle's selectors;
  a browser test that mints a pin (via the registry directly), then `click` with `handle=` submits
  the form.
- [x] **Step 2–4:** run/implement/run. `read_pin` description states plainly: humans place pins,
  you cannot; use `handle` to act; if you get `StaleHandle`, use the `css`/`xpath`/suggested
  locator in the bundle instead.
- [x] **Step 5:** commit `feat(pins,mcp): list_pins/read_pin and handle targets`.

---

### Task 4: Cockpit — click and drag to pin

**Files:** Modify `cockpit/static/cockpit.js`, `cockpit/templates/session.html`,
`src/saidkick/api.py` (control-socket `pin` message); Test extend `tests/api/test_takeover_ws.py`.

**Interfaces (Produces):** a "Pin" mode toggle in the session UI. While active, a click mints a
point pin and a drag mints a rect pin (canvas→viewport scaling reuses the takeover transform).
The control socket accepts `{"type":"pin", "x","y"[, "w","h"], "label"}`, mints via the registry,
`highlight`s the element so the operator sees the echo in the stream, emits a `pin_created` event,
and replies `{"pin": <info>}`. Pinning does **not** require holding control.

- [x] **Step 1: failing test** — over the control socket (no `take`), send a `pin` message at the
  button's centre; assert the reply carries a `pin` with `descriptor.tag == "BUTTON"` and that
  `registry.list(tab)` grew.
- [x] **Step 2–4:** run/implement/run. JS: a Pin toggle; on mousedown record start, on mouseup
  emit point (no drag) or rect (dragged >5px); render returned pins as a list with their labels.
- [x] **Step 5:** commit `feat(pins,cockpit): click and drag to place pins`.

---

### Task 5: CLI, gate, docs, release 2.1.0

**Files:** `src/saidkick/client.py`, `cli.py`, `README.md`, `SKILL.md`, `CHANGELOG.md`,
`pyproject.toml`.

- [x] **Step 1:** `SaidkickClient.pins(context)` / `read_pin(handle)`; `saidkick pins --context C`.
- [x] **Step 2: full gate**, each as its own step, checking each exit code:
  `uvx ruff check src tests` · `uv run pytest -m "not browser" -q` · `uv run pytest -m browser -q`.
- [x] **Step 3:** README "Pins" section + SKILL.md "Using a pin" (act on `handle`; on `StaleHandle`
  fall back to the bundle's selectors); CHANGELOG 2.1.0; bump version.
- [x] **Step 4:** commit `feat(pins): CLI, docs, 2.1.0`.

---

## Self-Review

Spec §6 pins coverage: minting/point → T1; drag-rect → T1; stamp handle → T1; descriptor+suggested
+css+xpath+clip bundle → T1; StaleHandle → T1/T2/T3; highlight-echo confirmation → T4; agents
list/read only, humans place → enforced by there being no minting MCP tool (T3) and minting living
on the control socket (T4). All mechanisms probe-verified. No new error types — `StaleHandle`
already exists in the closed set from VS1.
