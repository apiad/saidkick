# Find / Press / Screenshot / Rich-Type / Exec-Isolation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Saidkick 0.4.0 — semantic locators (`--by-text` / `--by-label` / `--by-placeholder` + `--within-css`, `--nth`, `--exact`, `--regex`) on every selector-using command, new `/find` debug endpoint, `/press` for CDP keyboard events, `/screenshot` for PNG capture, contenteditable-aware `type`, and scope-isolated `exec` (async-IIFE wrap, breaking).

**Architecture:** Server-side, extend the existing Pydantic request models with a `Locator` mixin and validate "exactly one locator" at the boundary. The content script grows a unified `collectLocator()` resolver that replaces the CSS/XPath-only `collectMatches()`, and a `RESOLVE_RECT` helper for screenshot clips. The background script gets `PRESS` and `SCREENSHOT` handlers backed by `chrome.debugger` `Input.dispatchKeyEvent` and `Page.captureScreenshot`, and wraps `EXECUTE` payloads in `(async () => { ... })()` so state doesn't leak between calls.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, pytest + pytest-asyncio, `fastapi.testclient.TestClient`, `unittest.mock.AsyncMock`/`patch`. Chrome MV3 extension (vanilla JS + CDP via `chrome.debugger`). Typer + Rich CLI.

---

## File Structure

**Modified:**
- `src/saidkick/server.py` — `Locator` mixin, `_validate_locator_set`, extended `SelectorRequest`/`TypeRequest`/`SelectRequest`, `/dom`/`/text` gain locator fields, new `/find`/`/press`/`/screenshot` endpoints, classifier gains new patterns.
- `src/saidkick/client.py` — new `find`/`press`/`screenshot` methods; existing selector methods gain `by_text`/`by_label`/`by_placeholder`/`within_css`/`nth`/`exact`/`regex` kwargs.
- `src/saidkick/cli.py` — new `find`/`press`/`screenshot` commands; `--by-text`/`--by-label`/`--by-placeholder`/`--within-css`/`--nth`/`--exact`/`--regex` on `dom`/`text`/`click`/`type`/`select`.
- `src/saidkick/extension/content.js` — `collectLocator(locator, root)` helper replaces `collectMatches`; `waitForLocator`/`waitForAnyLocator`; rich-type for contenteditable; new `FIND` and `RESOLVE_RECT` handlers.
- `src/saidkick/extension/background.js` — new `PRESS` and `SCREENSHOT` handlers; `EXECUTE` wraps `payload.code` in `(async () => { ... })()`.
- `pyproject.toml` — `0.3.0` → `0.4.0`.
- `CHANGELOG.md` — new `[0.4.0]` entry with the single BREAKING change on `exec`.
- `docs/user-guide.md`, `docs/design.md` — new commands + semantic-locator docs.
- `tests/test_saidkick_enhanced.py`, `tests/test_tabs.py` — existing tests updated to account for new default locator fields in command payloads.

**Created:**
- `tests/test_locators.py` — validator tests (exactly-one rule, regex/exact mutex).
- `tests/test_find.py` — `/find` endpoint tests.
- `tests/test_press.py` — `/press` endpoint tests.
- `tests/test_screenshot.py` — `/screenshot` endpoint tests.

**Unchanged:** `main_world.js`, `popup.html`, `popup.js`, `manifest.json`.

---

## Task 1: `Locator` mixin + validator

**Files:**
- Modify: `src/saidkick/server.py`
- Create: `tests/test_locators.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_locators.py`:

```python
import pytest
from fastapi import HTTPException
from saidkick.server import Locator, _validate_locator, _validate_required_locator


def _loc(**kw):
    """Build a Locator with safe defaults."""
    return Locator(**kw)


def test_required_locator_zero_set_raises_400():
    with pytest.raises(HTTPException) as exc:
        _validate_required_locator(_loc())
    assert exc.value.status_code == 400
    assert "No locator" in exc.value.detail


def test_required_locator_two_set_raises_400():
    with pytest.raises(HTTPException) as exc:
        _validate_required_locator(_loc(css=".a", by_text="b"))
    assert exc.value.status_code == 400
    assert "Ambiguous locator options" in exc.value.detail


@pytest.mark.parametrize("kw", [
    {"css": ".a"},
    {"xpath": "//div"},
    {"by_text": "hi"},
    {"by_label": "hi"},
    {"by_placeholder": "hi"},
])
def test_required_locator_exactly_one_passes(kw):
    _validate_required_locator(_loc(**kw))  # no raise


def test_optional_locator_zero_set_is_fine():
    _validate_locator(_loc())  # no raise


def test_exact_and_regex_mutex():
    with pytest.raises(HTTPException) as exc:
        _validate_locator(_loc(by_text="x", exact=True, regex=True))
    assert exc.value.status_code == 400
    assert "mutually exclusive" in exc.value.detail
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/apiad/Workspace/repos/saidkick && uv run pytest tests/test_locators.py -v`
Expected: ImportError — `Locator`, `_validate_locator`, `_validate_required_locator` don't exist yet.

- [ ] **Step 3: Add the mixin and validators**

Near the top of `src/saidkick/server.py`, after the existing request models, add:

```python
class Locator(BaseModel):
    """Mixin: shared locator fields for every selector-using endpoint."""
    css: Optional[str] = None
    xpath: Optional[str] = None
    by_text: Optional[str] = None
    by_label: Optional[str] = None
    by_placeholder: Optional[str] = None
    within_css: Optional[str] = None
    nth: Optional[int] = None
    exact: bool = False
    regex: bool = False


_LOCATOR_FIELDS = ("css", "xpath", "by_text", "by_label", "by_placeholder")


def _count_locators(loc: Locator) -> int:
    return sum(1 for f in _LOCATOR_FIELDS if getattr(loc, f) is not None)


def _validate_locator(loc: Locator) -> None:
    """Shared checks that apply whether or not a locator is required."""
    if loc.exact and loc.regex:
        raise HTTPException(status_code=400, detail="exact and regex are mutually exclusive")
    if _count_locators(loc) > 1:
        raise HTTPException(
            status_code=400,
            detail="Ambiguous locator options: specify exactly one",
        )


def _validate_required_locator(loc: Locator) -> None:
    _validate_locator(loc)
    if _count_locators(loc) == 0:
        raise HTTPException(
            status_code=400,
            detail="No locator: specify one of css/xpath/by-text/by-label/by-placeholder",
        )


def _locator_payload(loc: Locator) -> Dict[str, Any]:
    """Serialise locator fields for the extension payload."""
    return {
        "css": loc.css, "xpath": loc.xpath,
        "by_text": loc.by_text, "by_label": loc.by_label,
        "by_placeholder": loc.by_placeholder,
        "within_css": loc.within_css, "nth": loc.nth,
        "exact": loc.exact, "regex": loc.regex,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_locators.py -v`
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/saidkick/server.py tests/test_locators.py
git commit -m "feat(server): Locator mixin + required/optional validators"
```

---

## Task 2: Plumb locator fields into existing selector endpoints

**Files:**
- Modify: `src/saidkick/server.py` (request models + endpoints)
- Modify: `tests/test_saidkick_enhanced.py`, `tests/test_tabs.py`, `tests/test_wait_ms.py`

The existing `SelectorRequest` grows the locator fields; `/dom` and `/text` (query-string endpoints) grow the same fields. Every extension payload carries the full locator object so content.js resolves uniformly.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_locators.py`:

```python
from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_click_by_text_propagates_to_extension():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": "Clicked"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/click", json={"tab": "br-aaaa:1", "by_text": "Send"}
        )
    assert r.status_code == 200
    assert seen["payload"]["by_text"] == "Send"
    assert seen["payload"]["css"] is None
    assert seen["payload"]["exact"] is False


def test_click_no_locator_is_400():
    setup_single_browser()
    r = TestClient(app).post("/click", json={"tab": "br-aaaa:1"})
    assert r.status_code == 400
    assert "No locator" in r.json()["detail"]


def test_click_two_locators_is_400():
    setup_single_browser()
    r = TestClient(app).post(
        "/click", json={"tab": "br-aaaa:1", "css": ".a", "by_text": "b"},
    )
    assert r.status_code == 400
    assert "Ambiguous locator options" in r.json()["detail"]


def test_dom_by_label_query_string():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": "<div/>"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/dom?tab=br-aaaa:1&by_label=Username")
    assert r.status_code == 200
    assert seen["payload"]["by_label"] == "Username"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_locators.py -v -k "by_text_propagates or no_locator_is_400 or two_locators or by_label_query"`
Expected: failures — endpoints don't know about the new fields yet.

- [ ] **Step 3: Extend request models + endpoints**

In `src/saidkick/server.py`, replace the existing `SelectorRequest`:

```python
class SelectorRequest(Locator):
    tab: str
    wait_ms: int = 0


class TypeRequest(SelectorRequest):
    text: str
    clear: bool = False


class SelectRequest(SelectorRequest):
    value: str
```

Update each action endpoint to validate the locator and forward the full locator payload. Replace `post_click`:

```python
@app.post("/click")
async def post_click(req: SelectorRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    _validate_required_locator(req)
    response = await manager.send_command(
        browser_id, "CLICK",
        payload={
            "tab_id": tab_id, "wait_ms": req.wait_ms,
            **_locator_payload(req),
        },
        timeout=_command_timeout(wait_ms=req.wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")
```

Replace `post_type`:

```python
@app.post("/type")
async def post_type(req: TypeRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    _validate_required_locator(req)
    response = await manager.send_command(
        browser_id, "TYPE",
        payload={
            "tab_id": tab_id, "wait_ms": req.wait_ms,
            "text": req.text, "clear": req.clear,
            **_locator_payload(req),
        },
        timeout=_command_timeout(wait_ms=req.wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")
```

Replace `post_select`:

```python
@app.post("/select")
async def post_select(req: SelectRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    _validate_required_locator(req)
    response = await manager.send_command(
        browser_id, "SELECT",
        payload={
            "tab_id": tab_id, "wait_ms": req.wait_ms,
            "value": req.value,
            **_locator_payload(req),
        },
        timeout=_command_timeout(wait_ms=req.wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")
```

Replace `get_dom`:

```python
@app.get("/dom")
async def get_dom(
    tab: str,
    css: Optional[str] = None,
    xpath: Optional[str] = None,
    by_text: Optional[str] = None,
    by_label: Optional[str] = None,
    by_placeholder: Optional[str] = None,
    within_css: Optional[str] = None,
    nth: Optional[int] = None,
    exact: bool = False,
    regex: bool = False,
    all: bool = False,
    wait_ms: int = 0,
):
    browser_id, tab_id = _parse_or_400(tab)
    loc = Locator(
        css=css, xpath=xpath, by_text=by_text, by_label=by_label,
        by_placeholder=by_placeholder, within_css=within_css,
        nth=nth, exact=exact, regex=regex,
    )
    _validate_required_locator(loc)
    response = await manager.send_command(
        browser_id, "GET_DOM",
        payload={
            "tab_id": tab_id, "wait_ms": wait_ms, "all": all,
            **_locator_payload(loc),
        },
        timeout=_command_timeout(wait_ms=wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")
```

Replace `get_text`:

```python
@app.get("/text")
async def get_text(
    tab: str,
    css: Optional[str] = None,
    xpath: Optional[str] = None,
    by_text: Optional[str] = None,
    by_label: Optional[str] = None,
    by_placeholder: Optional[str] = None,
    within_css: Optional[str] = None,
    nth: Optional[int] = None,
    exact: bool = False,
    regex: bool = False,
    wait_ms: int = 0,
):
    browser_id, tab_id = _parse_or_400(tab)
    loc = Locator(
        css=css, xpath=xpath, by_text=by_text, by_label=by_label,
        by_placeholder=by_placeholder, within_css=within_css,
        nth=nth, exact=exact, regex=regex,
    )
    # text allows the "no locator" case (whole body) — treat as optional
    _validate_locator(loc)
    response = await manager.send_command(
        browser_id, "GET_TEXT",
        payload={
            "tab_id": tab_id, "wait_ms": wait_ms,
            **_locator_payload(loc),
        },
        timeout=_command_timeout(wait_ms=wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")
```

- [ ] **Step 4: Update the older tests to expect the new payload shape**

Existing endpoint tests assert exact payload equality. The extension payload now includes the locator fields. Update:

In `tests/test_saidkick_enhanced.py`, replace the `test_dom_anchoring_params` call's assert:

```python
    mock_send.assert_called_with(
        "br-aaaa", "GET_DOM",
        payload={
            "tab_id": 3, "wait_ms": 0, "all": True,
            "css": ".test", "xpath": None,
            "by_text": None, "by_label": None, "by_placeholder": None,
            "within_css": None, "nth": None, "exact": False, "regex": False,
        },
        timeout=10.0,
    )
```

Replace the three `mock_send.assert_called_with` calls in `test_interaction_endpoints`:

```python
        mock_send.assert_called_with(
            "br-aaaa", "CLICK",
            payload={
                "tab_id": 1, "wait_ms": 0,
                "css": "#btn", "xpath": None,
                "by_text": None, "by_label": None, "by_placeholder": None,
                "within_css": None, "nth": None, "exact": False, "regex": False,
            },
            timeout=10.0,
        )

        # ... (similar for /type and /select — include the full locator bag with defaults)
```

And for `/type`:

```python
        mock_send.assert_called_with(
            "br-aaaa", "TYPE",
            payload={
                "tab_id": 2, "wait_ms": 0,
                "text": "hello", "clear": True,
                "css": "#input", "xpath": None,
                "by_text": None, "by_label": None, "by_placeholder": None,
                "within_css": None, "nth": None, "exact": False, "regex": False,
            },
            timeout=10.0,
        )
```

And for `/select`:

```python
        mock_send.assert_called_with(
            "br-aaaa", "SELECT",
            payload={
                "tab_id": 3, "wait_ms": 0, "value": "opt1",
                "css": None, "xpath": "//select",
                "by_text": None, "by_label": None, "by_placeholder": None,
                "within_css": None, "nth": None, "exact": False, "regex": False,
            },
            timeout=10.0,
        )
```

In `tests/test_tabs.py` `test_dom_routes_to_correct_browser`, add `wait_ms` / locator asserts (only check the fields the test cares about):

```python
    assert seen["payload"]["tab_id"] == 7
    assert seen["payload"]["css"] == ".foo"
    assert seen["payload"]["wait_ms"] == 0
    assert seen["payload"]["exact"] is False
```

In `tests/test_wait_ms.py`, `test_dom_passes_wait_ms_to_extension` and friends only check specific fields — no change needed.

In `tests/test_tabs.py`, `test_execute_unknown_valid_browser_is_404` uses `/execute` which is unchanged. OK.

For `/text` tests in `tests/test_text.py`, update:

```python
def test_text_routes_with_defaults():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": "Hello world"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/text?tab=br-aaaa:1")
    assert r.status_code == 200
    assert r.json() == "Hello world"
    assert seen["args"][1] == "GET_TEXT"
    assert seen["payload"]["tab_id"] == 1
    assert seen["payload"]["css"] is None
    assert seen["payload"]["by_text"] is None
    assert seen["payload"]["wait_ms"] == 0
```

`test_text_with_css_scope_and_wait` only asserts on `css` and `wait_ms` — already compatible.

`test_text_element_not_found_is_404` and `test_text_malformed_tab_is_400` — no change needed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest tests/ --ignore=tests/test_saidkick_e2e.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/saidkick/server.py tests/test_locators.py tests/test_saidkick_enhanced.py tests/test_tabs.py tests/test_text.py
git commit -m "feat(server): semantic locator fields on dom/text/click/type/select"
```

---

## Task 3: `GET /find` endpoint

**Files:**
- Modify: `src/saidkick/server.py`
- Create: `tests/test_find.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_find.py`:

```python
from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_find_by_text_routes_to_extension():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {
            "success": True,
            "payload": [
                {"selector": "div:nth-of-type(3)", "tag": "DIV",
                 "role": "listitem", "name": "Leydis CIMEX",
                 "text": "Leydis CIMEX", "rect": {"x": 0, "y": 0, "w": 100, "h": 40},
                 "visible": True},
            ],
        }
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/find?tab=br-aaaa:1&by_text=Leydis")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["name"] == "Leydis CIMEX"
    assert seen["args"][1] == "FIND"
    assert seen["payload"]["by_text"] == "Leydis"


def test_find_no_locator_is_400():
    setup_single_browser()
    r = TestClient(app).get("/find?tab=br-aaaa:1")
    assert r.status_code == 400
    assert "No locator" in r.json()["detail"]


def test_find_two_locators_is_400():
    setup_single_browser()
    r = TestClient(app).get("/find?tab=br-aaaa:1&css=.a&by_text=b")
    assert r.status_code == 400


def test_find_with_within_and_nth():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": []}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get(
            "/find?tab=br-aaaa:1&by_text=send&within_css=.modal&nth=1&exact=true"
        )
    assert r.status_code == 200
    assert seen["payload"]["within_css"] == ".modal"
    assert seen["payload"]["nth"] == 1
    assert seen["payload"]["exact"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_find.py -v`
Expected: 404s — endpoint doesn't exist.

- [ ] **Step 3: Add `/find` endpoint**

In `src/saidkick/server.py`, add before `/tabs`:

```python
@app.get("/find")
async def get_find(
    tab: str,
    css: Optional[str] = None,
    xpath: Optional[str] = None,
    by_text: Optional[str] = None,
    by_label: Optional[str] = None,
    by_placeholder: Optional[str] = None,
    within_css: Optional[str] = None,
    nth: Optional[int] = None,
    exact: bool = False,
    regex: bool = False,
    wait_ms: int = 0,
):
    browser_id, tab_id = _parse_or_400(tab)
    loc = Locator(
        css=css, xpath=xpath, by_text=by_text, by_label=by_label,
        by_placeholder=by_placeholder, within_css=within_css,
        nth=nth, exact=exact, regex=regex,
    )
    _validate_required_locator(loc)
    response = await manager.send_command(
        browser_id, "FIND",
        payload={"tab_id": tab_id, "wait_ms": wait_ms, **_locator_payload(loc)},
        timeout=_command_timeout(wait_ms=wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest tests/ --ignore=tests/test_saidkick_e2e.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/saidkick/server.py tests/test_find.py
git commit -m "feat(server): GET /find debug endpoint for locator resolution"
```

---

## Task 4: `POST /press` endpoint

**Files:**
- Modify: `src/saidkick/server.py`
- Create: `tests/test_press.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_press.py`:

```python
from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_press_enter_no_target():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"pressed": "Enter"}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/press", json={"tab": "br-aaaa:1", "key": "Enter"}
        )
    assert r.status_code == 200
    assert r.json() == {"pressed": "Enter"}
    assert seen["args"][1] == "PRESS"
    assert seen["payload"]["key"] == "Enter"
    assert seen["payload"]["modifiers"] == []
    assert seen["payload"]["css"] is None


def test_press_with_modifiers_and_locator():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"pressed": "k"}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/press",
            json={
                "tab": "br-aaaa:1", "key": "k",
                "modifiers": ["ctrl", "shift"],
                "by_label": "Search",
            },
        )
    assert r.status_code == 200
    assert seen["payload"]["modifiers"] == ["ctrl", "shift"]
    assert seen["payload"]["by_label"] == "Search"


def test_press_bad_modifier_is_400():
    setup_single_browser()
    r = TestClient(app).post(
        "/press", json={"tab": "br-aaaa:1", "key": "a", "modifiers": ["hyper"]}
    )
    assert r.status_code == 400
    assert "unknown modifier" in r.json()["detail"].lower()


def test_press_missing_key_is_422():
    setup_single_browser()
    r = TestClient(app).post("/press", json={"tab": "br-aaaa:1"})
    assert r.status_code == 422


def test_press_two_locators_is_400():
    setup_single_browser()
    r = TestClient(app).post(
        "/press",
        json={"tab": "br-aaaa:1", "key": "a",
              "css": ".a", "by_text": "b"},
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_press.py -v`
Expected: failures — endpoint and request model don't exist.

- [ ] **Step 3: Add `PressRequest` + `/press` endpoint**

In `src/saidkick/server.py`, add with the other request models:

```python
_VALID_MODIFIERS = {"ctrl", "shift", "alt", "meta"}


class PressRequest(Locator):
    tab: str
    key: str
    modifiers: List[str] = []
    wait_ms: int = 0
```

Add the endpoint before `/tabs`:

```python
@app.post("/press")
async def post_press(req: PressRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    _validate_locator(req)  # optional locator
    bad = [m for m in req.modifiers if m not in _VALID_MODIFIERS]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"unknown modifier: {bad[0]}",
        )
    response = await manager.send_command(
        browser_id, "PRESS",
        payload={
            "tab_id": tab_id, "key": req.key,
            "modifiers": req.modifiers, "wait_ms": req.wait_ms,
            **_locator_payload(req),
        },
        timeout=_command_timeout(wait_ms=req.wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest tests/ --ignore=tests/test_saidkick_e2e.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/saidkick/server.py tests/test_press.py
git commit -m "feat(server): POST /press with optional locator focus target"
```

---

## Task 5: `GET /screenshot` endpoint

**Files:**
- Modify: `src/saidkick/server.py`
- Create: `tests/test_screenshot.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_screenshot.py`:

```python
import base64
from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


SAMPLE_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_screenshot_viewport():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {
            "success": True,
            "payload": {"png_base64": SAMPLE_PNG_B64, "width": 1920, "height": 1080},
        }
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/screenshot?tab=br-aaaa:1")
    assert r.status_code == 200
    body = r.json()
    assert body["png_base64"] == SAMPLE_PNG_B64
    assert body["width"] == 1920
    assert seen["args"][1] == "SCREENSHOT"
    assert seen["payload"]["full_page"] is False
    assert seen["payload"]["css"] is None


def test_screenshot_with_full_page_and_clip():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {
            "success": True,
            "payload": {"png_base64": SAMPLE_PNG_B64, "width": 400, "height": 300},
        }
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get(
            "/screenshot?tab=br-aaaa:1&by_text=Article&full_page=true"
        )
    assert r.status_code == 200
    assert seen["payload"]["full_page"] is True
    assert seen["payload"]["by_text"] == "Article"


def test_screenshot_two_locators_is_400():
    setup_single_browser()
    r = TestClient(app).get(
        "/screenshot?tab=br-aaaa:1&css=.a&by_text=b"
    )
    assert r.status_code == 400


def test_screenshot_element_not_found_is_404():
    setup_single_browser()
    async def fake_send(*args, **kwargs):
        return {"success": False, "payload": "element not found"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/screenshot?tab=br-aaaa:1&css=.nope")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_screenshot.py -v`
Expected: failures — endpoint doesn't exist.

- [ ] **Step 3: Add `/screenshot` endpoint**

In `src/saidkick/server.py`, add before `/tabs`:

```python
@app.get("/screenshot")
async def get_screenshot(
    tab: str,
    css: Optional[str] = None,
    xpath: Optional[str] = None,
    by_text: Optional[str] = None,
    by_label: Optional[str] = None,
    by_placeholder: Optional[str] = None,
    within_css: Optional[str] = None,
    nth: Optional[int] = None,
    exact: bool = False,
    regex: bool = False,
    full_page: bool = False,
):
    browser_id, tab_id = _parse_or_400(tab)
    loc = Locator(
        css=css, xpath=xpath, by_text=by_text, by_label=by_label,
        by_placeholder=by_placeholder, within_css=within_css,
        nth=nth, exact=exact, regex=regex,
    )
    _validate_locator(loc)  # optional
    response = await manager.send_command(
        browser_id, "SCREENSHOT",
        payload={
            "tab_id": tab_id, "full_page": full_page,
            **_locator_payload(loc),
        },
        timeout=_command_timeout(timeout_ms=15000),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest tests/ --ignore=tests/test_saidkick_e2e.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/saidkick/server.py tests/test_screenshot.py
git commit -m "feat(server): GET /screenshot with optional locator clip"
```

---

## Task 6: Content-script refactor — `collectLocator` + `FIND` / `RESOLVE_RECT`

**Files:**
- Modify: `src/saidkick/extension/content.js`

No automated tests — verified by manual smoke in Task 15.

- [ ] **Step 1: Rewrite `content.js`**

Replace `src/saidkick/extension/content.js`:

```javascript
(function() {
    console.log("Saidkick: Content script (isolated world) initializing");
    try {
        chrome.runtime.sendMessage({
            type: "log", level: "log",
            data: "Saidkick: Content script connected",
            timestamp: new Date().toISOString(),
            url: window.location.href,
        });
    } catch (e) {}

    window.addEventListener('message', (event) => {
        if (event.source !== window) return;
        const msg = event.data;
        if (msg && msg.type === 'saidkick-log') {
            try { chrome.runtime.sendMessage({ type: "log", ...msg.detail }); }
            catch (e) {}
        }
    });

    // Locator resolver.
    function resolveRoot(locator) {
        if (!locator.within_css) return document;
        const root = document.querySelector(locator.within_css);
        if (!root) throw new Error(`within-css matched no element: ${locator.within_css}`);
        return root;
    }

    function matchesPredicate(locator) {
        // Returns a predicate(el) for text/label/placeholder locators.
        const val = locator.by_text ?? locator.by_label ?? locator.by_placeholder;
        if (val == null) return null;
        let test;
        if (locator.regex) {
            let re;
            try { re = new RegExp(val); }
            catch (e) { throw new Error(`invalid regex: ${e.message}`); }
            test = s => re.test(s);
        } else if (locator.exact) {
            test = s => s === val;
        } else {
            const needle = val.toLowerCase();
            test = s => s.toLowerCase().includes(needle);
        }
        const getText = el => {
            if (locator.by_text != null) {
                return (el.textContent || el.innerText || "").trim();
            }
            if (locator.by_label != null) {
                const aria = el.getAttribute("aria-label");
                if (aria) return aria;
                const labelledby = el.getAttribute("aria-labelledby");
                if (labelledby) {
                    const parts = labelledby.split(/\s+/)
                        .map(id => document.getElementById(id)?.textContent || "")
                        .join(" ").trim();
                    if (parts) return parts;
                }
                if (el.id) {
                    const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                    if (label) return (label.textContent || "").trim();
                }
                return "";
            }
            if (locator.by_placeholder != null) {
                return el.getAttribute("placeholder") || "";
            }
            return "";
        };
        return el => test(getText(el));
    }

    function collectLocator(locator) {
        const root = resolveRoot(locator);
        let matches;
        if (locator.css) {
            matches = Array.from(root.querySelectorAll(locator.css));
        } else if (locator.xpath) {
            const result = document.evaluate(
                locator.xpath, root, null,
                XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null
            );
            matches = [];
            for (let i = 0; i < result.snapshotLength; i++) {
                matches.push(result.snapshotItem(i));
            }
        } else {
            const pred = matchesPredicate(locator);
            if (!pred) throw new Error("No selector provided");
            matches = Array.from(root.querySelectorAll("*")).filter(pred);
        }
        if (locator.nth != null) {
            const el = matches[locator.nth];
            return el ? [el] : [];
        }
        return matches;
    }

    async function waitForLocator(locator, waitMs) {
        const start = Date.now();
        const POLL = 100;
        while (true) {
            let matches;
            try { matches = collectLocator(locator); }
            catch (e) { throw e; }
            if (matches.length === 1) return matches[0];
            if (matches.length > 1) {
                if (Date.now() - start >= waitMs) {
                    throw new Error(`Ambiguous locator: found ${matches.length} matches`);
                }
            } else if (Date.now() - start >= waitMs) {
                throw new Error("element not found");
            }
            await new Promise(r => setTimeout(r, POLL));
        }
    }

    async function waitForAnyLocator(locator, waitMs) {
        const start = Date.now();
        const POLL = 100;
        while (true) {
            let matches;
            try { matches = collectLocator(locator); }
            catch (e) { return []; }
            if (matches.length >= 1) return matches;
            if (Date.now() - start >= waitMs) return matches;
            await new Promise(r => setTimeout(r, POLL));
        }
    }

    // Build a unique CSS path back to an element.
    function uniqueSelector(el) {
        if (!(el instanceof Element)) return "";
        if (el.id) return `#${CSS.escape(el.id)}`;
        const parts = [];
        while (el && el.nodeType === 1 && el !== document.body) {
            let part = el.tagName.toLowerCase();
            const parent = el.parentElement;
            if (parent) {
                const siblings = Array.from(parent.children).filter(s => s.tagName === el.tagName);
                if (siblings.length > 1) {
                    part += `:nth-of-type(${siblings.indexOf(el) + 1})`;
                }
            }
            parts.unshift(part);
            el = parent;
        }
        return "body > " + parts.join(" > ");
    }

    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
        const { type, payload } = request;

        const handle = async () => {
            const waitMs = (payload && payload.wait_ms) || 0;

            if (type === "GET_DOM") {
                const { all } = payload || {};
                let matches;
                if (!payload.css && !payload.xpath && !payload.by_text
                    && !payload.by_label && !payload.by_placeholder
                    && !payload.within_css) {
                    matches = [document.documentElement];
                } else if (all) {
                    matches = await waitForAnyLocator(payload, waitMs);
                    if (matches.length === 0) throw new Error("element not found");
                } else {
                    matches = [await waitForLocator(payload, waitMs)];
                }
                const output = all
                    ? matches.map(m => m.outerHTML).join("\n")
                    : matches[0].outerHTML;
                return { success: true, payload: output };
            }

            if (type === "CLICK") {
                const element = await waitForLocator(payload, waitMs);
                element.click();
                element.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
                element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
                return { success: true, payload: "Clicked" };
            }

            if (type === "TYPE") {
                const element = await waitForLocator(payload, waitMs);
                element.focus();
                if (element.isContentEditable) {
                    if (payload.clear) {
                        const range = document.createRange();
                        range.selectNodeContents(element);
                        const sel = window.getSelection();
                        sel.removeAllRanges();
                        sel.addRange(range);
                        document.execCommand("delete");
                    }
                    document.execCommand("insertText", false, payload.text);
                    element.dispatchEvent(new Event("input", { bubbles: true }));
                    element.dispatchEvent(new Event("change", { bubbles: true }));
                    return { success: true, payload: "Typed" };
                }
                if (payload.clear) {
                    if (element.tagName === "INPUT" || element.tagName === "TEXTAREA") {
                        element.value = "";
                    }
                }
                if (element.tagName === "INPUT" || element.tagName === "TEXTAREA") {
                    element.value += payload.text;
                }
                element.dispatchEvent(new Event("input", { bubbles: true }));
                element.dispatchEvent(new Event("change", { bubbles: true }));
                element.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true }));
                element.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true }));
                return { success: true, payload: "Typed" };
            }

            if (type === "SELECT") {
                const element = await waitForLocator(payload, waitMs);
                if (element.tagName !== "SELECT") {
                    throw new Error("Element is not a <select>");
                }
                const val = payload.value;
                let found = false;
                for (const opt of element.options) {
                    if (opt.value === val || opt.text === val) {
                        element.value = opt.value;
                        found = true;
                        break;
                    }
                }
                if (!found) throw new Error(`option not found: ${val}`);
                element.dispatchEvent(new Event("change", { bubbles: true }));
                return { success: true, payload: "Selected" };
            }

            if (type === "GET_TEXT") {
                const hasLocator = payload.css || payload.xpath || payload.by_text
                    || payload.by_label || payload.by_placeholder;
                const element = hasLocator
                    ? await waitForLocator(payload, waitMs)
                    : document.body;
                return { success: true, payload: element.innerText || "" };
            }

            if (type === "FIND") {
                const matches = await waitForAnyLocator(payload, waitMs);
                const out = matches.slice(0, 50).map(el => {
                    const rect = el.getBoundingClientRect();
                    return {
                        selector: uniqueSelector(el),
                        tag: el.tagName,
                        role: el.getAttribute("role") || null,
                        name: el.getAttribute("aria-label") || (el.textContent || "").trim().slice(0, 80),
                        text: (el.textContent || "").trim().slice(0, 200),
                        rect: {x: Math.round(rect.x), y: Math.round(rect.y),
                               w: Math.round(rect.width), h: Math.round(rect.height)},
                        visible: !!el.offsetParent && rect.width > 0 && rect.height > 0,
                    };
                });
                return { success: true, payload: out };
            }

            if (type === "RESOLVE_RECT") {
                // Helper: resolve a locator to a bounding rect for background-side screenshot clip.
                const el = await waitForLocator(payload, waitMs);
                const rect = el.getBoundingClientRect();
                return { success: true, payload: {
                    x: Math.round(rect.x), y: Math.round(rect.y),
                    width: Math.round(rect.width), height: Math.round(rect.height),
                }};
            }

            throw new Error(`unknown command: ${type}`);
        };

        handle().then(
            result => sendResponse(result),
            err => sendResponse({ success: false, payload: err.message }),
        );
        return true;
    });
})();
```

- [ ] **Step 2: Commit**

```bash
git add src/saidkick/extension/content.js
git commit -m "feat(extension): locator resolver, rich-type, FIND, RESOLVE_RECT"
```

---

## Task 7: Background-script — `PRESS` handler

**Files:**
- Modify: `src/saidkick/extension/background.js`

- [ ] **Step 1: Add `PRESS` handler**

In `src/saidkick/extension/background.js`, add a helper near `ensureDebuggerAttached`:

```javascript
const MODIFIER_BITS = { alt: 1, ctrl: 2, meta: 4, shift: 8 };

function modifiersMask(mods) {
    return (mods || []).reduce((acc, m) => acc | (MODIFIER_BITS[m] || 0), 0);
}

// Minimal JS key → CDP keyCode map for the common keys. Unknown keys pass through.
const KEY_TO_CDP = {
    "Enter":     {keyCode: 13,  code: "Enter",     text: "\r"},
    "Escape":    {keyCode: 27,  code: "Escape"},
    "Tab":       {keyCode: 9,   code: "Tab",       text: "\t"},
    "Backspace": {keyCode: 8,   code: "Backspace"},
    "ArrowUp":    {keyCode: 38, code: "ArrowUp"},
    "ArrowDown":  {keyCode: 40, code: "ArrowDown"},
    "ArrowLeft":  {keyCode: 37, code: "ArrowLeft"},
    "ArrowRight": {keyCode: 39, code: "ArrowRight"},
    "Home":      {keyCode: 36,  code: "Home"},
    "End":       {keyCode: 35,  code: "End"},
    "PageUp":    {keyCode: 33,  code: "PageUp"},
    "PageDown":  {keyCode: 34,  code: "PageDown"},
    "Delete":    {keyCode: 46,  code: "Delete"},
};

function cdpKeyParams(key) {
    const mapped = KEY_TO_CDP[key];
    if (mapped) return mapped;
    // Single character keys.
    if (key.length === 1) {
        return {
            keyCode: key.toUpperCase().charCodeAt(0),
            code: "Key" + key.toUpperCase(),
            text: key,
            key,
        };
    }
    // Function keys, etc. — best-effort passthrough.
    return { code: key, key };
}

async function dispatchKey(tabId, key, modifiers) {
    const target = { tabId };
    const base = cdpKeyParams(key);
    const mods = modifiersMask(modifiers);
    await new Promise((resolve, reject) => {
        chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
            type: "keyDown", key, ...base, modifiers: mods,
        }, () => {
            if (chrome.runtime.lastError) reject(chrome.runtime.lastError);
            else resolve();
        });
    });
    if (base.text) {
        await new Promise((resolve, reject) => {
            chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
                type: "char", key, ...base, modifiers: mods,
            }, () => {
                if (chrome.runtime.lastError) reject(chrome.runtime.lastError);
                else resolve();
            });
        });
    }
    await new Promise((resolve, reject) => {
        chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
            type: "keyUp", key, ...base, modifiers: mods,
        }, () => {
            if (chrome.runtime.lastError) reject(chrome.runtime.lastError);
            else resolve();
        });
    });
}
```

Inside `socket.onmessage`, after the existing branches (so it falls through the `tab = chrome.tabs.get(tabId)` preamble), add:

Wait — `PRESS` uses `tab_id` from the payload, so it participates in the existing preamble that resolves `tab`. Add this branch at the same level as `GET_DOM/CLICK/TYPE/SELECT/GET_TEXT`:

```javascript
        if (type === "PRESS") {
            try {
                await ensureDebuggerAttached(tab.id);
                // If a locator is provided, ask the content script to resolve + focus.
                const hasLocator = payload.css || payload.xpath || payload.by_text
                    || payload.by_label || payload.by_placeholder;
                if (hasLocator) {
                    const resp = await new Promise(resolve => {
                        chrome.tabs.sendMessage(tab.id, {
                            type: "CLICK", payload,   // CLICK focuses by calling .click, but we actually want focus — piggyback with a dedicated FOCUS op next line
                        }, resolve);
                    });
                    // Simpler: issue a plain CLICK — clicking focuses the element.
                    if (!resp || !resp.success) {
                        socket.send(JSON.stringify({
                            type: "RESPONSE", id, success: false,
                            payload: resp?.payload || "focus failed",
                        }));
                        return;
                    }
                }
                await dispatchKey(tab.id, payload.key, payload.modifiers || []);
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: true,
                    payload: { pressed: payload.key },
                }));
            } catch (err) {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: false,
                    payload: err.message || String(err),
                }));
            }
            return;
        }
```

Hmm — on reflection, using CLICK to focus a field is wrong for non-clickable inputs. Replace the locator-focus flow with a dedicated content-script op. Revise the content-script addition (add to `content.js` in Task 6's handler list):

```javascript
            if (type === "FOCUS") {
                const el = await waitForLocator(payload, waitMs);
                el.focus();
                return { success: true, payload: "Focused" };
            }
```

(Add this to `content.js` in Task 6 before committing.)

Then in `background.js` PRESS branch, replace the inner `CLICK` call with:

```javascript
                if (hasLocator) {
                    const resp = await new Promise(resolve => {
                        chrome.tabs.sendMessage(tab.id, { type: "FOCUS", payload }, resolve);
                    });
                    if (!resp || !resp.success) {
                        socket.send(JSON.stringify({
                            type: "RESPONSE", id, success: false,
                            payload: resp?.payload || "focus failed",
                        }));
                        return;
                    }
                }
```

- [ ] **Step 2: Revise Task 6's content.js to include the `FOCUS` handler**

If Task 6 is already committed, amend via a small follow-up commit:

Add the `FOCUS` branch to the `handle` function in `content.js` (before `GET_TEXT` or anywhere in the branch list). Commit.

- [ ] **Step 3: Commit PRESS**

```bash
git add src/saidkick/extension/background.js src/saidkick/extension/content.js
git commit -m "feat(extension): PRESS handler via CDP Input.dispatchKeyEvent; FOCUS helper"
```

---

## Task 8: Background-script — `SCREENSHOT` handler

**Files:**
- Modify: `src/saidkick/extension/background.js`

- [ ] **Step 1: Add `SCREENSHOT` handler**

Inside `socket.onmessage`, alongside the other tab-id commands:

```javascript
        if (type === "SCREENSHOT") {
            try {
                await ensureDebuggerAttached(tab.id);
                let clip = null;
                const hasLocator = payload.css || payload.xpath || payload.by_text
                    || payload.by_label || payload.by_placeholder;
                if (hasLocator) {
                    const resp = await new Promise(resolve => {
                        chrome.tabs.sendMessage(tab.id, {
                            type: "RESOLVE_RECT", payload,
                        }, resolve);
                    });
                    if (!resp || !resp.success) {
                        socket.send(JSON.stringify({
                            type: "RESPONSE", id, success: false,
                            payload: resp?.payload || "resolve rect failed",
                        }));
                        return;
                    }
                    const r = resp.payload;
                    clip = { x: r.x, y: r.y, width: r.width, height: r.height, scale: 1 };
                }
                const cdpParams = { format: "png" };
                if (clip) cdpParams.clip = clip;
                if (payload.full_page) cdpParams.captureBeyondViewport = true;
                const shot = await new Promise((resolve, reject) => {
                    chrome.debugger.sendCommand(
                        { tabId: tab.id }, "Page.captureScreenshot", cdpParams,
                        (result) => {
                            if (chrome.runtime.lastError) reject(chrome.runtime.lastError);
                            else resolve(result);
                        }
                    );
                });
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: true,
                    payload: {
                        png_base64: shot.data,
                        width: clip ? clip.width : (window.screen?.width || 0),
                        height: clip ? clip.height : (window.screen?.height || 0),
                    },
                }));
            } catch (err) {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: false,
                    payload: err.message || String(err),
                }));
            }
            return;
        }
```

- [ ] **Step 2: Commit**

```bash
git add src/saidkick/extension/background.js
git commit -m "feat(extension): SCREENSHOT handler via CDP Page.captureScreenshot"
```

---

## Task 9: Background-script — `EXECUTE` IIFE-wrap (breaking)

**Files:**
- Modify: `src/saidkick/extension/background.js`

- [ ] **Step 1: Wrap user code**

In the existing `EXECUTE` branch of `background.js`, locate the `Runtime.evaluate` call and change the `expression` construction:

```javascript
                const wrappedCode = `(async () => {\n${payload.code}\n})()`;
                chrome.debugger.sendCommand(
                    debugTarget, "Runtime.evaluate",
                    { expression: wrappedCode, returnByValue: true, awaitPromise: true },
                    (result) => {
                        // ... unchanged response handling
                    }
                );
```

(The surrounding code — `ensureDebuggerAttached`, error handling — stays the same.)

- [ ] **Step 2: Commit**

```bash
git add src/saidkick/extension/background.js
git commit -m "feat(extension): wrap EXECUTE in async IIFE for scope isolation (breaking)

BREAKING: callers must 'return' their value from exec; bare expressions
no longer propagate as the response payload."
```

---

## Task 10: Python client additions

**Files:**
- Modify: `src/saidkick/client.py`

- [ ] **Step 1: Extend `SaidkickClient`**

Write `src/saidkick/client.py`:

```python
import httpx
from typing import List, Dict, Any, Optional


class SaidkickClient:
    def __init__(self, base_url: str = "http://localhost:6992"):
        self.base_url = base_url

    # ---- locator passthrough helper ----
    @staticmethod
    def _locator_params(
        css=None, xpath=None, by_text=None, by_label=None, by_placeholder=None,
        within_css=None, nth=None, exact=False, regex=False,
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if css is not None: out["css"] = css
        if xpath is not None: out["xpath"] = xpath
        if by_text is not None: out["by_text"] = by_text
        if by_label is not None: out["by_label"] = by_label
        if by_placeholder is not None: out["by_placeholder"] = by_placeholder
        if within_css is not None: out["within_css"] = within_css
        if nth is not None: out["nth"] = nth
        if exact: out["exact"] = True
        if regex: out["regex"] = True
        return out

    # ---- introspection ----
    def list_tabs(self, active: bool = False) -> List[Dict[str, Any]]:
        params = {"active": "true" if active else "false"}
        r = httpx.get(f"{self.base_url}/tabs", params=params)
        r.raise_for_status()
        return r.json()

    def get_logs(self, limit=100, grep=None, browser=None):
        params: Dict[str, Any] = {"limit": limit}
        if grep: params["grep"] = grep
        if browser: params["browser"] = browser
        r = httpx.get(f"{self.base_url}/console", params=params)
        r.raise_for_status()
        return r.json()

    def find(self, tab: str, wait_ms: int = 0, **locator) -> List[Dict[str, Any]]:
        params = {"tab": tab, "wait_ms": wait_ms, **self._locator_params(**locator)}
        r = httpx.get(f"{self.base_url}/find", params=params,
                      timeout=wait_ms / 1000 + 10)
        r.raise_for_status()
        return r.json()

    # ---- navigation ----
    def navigate(self, tab, url, wait="dom", timeout_ms=15000):
        r = httpx.post(
            f"{self.base_url}/navigate",
            json={"tab": tab, "url": url, "wait": wait, "timeout_ms": timeout_ms},
            timeout=timeout_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def open(self, browser, url, wait="dom", timeout_ms=15000, activate=False):
        r = httpx.post(
            f"{self.base_url}/open",
            json={"browser": browser, "url": url, "wait": wait,
                  "timeout_ms": timeout_ms, "activate": activate},
            timeout=timeout_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    # ---- DOM / text / screenshot ----
    def get_dom(self, tab, all_matches=False, wait_ms=0, **locator):
        params = {"tab": tab, "all": all_matches, "wait_ms": wait_ms,
                  **self._locator_params(**locator)}
        r = httpx.get(f"{self.base_url}/dom", params=params,
                      timeout=wait_ms / 1000 + 10)
        r.raise_for_status()
        return r.json()

    def text(self, tab, wait_ms=0, **locator):
        params = {"tab": tab, "wait_ms": wait_ms, **self._locator_params(**locator)}
        r = httpx.get(f"{self.base_url}/text", params=params,
                      timeout=wait_ms / 1000 + 10)
        r.raise_for_status()
        return r.json()

    def screenshot(self, tab, full_page: bool = False, **locator) -> Dict[str, Any]:
        params = {"tab": tab, "full_page": "true" if full_page else "false",
                  **self._locator_params(**locator)}
        r = httpx.get(f"{self.base_url}/screenshot", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    # ---- JS execution ----
    def execute(self, tab: str, code: str) -> Any:
        r = httpx.post(f"{self.base_url}/execute", json={"tab": tab, "code": code})
        r.raise_for_status()
        return r.json()

    # ---- interaction ----
    def click(self, tab, wait_ms=0, **locator):
        r = httpx.post(
            f"{self.base_url}/click",
            json={"tab": tab, "wait_ms": wait_ms, **self._locator_params(**locator)},
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def type(self, tab, text, clear=False, wait_ms=0, **locator):
        r = httpx.post(
            f"{self.base_url}/type",
            json={"tab": tab, "text": text, "clear": clear, "wait_ms": wait_ms,
                  **self._locator_params(**locator)},
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def select(self, tab, value, wait_ms=0, **locator):
        r = httpx.post(
            f"{self.base_url}/select",
            json={"tab": tab, "value": value, "wait_ms": wait_ms,
                  **self._locator_params(**locator)},
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def press(self, tab, key, modifiers=None, wait_ms=0, **locator):
        r = httpx.post(
            f"{self.base_url}/press",
            json={"tab": tab, "key": key, "modifiers": modifiers or [],
                  "wait_ms": wait_ms, **self._locator_params(**locator)},
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 2: Smoke**

Run: `uv run pytest tests/ --ignore=tests/test_saidkick_e2e.py -q`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add src/saidkick/client.py
git commit -m "feat(client): find/press/screenshot + locator kwargs on selector methods"
```

---

## Task 11: CLI — new commands + flags

**Files:**
- Modify: `src/saidkick/cli.py`

- [ ] **Step 1: Add locator flags + new commands**

Full rewrite of `src/saidkick/cli.py`:

```python
import base64
import logging
import sys
from typing import List, Optional

import typer
import uvicorn
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

from saidkick.client import SaidkickClient

app = typer.Typer(help="Saidkick Dev Tool CLI")
console = Console(
    theme=Theme({
        "info": "cyan", "warning": "yellow", "error": "red", "success": "green",
        "status": "blue", "cmd": "magenta", "browser": "white",
    })
)
client = SaidkickClient()


def handle_client_error(e: Exception):
    import httpx
    if isinstance(e, httpx.ConnectError):
        console.print("[error]Error: Saidkick server is not running.[/error]")
    elif isinstance(e, httpx.HTTPStatusError):
        try:
            detail = e.response.json().get("detail", str(e))
        except Exception:
            detail = str(e)
        console.print(f"[error]Error: {detail}[/error]")
    else:
        console.print(f"[error]Error: {e}[/error]")
    raise typer.Exit(1)


def _locator_kwargs(
    css: Optional[str], xpath: Optional[str],
    by_text: Optional[str], by_label: Optional[str], by_placeholder: Optional[str],
    within_css: Optional[str], nth: Optional[int],
    exact: bool, regex: bool,
):
    return dict(
        css=css, xpath=xpath,
        by_text=by_text, by_label=by_label, by_placeholder=by_placeholder,
        within_css=within_css, nth=nth, exact=exact, regex=regex,
    )


@app.command()
def start(host: str = "0.0.0.0", port: int = 6992, reload: bool = False):
    """Start the Saidkick FastAPI server."""
    logging.basicConfig(
        level="INFO", format="%(message)s", datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=console)],
    )
    logging.getLogger("saidkick").setLevel(logging.INFO)
    uvicorn.run("saidkick.server:app", host=host, port=port, reload=reload, log_level="info")


@app.command()
def logs(
    limit: int = typer.Option(100, "--limit", "-n"),
    grep: str = typer.Option(None, "--grep", "-g"),
    browser: str = typer.Option(None, "--browser"),
):
    """Fetch console logs."""
    try:
        for log in client.get_logs(limit=limit, grep=grep, browser=browser):
            level = log.get("level", "info").upper()
            console.print(f"[browser]{log.get('browser_id','')} {level}: {log.get('data')}[/browser]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def tabs(active: bool = typer.Option(False, "--active")):
    """List tabs across connected browsers."""
    try:
        entries = client.list_tabs(active=active)
        if not entries:
            console.print("[warning]No tabs. Is a browser connected?[/warning]")
            return
        for e in entries:
            marker = "  [success](active)[/success]" if e.get("active") else ""
            console.print(f"[cmd]{e['tab']}[/cmd]  {e.get('url') or ''}  [info]\"{e.get('title') or ''}\"[/info]{marker}")
    except Exception as e:
        handle_client_error(e)


@app.command()
def find(
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Return JSON list of locator matches (debug aid)."""
    try:
        import json
        out = client.find(tab=tab, wait_ms=wait_ms,
                          **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                                            within_css, nth, exact, regex))
        sys.stdout.write(json.dumps(out, indent=2))
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


@app.command()
def dom(
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    all_matches: bool = typer.Option(False, "--all"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Get DOM of the matched element(s)."""
    try:
        out = client.get_dom(tab=tab, all_matches=all_matches, wait_ms=wait_ms,
                             **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                                               within_css, nth, exact, regex))
        sys.stdout.write(str(out))
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


@app.command()
def text(
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Print innerText of the tab or a located element."""
    try:
        out = client.text(tab=tab, wait_ms=wait_ms,
                          **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                                            within_css, nth, exact, regex))
        sys.stdout.write(str(out))
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


@app.command()
def click(
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Click a located element."""
    try:
        out = client.click(tab=tab, wait_ms=wait_ms,
                           **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                                             within_css, nth, exact, regex))
        console.print(f"[success]{out}[/success]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def type(
    text: str = typer.Argument(...),
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    clear: bool = typer.Option(False, "--clear"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Type into a located element (contenteditable-aware)."""
    try:
        out = client.type(tab=tab, text=text, clear=clear, wait_ms=wait_ms,
                          **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                                            within_css, nth, exact, regex))
        console.print(f"[success]{out}[/success]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def select(
    value: str = typer.Argument(...),
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Select an option in a <select>."""
    try:
        out = client.select(tab=tab, value=value, wait_ms=wait_ms,
                            **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                                              within_css, nth, exact, regex))
        console.print(f"[success]{out}[/success]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def press(
    key: str = typer.Argument(..., help="Key name (Enter, Escape, Tab, a, ArrowDown, ...)"),
    tab: str = typer.Option(..., "--tab"),
    mod: List[str] = typer.Option([], "--mod", help="Modifiers: ctrl, shift, alt, meta (comma-separated or repeated)"),
    css: str = typer.Option(None, "--css"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Press a key, optionally focusing a target first."""
    mods: List[str] = []
    for entry in mod:
        for part in entry.split(","):
            part = part.strip()
            if part:
                mods.append(part)
    try:
        out = client.press(
            tab=tab, key=key, modifiers=mods, wait_ms=wait_ms,
            **_locator_kwargs(css, None, by_text, by_label, by_placeholder,
                              within_css, nth, False, False),
        )
        console.print(f"[success]{out}[/success]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def screenshot(
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    full_page: bool = typer.Option(False, "--full-page"),
    output: str = typer.Option(None, "--output"),
):
    """Capture a PNG. Default: stdout raw bytes. --output to write to file."""
    try:
        result = client.screenshot(
            tab=tab, full_page=full_page,
            **_locator_kwargs(css, None, by_text, by_label, by_placeholder,
                              within_css, nth, False, False),
        )
        data = base64.b64decode(result["png_base64"])
        if output:
            with open(output, "wb") as f:
                f.write(data)
            console.print(f"[success]Wrote {len(data)} bytes to {output}[/success]")
        else:
            sys.stdout.buffer.write(data)
    except Exception as e:
        handle_client_error(e)


@app.command()
def navigate(
    url: str = typer.Argument(...),
    tab: str = typer.Option(..., "--tab"),
    wait: str = typer.Option("dom", "--wait"),
    timeout_ms: int = typer.Option(15000, "--timeout-ms"),
):
    """Navigate a tab to URL."""
    try:
        out = client.navigate(tab=tab, url=url, wait=wait, timeout_ms=timeout_ms)
        sys.stdout.write(out.get("url", "")); sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


@app.command("open")
def open_cmd(
    url: str = typer.Argument(...),
    browser: str = typer.Option(..., "--browser"),
    wait: str = typer.Option("dom", "--wait"),
    timeout_ms: int = typer.Option(15000, "--timeout-ms"),
    activate: bool = typer.Option(False, "--activate"),
):
    """Open URL in new tab."""
    try:
        out = client.open(browser=browser, url=url, wait=wait,
                          timeout_ms=timeout_ms, activate=activate)
        sys.stdout.write(out.get("tab", "")); sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


@app.command()
def exec(
    tab: str = typer.Option(..., "--tab"),
    code: Optional[str] = typer.Argument(None),
):
    """Execute JS in tab. Must 'return' a value to see it (0.4.0 breaking change)."""
    if code is None:
        if sys.stdin.isatty():
            console.print("[warning]Waiting for JS from stdin... (Ctrl+D to finish)[/warning]")
        code = sys.stdin.read()
    if not code or not code.strip():
        console.print("[error]Error: No code provided.[/error]")
        raise typer.Exit(1)
    try:
        result = client.execute(tab=tab, code=code)
        if isinstance(result, (dict, list)):
            import json
            sys.stdout.write(json.dumps(result))
        else:
            sys.stdout.write(str(result) if result is not None else "")
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


if __name__ == "__main__":
    app()
```

- [ ] **Step 2: Smoke CLI**

Run: `uv run saidkick --help`
Expected: lists `start, logs, tabs, find, dom, text, click, type, select, press, screenshot, navigate, open, exec`.

Run: `uv run saidkick find --help | grep by-text`
Expected: line mentioning `--by-text`.

- [ ] **Step 3: Commit**

```bash
git add src/saidkick/cli.py
git commit -m "feat(cli): find, press, screenshot commands; semantic locator flags everywhere"
```

---

## Task 12: Docs, CHANGELOG, version bump

**Files:**
- Modify: `pyproject.toml`, `CHANGELOG.md`, `docs/user-guide.md`, `docs/design.md`

- [ ] **Step 1: Bump version**

Edit `pyproject.toml`: `version = "0.3.0"` → `version = "0.4.0"`.

- [ ] **Step 2: CHANGELOG**

Prepend under `## [Unreleased]` in `CHANGELOG.md`:

```markdown
## [0.4.0] - 2026-04-21

### BREAKING CHANGES

- `exec` now wraps user code in `(async () => { ... })()` so scope doesn't leak between calls. **Callers must `return` their result**; a bare expression like `document.title` no longer becomes the response payload — use `return document.title`. Rationale: fixes the silent-collision footgun where `const x = 1` in one call caused the next to throw on redeclaration.

### Features

- Semantic locators — `--by-text`, `--by-label`, `--by-placeholder` on every selector-using command (`dom`, `text`, `click`, `type`, `select`, plus the new `find`, `press`, `screenshot`). `--within-css` scopes the search; `--nth N` disambiguates multi-matches; `--exact` and `--regex` adjust match semantics. Ambiguity without `--nth` returns 400.
- `GET /find` + `saidkick find --tab X --by-text ...` — debug tool that returns up to 50 matches as JSON with `selector`, `tag`, `role`, `name`, `text`, `rect`, `visible`.
- `POST /press` + `saidkick press KEY --tab X [--mod ctrl,shift] [--by-* ...]` — dispatches keyboard events via CDP `Input.dispatchKeyEvent`. Optional locator focuses the target first.
- `GET /screenshot` + `saidkick screenshot --tab X [--output PATH]` — captures a PNG via CDP `Page.captureScreenshot`. Optional locator clips to an element's bounding rect; `--full-page` captures beyond the viewport.
- `type` on `contenteditable` elements now uses `document.execCommand("insertText", ...)` — fixes WhatsApp, Slack, Discord, Gmail compose, GitHub comments, Notion, and every other Lexical / ProseMirror / Quill / Slate / Draft-backed rich-text field.

### Internal

- New `Locator` Pydantic mixin and `_validate_locator` / `_validate_required_locator` helpers. Every selector-using endpoint now validates "exactly one locator is set" at the boundary with a 400 on zero or multiple.
- Content-script `collectMatches` → `collectLocator`; single resolver backs CSS, XPath, text, label, and placeholder locators.
- `FOCUS` and `RESOLVE_RECT` content-script helpers so background-side `PRESS` / `SCREENSHOT` can target specific elements.

## [0.3.0] - 2026-04-21
```

- [ ] **Step 3: Extend `docs/user-guide.md`**

After the existing "Driving a flow from scratch" section, append:

```markdown
## Finding elements by intent

Real apps ship class-obfuscated DOMs. The semantic locators let you target by what the user sees rather than what the framework compiled:

- `--by-text "Send"` — substring (case-insensitive) match on the element's text.
- `--by-label "Type a message"` — matches `aria-label`, `aria-labelledby` target, or `<label for>`.
- `--by-placeholder "Search..."` — matches the HTML `placeholder` attribute.

Scope a search with `--within-css ".modal"`; disambiguate a multi-match with `--nth 2`; tighten a match with `--exact` or `--regex`.

Exactly one of `--css` / `--xpath` / `--by-text` / `--by-label` / `--by-placeholder` must be set on any selector-using command.

### Example: driving WhatsApp Web without exec

```bash
TAB=br-XXXX:N
saidkick click  --tab "$TAB" --by-text "Leydis CIMEX"
saidkick type   "Hola Leydis" --tab "$TAB" --by-label "Type a message"
saidkick press  Enter --tab "$TAB"
saidkick screenshot --tab "$TAB" --output /tmp/confirm.png
```
```

Append command sections:

```markdown
### `saidkick find`
Debug aid: return JSON list of matches for a locator.
- `--tab` (required); exactly one locator (`--by-text` / `--css` / etc.); optional `--within-css`, `--nth`, `--exact`, `--regex`, `--wait-ms`.

### `saidkick press`
Dispatch a keyboard event via CDP.
- `KEY` (argument): `Enter`, `Escape`, `Tab`, `ArrowDown`, single char (`a`), etc.
- `--tab` (required).
- `--mod ctrl,shift` (or repeated): modifiers.
- Optional locator flags: focuses the matched element before dispatching.
- `--wait-ms`: poll for the focus target.

### `saidkick screenshot`
Capture a PNG.
- `--tab` (required).
- Optional locator flags: clip to the matched element's bounding rect.
- `--full-page`: capture beyond the viewport.
- `--output PATH`: write to a file (overwrites silently); default is raw bytes to stdout.
```

Update the REST API table:

```markdown
| `/find` | `GET` | Locator matches as JSON (debug). Query: `tab`, locator fields, `wait_ms`. |
| `/press` | `POST` | Keyboard event. Body: `{"tab": ..., "key": ..., "modifiers": [...], locator fields, "wait_ms": N}`. |
| `/screenshot` | `GET` | PNG capture. Query: `tab`, locator fields, `full_page`. Returns `{"png_base64": "...", "width": N, "height": N}`. |
```

- [ ] **Step 4: Extend `docs/design.md`**

Append after the existing error-policy section:

```markdown
### Semantic locators

`Locator` is a Pydantic mixin with fields `css`, `xpath`, `by_text`, `by_label`, `by_placeholder`, `within_css`, `nth`, `exact`, `regex`. `_validate_locator` enforces the regex/exact mutex; `_validate_required_locator` additionally enforces the exactly-one-of rule.

Content-script resolution follows one path:
1. Scope root = `within_css ? document.querySelector(within_css) : document`.
2. If `css`: `root.querySelectorAll(css)`.
3. Else if `xpath`: `document.evaluate(xpath, root, ...)`.
4. Else: scan `root.querySelectorAll("*")` with the text/label/placeholder predicate (substring-ci by default; `exact` or `regex` adjust).
5. `nth` picks one; absence + multi-match raises `Ambiguous locator`.

### Keyboard events

`PRESS` attaches the debugger (shared with `EXECUTE`), optionally asks the content script to `FOCUS` a locator target, then issues `Input.dispatchKeyEvent` (keyDown → optional char → keyUp). Framework listeners see real native-origin events.

### Screenshots

`SCREENSHOT` attaches the debugger, optionally asks the content script to `RESOLVE_RECT` on a locator to get a clip rectangle, then calls `Page.captureScreenshot` with `{format: "png", clip?, captureBeyondViewport: full_page}`. Base64-encoded PNG returned to the server.

### Exec scope isolation

`EXECUTE` wraps the user's `payload.code` in `(async () => { ... })()` before passing to `Runtime.evaluate` with `awaitPromise: true`. Each invocation gets a fresh async-function scope; `const`/`let` declarations no longer collide across calls. Top-level `await` works. Callers must `return` explicitly.
```

- [ ] **Step 5: Run full suite once more**

Run: `uv run pytest tests/ --ignore=tests/test_saidkick_e2e.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml CHANGELOG.md docs/user-guide.md docs/design.md
git commit -m "docs: locators, find, press, screenshot, exec IIFE; bump to 0.4.0"
```

---

## Task 13: Manual end-to-end smoke test

No automation. Each step verify-and-confirm.

- [ ] **Step 1: Reload extension**

`chrome://extensions/` → reload Saidkick.

- [ ] **Step 2: Start the server**

`uv run saidkick start`
Expected: `Browser connected: br-XXXX` within seconds.

- [ ] **Step 3: `find` sanity**

```bash
TAB=br-XXXX:N  # from `saidkick tabs`
uv run saidkick find --tab "$TAB" --by-text "a"
```

Expected: JSON array of matching elements; each entry has `selector`, `tag`, `name`, `rect`, `visible`.

- [ ] **Step 4: `press` sanity**

In an active Google search tab: type something, then:

```bash
uv run saidkick press Enter --tab "$TAB"
```

Expected: search triggers. (If no focus, the keydown still dispatches to document.activeElement.)

- [ ] **Step 5: `screenshot` sanity**

```bash
uv run saidkick screenshot --tab "$TAB" --output /tmp/shot.png
file /tmp/shot.png
```

Expected: `PNG image data, ..., 8-bit/color RGBA, non-interlaced`.

- [ ] **Step 6: WhatsApp send without exec (the real test)**

```bash
TAB=br-XXXX:N  # a WhatsApp tab
uv run saidkick click --tab "$TAB" --by-text "Leydis CIMEX"
uv run saidkick type "test from 0.4.0" --tab "$TAB" --by-label "Type a message"
uv run saidkick press Enter --tab "$TAB"
uv run saidkick screenshot --tab "$TAB" --output /tmp/sent.png
```

Expected: message lands in Leydis's chat; screenshot captures the new bubble. Zero `exec` calls needed.

- [ ] **Step 7: `exec` IIFE smoke**

```bash
echo "return document.title" | uv run saidkick exec --tab "$TAB"
echo "return document.title" | uv run saidkick exec --tab "$TAB"   # second call doesn't error
```

Expected: both calls print the page title. No `SyntaxError: Identifier 'x' has already been declared` between calls even if the user code declares `const x = 1`.

- [ ] **Step 8: Journal + workspace repo node sync**

Append to today's journal (`vault/Calendar/Journal/journal-2026-04-21.md`):

```
> 🤖 HH:MM — milestone: saidkick 0.4.0 landed (find/press/screenshot + rich-type + exec IIFE). Manual smoke passed.
```

Back in `/home/apiad/Workspace`:
- Update `vault/Efforts/Repos/saidkick.md`: mark Tier-2 `find`, `press`, `screenshot`, and the rich-type / exec-IIFE fixes as done in 0.4.0; bump `last_sync`; add a note that `by_role` + AXTree dump remain for 0.5.0.
- Commit: `chore(repos): sync saidkick after 0.4.0 landed`.

---

## Self-Review

**1. Spec coverage.**
- `Locator` mixin + validators → Task 1.
- Plumbing into existing endpoints (dom/text/click/type/select) → Task 2.
- `/find` endpoint → Task 3.
- `/press` endpoint → Task 4.
- `/screenshot` endpoint → Task 5.
- Content-script locator resolver + `FIND` + `RESOLVE_RECT` + `FOCUS` + rich-type → Task 6 (+ Task 7 covers the FOCUS addition called out in-line).
- Background `PRESS` → Task 7.
- Background `SCREENSHOT` → Task 8.
- `EXECUTE` IIFE-wrap → Task 9.
- Python client → Task 10.
- CLI → Task 11.
- Docs + CHANGELOG + version bump → Task 12.
- Manual smoke → Task 13.

**2. Placeholder scan.** No "TBD" / "add error handling" / vague descriptions. Every code step shows the code; every test shows its code.

**3. Type consistency.** `Locator` mixin fields used identically across tasks. `_locator_payload(loc)` helper introduced in Task 1, reused in Tasks 2, 3, 4, 5. Payload shape consistent: `{tab_id, wait_ms, …locator fields}`. Extension message types (`FIND`, `PRESS`, `SCREENSHOT`, `FOCUS`, `RESOLVE_RECT`) named consistently between background and content.

**4. Ambiguity check.**
- `text` allows zero-locator (returns `document.body.innerText`) — explicitly uses `_validate_locator` (optional) rather than `_validate_required_locator`. Same for `screenshot` and `press`.
- `find` requires a locator — uses `_validate_required_locator`.
- `dom` requires a locator via Task 2's endpoint body (the prior "no selector = documentElement" fallback from 0.3.0 remains in content.js when all locator fields are empty, but the server-side validator rejects the empty case first). Call out: **`dom`'s content-script fallback is unreachable in 0.4.0 because the server validator requires a locator**; retaining the branch is harmless but the server is authoritative.

Fix: the `test_dom_with_no_selector` behavior from 0.3.0 (returning `document.documentElement.outerHTML`) is no longer supported at the endpoint level. Grep existing tests for this case — none assert it; the prior 0.3.0 test suite only tested `/dom` with a selector present. Safe.
