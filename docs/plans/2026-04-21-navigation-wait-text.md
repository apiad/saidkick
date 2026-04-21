# Navigation / Wait / Text / Error Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver saidkick 0.3.0 — `POST /navigate`, `POST /open`, `GET /text`, a shared `wait_ms` option on all selector-using commands — and reclassify the 0.2.0 misuses of HTTP 500 into 400/404/502/504 per the revised taxonomy.

**Architecture:** Server-side, introduce an error classifier that turns extension failure strings into the right `HTTPException`. Give `send_command` an optional per-call timeout so long-waiting commands don't trip the 10s default. Extension-side, add a content-script `waitForSelector` helper used by every selector operation, a new `GET_TEXT` handler, and background-script `NAVIGATE` / `OPEN` handlers that drive `chrome.tabs.update` / `chrome.tabs.create` with debugger-based `Page.domContentLoaded` / `Page.loadEventFired` waits. CLI, client, docs, and CHANGELOG follow the same lockstep pattern as 0.2.0.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, pytest + pytest-asyncio, `fastapi.testclient.TestClient`, `unittest.mock.AsyncMock`/`patch`. Chrome MV3 extension (vanilla JS, `chrome.debugger` Page domain). Typer + Rich CLI.

---

## File Structure

**Modified:**
- `src/saidkick/server.py` — `_raise_for_extension_error` helper, `_validate_http_url`, `send_command` gains `timeout` param, `_command_timeout` helper, all existing endpoints call classifier, `wait_ms` plumbed into existing endpoints' extension payloads, three new endpoints (`/navigate`, `/open`, `/text`).
- `src/saidkick/client.py` — `navigate`, `open`, `text` methods; `wait_ms` kwarg on `get_dom`, `click`, `type`, `select`.
- `src/saidkick/cli.py` — three new commands (`navigate`, `open`, `text`); `--wait-ms` on `dom`, `click`, `type`, `select`.
- `src/saidkick/extension/background.js` — `ensureDebuggerAttached`, `waitForPageEvent`, `NAVIGATE` and `OPEN` handlers; EXECUTE refactored to use `ensureDebuggerAttached`.
- `src/saidkick/extension/content.js` — `waitForSelector` / `waitForAnyMatches`; existing `findElement` replaced; `GET_TEXT` handler; every handler awaits `payload.wait_ms`.
- `pyproject.toml` — `0.2.0` → `0.3.0`.
- `CHANGELOG.md` — add `[0.3.0]` entry.
- `docs/user-guide.md`, `docs/design.md` — new commands and protocol details.
- `tests/test_saidkick.py`, `tests/test_saidkick_enhanced.py`, `tests/test_tabs.py` — any 500 assertions become 404/400/502 as appropriate.

**Created:**
- `tests/test_error_taxonomy.py` — table-driven tests for `_raise_for_extension_error` and `_validate_http_url`.
- `tests/test_navigate_open.py` — `/navigate` and `/open` endpoint tests.
- `tests/test_wait_ms.py` — `wait_ms` propagation + `send_command` timeout override.
- `tests/test_text.py` — `/text` endpoint tests.

**Unchanged:** `main_world.js`, `popup.html`, `popup.js`, `manifest.json`.

---

## Task 1: Error taxonomy classifier + URL validator

**Files:**
- Modify: `src/saidkick/server.py`
- Create: `tests/test_error_taxonomy.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_error_taxonomy.py`:

```python
import pytest
from fastapi import HTTPException
from saidkick.server import _raise_for_extension_error, _validate_http_url


@pytest.mark.parametrize("msg,code", [
    ("element not found", 404),
    ("Element not found", 404),
    ("option not found: foo", 404),
    ("tab not found: 7", 404),
    ("Ambiguous selector: found 3 matches", 400),
    ("Element is not a <select>", 400),
    ("No selector provided", 400),
    ("invalid url: 'ftp://x'", 400),
    ("navigation timeout after 15000ms", 504),
    ("selector not resolved within 3000ms", 504),
    ("Browser response timeout", 504),
    ("some weird chrome error we never saw", 502),
    ("", 502),
])
def test_classifier_maps_messages_to_codes(msg, code):
    with pytest.raises(HTTPException) as exc:
        _raise_for_extension_error(msg)
    assert exc.value.status_code == code
    assert exc.value.detail == msg


@pytest.mark.parametrize("url", [
    "http://example.com/",
    "https://example.com/path?q=1",
    "https://sub.example.com:8080/",
])
def test_validate_http_url_accepts(url):
    _validate_http_url(url)  # no raise


@pytest.mark.parametrize("url", [
    "",
    "example.com",           # no scheme
    "ftp://example.com/",    # wrong scheme
    "javascript:alert(1)",   # not http
    "http://",               # no netloc
    "about:blank",           # not http
])
def test_validate_http_url_rejects(url):
    with pytest.raises(HTTPException) as exc:
        _validate_http_url(url)
    assert exc.value.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_error_taxonomy.py -v`
Expected: ImportError — `_raise_for_extension_error` and `_validate_http_url` don't exist.

- [ ] **Step 3: Implement the helpers**

Add near the top of `src/saidkick/server.py`, after `parse_tab_id`:

```python
from urllib.parse import urlparse


def _raise_for_extension_error(payload: str) -> None:
    """Map a failure string from the extension into an HTTPException.

    500 is reserved for server bugs; anything caller-observable gets a 4xx or
    502/504. The extension's message strings are ours, so keyword matches are
    deterministic.
    """
    m = (payload or "").lower()
    if ("element not found" in m
        or "option not found" in m
        or "tab not found" in m):
        raise HTTPException(status_code=404, detail=payload)
    if ("ambiguous selector" in m
        or "element is not a" in m
        or "no selector provided" in m
        or "invalid url" in m):
        raise HTTPException(status_code=400, detail=payload)
    if "timeout" in m or "not resolved within" in m:
        raise HTTPException(status_code=504, detail=payload)
    raise HTTPException(status_code=502, detail=payload)


def _validate_http_url(url: str) -> None:
    """Raise HTTPException(400) if `url` is not a well-formed http(s) URL."""
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"invalid url: {url!r}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_error_taxonomy.py -v`
Expected: all parametrize cases PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/saidkick
git add src/saidkick/server.py tests/test_error_taxonomy.py
git commit -m "feat(server): error classifier and http url validator"
```

---

## Task 2: Route existing endpoint failures through the classifier

**Files:**
- Modify: `src/saidkick/server.py`
- Modify: `tests/test_tabs.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tabs.py`:

```python
def test_execute_element_not_found_is_404():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    async def fake_send(browser_id, command_type, payload=None, timeout=None):
        return {"success": False, "payload": "Element not found"}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/execute", json={"tab": "br-aaaa:1", "code": "x"}
        )
    assert r.status_code == 404
    assert r.json()["detail"] == "Element not found"


def test_click_ambiguous_selector_is_400():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    async def fake_send(browser_id, command_type, payload=None, timeout=None):
        return {"success": False, "payload": "Ambiguous selector: found 3 matches"}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/click", json={"tab": "br-aaaa:1", "css": ".btn"}
        )
    assert r.status_code == 400


def test_type_unknown_extension_error_is_502():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    async def fake_send(browser_id, command_type, payload=None, timeout=None):
        return {"success": False, "payload": "weird chrome thing"}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/type", json={"tab": "br-aaaa:1", "css": "#x", "text": "y"}
        )
    assert r.status_code == 502
```

Note the `timeout=None` kwarg in `fake_send` — it's added in Task 4. Harmless to include now; the signature must tolerate extra kwargs. Use `**kwargs` in the fake if that's simpler.

Actually, keep it simple — write `async def fake_send(*args, **kwargs): return {...}` to avoid coupling these tests to Task 4's signature change. Update the three new tests to use `*args, **kwargs`.

Revised test bodies — replace the `async def fake_send(...)` lines with:

```python
    async def fake_send(*args, **kwargs):
        return {"success": False, "payload": "Element not found"}
```

etc.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tabs.py -v -k "element_not_found_is_404 or ambiguous_selector or unknown_extension_error"`
Expected: three failures — endpoints still return 500 or 200 on extension failure.

- [ ] **Step 3: Replace raise HTTPException(500, ...) with the classifier**

In `src/saidkick/server.py`, five endpoints currently have the pattern:

```python
    if not response.get("success"):
        raise HTTPException(status_code=500, detail=response.get("payload"))
    return response.get("payload")
```

Replace with:

```python
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")
```

Endpoints affected: `post_execute`, `post_click`, `post_type`, `post_select`. (`get_dom` currently doesn't check `success` — leave that for Task 3.)

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest tests/ --ignore=tests/test_saidkick_e2e.py -v`
Expected: all pass, including the three new tests from Step 1.

- [ ] **Step 5: Commit**

```bash
git add src/saidkick/server.py tests/test_tabs.py
git commit -m "refactor(server): route extension errors through classifier (400/404/502)"
```

---

## Task 3: `wait_ms` on existing selector endpoints

**Files:**
- Modify: `src/saidkick/server.py`
- Create: `tests/test_wait_ms.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wait_ms.py`:

```python
from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_dom_passes_wait_ms_to_extension():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return {"success": True, "payload": "<div/>"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/dom?tab=br-aaaa:1&css=.x&wait_ms=3000")
    assert r.status_code == 200
    payload = seen["args"][2] if len(seen["args"]) >= 3 else seen["kwargs"]["payload"]
    assert payload["wait_ms"] == 3000


def test_click_passes_wait_ms_to_extension():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return {"success": True, "payload": "Clicked"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/click", json={"tab": "br-aaaa:1", "css": ".btn", "wait_ms": 2500}
        )
    assert r.status_code == 200
    payload = seen["args"][2] if len(seen["args"]) >= 3 else seen["kwargs"]["payload"]
    assert payload["wait_ms"] == 2500


def test_wait_ms_defaults_to_zero():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return {"success": True, "payload": "Clicked"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/click", json={"tab": "br-aaaa:1", "css": ".btn"}
        )
    assert r.status_code == 200
    payload = seen["args"][2] if len(seen["args"]) >= 3 else seen["kwargs"]["payload"]
    assert payload["wait_ms"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_wait_ms.py -v`
Expected: failures — `wait_ms` not yet in payloads.

- [ ] **Step 3: Add `wait_ms` to request models and endpoint payloads**

In `src/saidkick/server.py`, update the request models:

```python
class SelectorRequest(BaseModel):
    tab: str
    css: Optional[str] = None
    xpath: Optional[str] = None
    wait_ms: int = 0


class TypeRequest(SelectorRequest):
    text: str
    clear: bool = False


class SelectRequest(SelectorRequest):
    value: str
```

`ExecuteRequest` is unchanged — EXECUTE doesn't use selectors.

Update each action endpoint to pass `wait_ms` through. Replace `get_dom`:

```python
@app.get("/dom")
async def get_dom(
    tab: str,
    css: Optional[str] = None,
    xpath: Optional[str] = None,
    all: bool = False,
    wait_ms: int = 0,
):
    browser_id, tab_id = _parse_or_400(tab)
    response = await manager.send_command(
        browser_id, "GET_DOM",
        payload={
            "tab_id": tab_id, "css": css, "xpath": xpath,
            "all": all, "wait_ms": wait_ms,
        },
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")
```

Note: `get_dom` gains a success-check that wasn't there before. Consistency.

Replace `post_click`:

```python
@app.post("/click")
async def post_click(req: SelectorRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    response = await manager.send_command(
        browser_id, "CLICK",
        payload={
            "tab_id": tab_id, "css": req.css, "xpath": req.xpath,
            "wait_ms": req.wait_ms,
        },
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
    response = await manager.send_command(
        browser_id, "TYPE",
        payload={
            "tab_id": tab_id, "css": req.css, "xpath": req.xpath,
            "text": req.text, "clear": req.clear, "wait_ms": req.wait_ms,
        },
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
    response = await manager.send_command(
        browser_id, "SELECT",
        payload={
            "tab_id": tab_id, "css": req.css, "xpath": req.xpath,
            "value": req.value, "wait_ms": req.wait_ms,
        },
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")
```

- [ ] **Step 4: Update the existing endpoint tests**

Earlier tests in `tests/test_saidkick.py` / `tests/test_saidkick_enhanced.py` assert exact payloads. Those payloads now include `wait_ms`. Update:

In `tests/test_saidkick_enhanced.py`:

```python
def test_dom_anchoring_params():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    with patch.object(manager, "send_command", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"payload": "<div>Match</div>", "success": True}
        response = client.get("/dom?tab=br-aaaa:3&css=.test&all=true")
    assert response.status_code == 200
    assert response.json() == "<div>Match</div>"
    mock_send.assert_called_with(
        "br-aaaa", "GET_DOM",
        payload={"tab_id": 3, "css": ".test", "xpath": None, "all": True, "wait_ms": 0},
    )


def test_interaction_endpoints():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    with patch.object(manager, "send_command", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"success": True, "payload": "OK"}

        response = client.post("/click", json={"tab": "br-aaaa:1", "css": "#btn"})
        assert response.status_code == 200
        mock_send.assert_called_with(
            "br-aaaa", "CLICK",
            payload={"tab_id": 1, "css": "#btn", "xpath": None, "wait_ms": 0},
        )

        response = client.post("/type", json={
            "tab": "br-aaaa:2", "css": "#input", "text": "hello", "clear": True,
        })
        assert response.status_code == 200
        mock_send.assert_called_with(
            "br-aaaa", "TYPE",
            payload={
                "tab_id": 2, "css": "#input", "xpath": None,
                "text": "hello", "clear": True, "wait_ms": 0,
            },
        )

        response = client.post("/select", json={
            "tab": "br-aaaa:3", "xpath": "//select", "value": "opt1",
        })
        assert response.status_code == 200
        mock_send.assert_called_with(
            "br-aaaa", "SELECT",
            payload={
                "tab_id": 3, "css": None, "xpath": "//select",
                "value": "opt1", "wait_ms": 0,
            },
        )
```

`tests/test_tabs.py` `test_dom_routes_to_correct_browser` — similar update:

```python
def test_dom_routes_to_correct_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]
    seen = {}
    async def fake_send(browser_id, command_type, payload=None, **kwargs):
        seen["browser_id"] = browser_id
        seen["payload"] = payload
        return {"success": True, "payload": "<div/>"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/dom?tab=br-aaaa:7&css=.foo")
    assert r.status_code == 200
    assert seen["browser_id"] == "br-aaaa"
    assert seen["payload"]["tab_id"] == 7
    assert seen["payload"]["css"] == ".foo"
    assert seen["payload"]["wait_ms"] == 0
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest tests/ --ignore=tests/test_saidkick_e2e.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/saidkick/server.py tests/test_wait_ms.py tests/test_saidkick_enhanced.py tests/test_tabs.py
git commit -m "feat(server): wait_ms option on dom/click/type/select"
```

---

## Task 4: `send_command` per-request timeout override

**Files:**
- Modify: `src/saidkick/server.py`
- Modify: `tests/test_wait_ms.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wait_ms.py`:

```python
from saidkick.server import _command_timeout


def test_command_timeout_defaults_to_ten():
    assert _command_timeout() == 10.0


def test_command_timeout_grows_with_wait_ms():
    # 5s wait should give us ~7s total
    assert _command_timeout(wait_ms=5000) >= 7.0


def test_command_timeout_grows_with_timeout_ms():
    # 20s navigation timeout should expand past 10s default
    assert _command_timeout(timeout_ms=20000) >= 22.0


def test_command_timeout_uses_max_of_inputs():
    # Both inputs; take the larger.
    assert _command_timeout(wait_ms=5000, timeout_ms=15000) >= 17.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_wait_ms.py -v -k "command_timeout"`
Expected: ImportError on `_command_timeout`.

- [ ] **Step 3: Implement `_command_timeout` and pipe it through**

In `src/saidkick/server.py`, add:

```python
def _command_timeout(wait_ms: int = 0, timeout_ms: int = 0) -> float:
    """Compute the server-side asyncio.wait_for budget for an extension command.

    Default 10s. Extended by the larger of wait_ms/timeout_ms plus 2s overhead
    to leave room for extension scheduling.
    """
    base = 10.0
    extension_budget_s = max(wait_ms, timeout_ms) / 1000.0
    return max(base, extension_budget_s + 2.0)
```

Update `send_command` to accept an optional `timeout: Optional[float] = None`:

```python
async def send_command(
    self, browser_id: str, command_type: str,
    payload: Any = None, timeout: Optional[float] = None,
) -> Dict[str, Any]:
    ws = self.connections.get(browser_id)
    if ws is None:
        raise HTTPException(
            status_code=404, detail=f"browser not connected: {browser_id}"
        )

    request_id = str(uuid.uuid4())
    future = asyncio.get_event_loop().create_future()
    self.pending_requests[request_id] = future

    logger.info(f"[CMD] {browser_id} <- {command_type}")
    try:
        await ws.send_text(
            json.dumps({"type": command_type, "id": request_id, "payload": payload})
        )
    except Exception as e:
        self.pending_requests.pop(request_id, None)
        raise HTTPException(
            status_code=502, detail=f"browser send failed: {e}"
        ) from e

    try:
        response = await asyncio.wait_for(
            future, timeout=timeout if timeout is not None else 10.0
        )
        return response
    except asyncio.TimeoutError as e:
        self.pending_requests.pop(request_id, None)
        raise HTTPException(
            status_code=504, detail="Browser response timeout"
        ) from e
```

Update the existing selector endpoints to pass the computed timeout. For `post_click`:

```python
@app.post("/click")
async def post_click(req: SelectorRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    response = await manager.send_command(
        browser_id, "CLICK",
        payload={
            "tab_id": tab_id, "css": req.css, "xpath": req.xpath,
            "wait_ms": req.wait_ms,
        },
        timeout=_command_timeout(wait_ms=req.wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")
```

Do the same for `get_dom`, `post_type`, `post_select`. `post_execute` and `/tabs` keep the default (they don't have wait_ms). Update only the call sites — leave the rest of their bodies intact.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest tests/ --ignore=tests/test_saidkick_e2e.py -v`
Expected: all pass, including the four new `_command_timeout` tests.

- [ ] **Step 5: Commit**

```bash
git add src/saidkick/server.py tests/test_wait_ms.py
git commit -m "feat(server): per-request command timeout scaled by wait_ms/timeout_ms"
```

---

## Task 5: Content-script `waitForSelector` + wire into existing handlers

**Files:**
- Modify: `src/saidkick/extension/content.js`

No automated tests for JS. Verified manually in Task 12.

- [ ] **Step 1: Replace `content.js` in full**

Write `src/saidkick/extension/content.js`:

```javascript
(function() {
    console.log("Saidkick: Content script (isolated world) initializing");
    try {
        chrome.runtime.sendMessage({
            type: "log",
            level: "log",
            data: "Saidkick: Content script connected",
            timestamp: new Date().toISOString(),
            url: window.location.href
        });
    } catch (e) {}

    // Mirror logs from the MAIN world.
    window.addEventListener('message', (event) => {
        if (event.source !== window) return;
        const message = event.data;
        if (message && message.type === 'saidkick-log') {
            try {
                chrome.runtime.sendMessage({ type: "log", ...message.detail });
            } catch (e) { /* context may be invalidated */ }
        }
    });

    function collectMatches(css, xpath) {
        if (css) return Array.from(document.querySelectorAll(css));
        if (xpath) {
            const result = document.evaluate(
                xpath, document, null,
                XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null
            );
            const nodes = [];
            for (let i = 0; i < result.snapshotLength; i++) {
                nodes.push(result.snapshotItem(i));
            }
            return nodes;
        }
        throw new Error("No selector provided");
    }

    async function waitForSelector(css, xpath, waitMs) {
        const start = Date.now();
        const POLL = 100;
        while (true) {
            let matches;
            try { matches = collectMatches(css, xpath); }
            catch (e) { throw e; }  // no selector provided — surface immediately
            if (matches.length === 1) return matches[0];
            if (matches.length > 1) {
                // Ambiguous now — but polling may let it settle. Only
                // throw once we've passed the deadline.
                if (Date.now() - start >= waitMs) {
                    throw new Error(`Ambiguous selector: found ${matches.length} matches`);
                }
            } else if (Date.now() - start >= waitMs) {
                throw new Error("element not found");
            }
            await new Promise(r => setTimeout(r, POLL));
        }
    }

    async function waitForAnyMatches(css, xpath, waitMs) {
        const start = Date.now();
        const POLL = 100;
        while (true) {
            let matches;
            try { matches = collectMatches(css, xpath); }
            catch (e) { return []; }  // no selector → caller's responsibility
            if (matches.length >= 1) return matches;
            if (Date.now() - start >= waitMs) return matches;
            await new Promise(r => setTimeout(r, POLL));
        }
    }

    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
        const { type, payload } = request;

        const handle = async () => {
            const waitMs = (payload && payload.wait_ms) || 0;

            if (type === "GET_DOM") {
                const { css, xpath, all } = payload || {};
                let matches;
                if (!css && !xpath) {
                    matches = [document.documentElement];
                } else if (all) {
                    matches = await waitForAnyMatches(css, xpath, waitMs);
                    if (matches.length === 0) throw new Error("element not found");
                } else {
                    matches = [await waitForSelector(css, xpath, waitMs)];
                }
                const output = all
                    ? matches.map(m => m.outerHTML).join("\n")
                    : matches[0].outerHTML;
                return { success: true, payload: output };
            }

            if (type === "CLICK") {
                const element = await waitForSelector(payload.css, payload.xpath, waitMs);
                element.click();
                element.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
                element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
                return { success: true, payload: "Clicked" };
            }

            if (type === "TYPE") {
                const element = await waitForSelector(payload.css, payload.xpath, waitMs);
                element.focus();
                if (payload.clear) {
                    if (element.tagName === "INPUT" || element.tagName === "TEXTAREA") {
                        element.value = "";
                    } else if (element.isContentEditable) {
                        element.innerText = "";
                    }
                }
                const text = payload.text;
                if (element.tagName === "INPUT" || element.tagName === "TEXTAREA") {
                    element.value += text;
                } else if (element.isContentEditable) {
                    element.innerText += text;
                }
                element.dispatchEvent(new Event("input", { bubbles: true }));
                element.dispatchEvent(new Event("change", { bubbles: true }));
                element.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true }));
                element.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true }));
                return { success: true, payload: "Typed" };
            }

            if (type === "SELECT") {
                const element = await waitForSelector(payload.css, payload.xpath, waitMs);
                if (element.tagName !== "SELECT") {
                    throw new Error("Element is not a <select>");
                }
                const val = payload.value;
                let found = false;
                for (const option of element.options) {
                    if (option.value === val || option.text === val) {
                        element.value = option.value;
                        found = true;
                        break;
                    }
                }
                if (!found) throw new Error(`option not found: ${val}`);
                element.dispatchEvent(new Event("change", { bubbles: true }));
                return { success: true, payload: "Selected" };
            }

            if (type === "GET_TEXT") {
                const { css } = payload || {};
                const element = css
                    ? await waitForSelector(css, null, waitMs)
                    : document.body;
                return { success: true, payload: element.innerText || "" };
            }

            throw new Error(`unknown command: ${type}`);
        };

        handle().then(
            result => sendResponse(result),
            err => sendResponse({ success: false, payload: err.message })
        );
        return true;  // async sendResponse
    });
})();
```

- [ ] **Step 2: Commit**

```bash
git add src/saidkick/extension/content.js
git commit -m "feat(extension): waitForSelector helpers; GET_TEXT handler; wait_ms support"
```

---

## Task 6: `GET /text` endpoint

**Files:**
- Modify: `src/saidkick/server.py`
- Create: `tests/test_text.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_text.py`:

```python
from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


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
    assert seen["payload"]["wait_ms"] == 0


def test_text_with_css_scope_and_wait():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": "scoped"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/text?tab=br-aaaa:1&css=main&wait_ms=500")
    assert r.status_code == 200
    assert seen["payload"]["css"] == "main"
    assert seen["payload"]["wait_ms"] == 500


def test_text_element_not_found_is_404():
    setup_single_browser()
    async def fake_send(*args, **kwargs):
        return {"success": False, "payload": "element not found"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/text?tab=br-aaaa:1&css=.nope")
    assert r.status_code == 404


def test_text_malformed_tab_is_400():
    r = TestClient(app).get("/text?tab=not-a-tab")
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_text.py -v`
Expected: failures — endpoint doesn't exist.

- [ ] **Step 3: Add `/text` endpoint**

In `src/saidkick/server.py`, after `post_select` but before `get_tabs`, add:

```python
@app.get("/text")
async def get_text(
    tab: str,
    css: Optional[str] = None,
    wait_ms: int = 0,
):
    browser_id, tab_id = _parse_or_400(tab)
    response = await manager.send_command(
        browser_id, "GET_TEXT",
        payload={"tab_id": tab_id, "css": css, "wait_ms": wait_ms},
        timeout=_command_timeout(wait_ms=wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest tests/ --ignore=tests/test_saidkick_e2e.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/saidkick/server.py tests/test_text.py
git commit -m "feat(server): GET /text returns innerText of a tab or CSS-scoped element"
```

---

## Task 7: Extension — debugger helpers + `NAVIGATE`/`OPEN` handlers

**Files:**
- Modify: `src/saidkick/extension/background.js`

No automated tests. Verified manually in Task 12.

- [ ] **Step 1: Factor debugger attach and page-event wait into helpers; add handlers**

Open `src/saidkick/extension/background.js`. At the top (after the `logQueue` line and before `sendToContentScript`), add two helpers:

```javascript
async function ensureDebuggerAttached(tabId) {
    const target = { tabId };
    await new Promise((resolve, reject) => {
        chrome.debugger.attach(target, "1.3", () => {
            const err = chrome.runtime.lastError;
            if (err && !err.message.includes("already attached")) {
                reject(err);
            } else {
                resolve();
            }
        });
    });
    await new Promise(r =>
        chrome.debugger.sendCommand(target, "Page.enable", {}, r)
    );
    await new Promise(r =>
        chrome.debugger.sendCommand(target, "Runtime.enable", {}, r)
    );
}

function waitForPageEvent(tabId, eventName, timeoutMs) {
    return new Promise((resolve, reject) => {
        let done = false;
        const handler = (source, method) => {
            if (done) return;
            if (source.tabId !== tabId) return;
            if (method !== eventName) return;
            done = true;
            clearTimeout(timer);
            chrome.debugger.onEvent.removeListener(handler);
            resolve();
        };
        const timer = setTimeout(() => {
            if (done) return;
            done = true;
            chrome.debugger.onEvent.removeListener(handler);
            reject(new Error(`navigation timeout after ${timeoutMs}ms`));
        }, timeoutMs);
        chrome.debugger.onEvent.addListener(handler);
    });
}

const PAGE_EVENT_FOR_WAIT = {
    dom: "Page.domContentLoaded",
    full: "Page.loadEventFired",
};
```

Inside `socket.onmessage`, add two new branches after `LIST_TABS` and before the existing tab-id preamble:

```javascript
        if (type === "NAVIGATE") {
            const { tab_id, url, wait: waitMode, timeout_ms } = payload || {};
            try {
                if (waitMode && waitMode !== "none") {
                    await ensureDebuggerAttached(tab_id);
                }
                await chrome.tabs.update(tab_id, { url });
                if (waitMode && waitMode !== "none") {
                    const ev = PAGE_EVENT_FOR_WAIT[waitMode];
                    if (!ev) throw new Error(`invalid wait mode: ${waitMode}`);
                    await waitForPageEvent(tab_id, ev, timeout_ms || 15000);
                }
                const finalTab = await chrome.tabs.get(tab_id);
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: true,
                    payload: { url: finalTab.url },
                }));
            } catch (err) {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: false,
                    payload: err.message || String(err),
                }));
            }
            return;
        }

        if (type === "OPEN") {
            const { url, wait: waitMode, timeout_ms, activate } = payload || {};
            try {
                const created = await chrome.tabs.create({
                    url, active: Boolean(activate),
                });
                const newTabId = created.id;
                if (waitMode && waitMode !== "none") {
                    await ensureDebuggerAttached(newTabId);
                    const ev = PAGE_EVENT_FOR_WAIT[waitMode];
                    if (!ev) throw new Error(`invalid wait mode: ${waitMode}`);
                    await waitForPageEvent(newTabId, ev, timeout_ms || 15000);
                }
                const finalTab = await chrome.tabs.get(newTabId);
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: true,
                    payload: { tab_id: newTabId, url: finalTab.url },
                }));
            } catch (err) {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: false,
                    payload: `tab create failed: ${err.message || err}`,
                }));
            }
            return;
        }
```

Finally, refactor the existing EXECUTE branch to use `ensureDebuggerAttached`. Replace the EXECUTE block inside `socket.onmessage`:

```javascript
        } else if (type === "EXECUTE") {
            try {
                await ensureDebuggerAttached(tab.id);
                const debugTarget = { tabId: tab.id };
                chrome.debugger.sendCommand(
                    debugTarget, "Runtime.evaluate",
                    { expression: payload.code, returnByValue: true },
                    (result) => {
                        if (chrome.runtime.lastError) {
                            socket.send(JSON.stringify({
                                type: "RESPONSE", id, success: false,
                                payload: chrome.runtime.lastError.message,
                            }));
                        } else if (result.exceptionDetails) {
                            socket.send(JSON.stringify({
                                type: "RESPONSE", id, success: false,
                                payload: result.exceptionDetails.exception.description,
                            }));
                        } else {
                            socket.send(JSON.stringify({
                                type: "RESPONSE", id, success: true,
                                payload: result.result.value,
                            }));
                        }
                    }
                );
            } catch (error) {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: false,
                    payload: error.message || String(error),
                }));
            }
        }
```

- [ ] **Step 2: Commit**

```bash
git add src/saidkick/extension/background.js
git commit -m "feat(extension): NAVIGATE and OPEN handlers, debugger attach factored out"
```

---

## Task 8: `POST /navigate` and `POST /open` endpoints

**Files:**
- Modify: `src/saidkick/server.py`
- Create: `tests/test_navigate_open.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_navigate_open.py`:

```python
from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_navigate_routes_with_defaults():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"url": "https://example.com/"}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/navigate",
            json={"tab": "br-aaaa:1", "url": "https://example.com/"},
        )
    assert r.status_code == 200
    assert r.json() == {"url": "https://example.com/"}
    assert seen["args"][1] == "NAVIGATE"
    assert seen["payload"]["tab_id"] == 1
    assert seen["payload"]["url"] == "https://example.com/"
    assert seen["payload"]["wait"] == "dom"
    assert seen["payload"]["timeout_ms"] == 15000


def test_navigate_custom_wait_and_timeout():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"url": "https://x/"}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/navigate",
            json={
                "tab": "br-aaaa:1", "url": "https://x/",
                "wait": "full", "timeout_ms": 30000,
            },
        )
    assert r.status_code == 200
    assert seen["payload"]["wait"] == "full"
    assert seen["payload"]["timeout_ms"] == 30000


def test_navigate_malformed_url_is_400():
    setup_single_browser()
    r = TestClient(app).post(
        "/navigate", json={"tab": "br-aaaa:1", "url": "javascript:alert(1)"}
    )
    assert r.status_code == 400


def test_navigate_bad_wait_mode_is_422():
    setup_single_browser()
    r = TestClient(app).post(
        "/navigate",
        json={"tab": "br-aaaa:1", "url": "https://x/", "wait": "sort-of"},
    )
    assert r.status_code == 422


def test_navigate_timeout_is_504():
    setup_single_browser()
    async def fake_send(*args, **kwargs):
        return {"success": False, "payload": "navigation timeout after 15000ms"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/navigate", json={"tab": "br-aaaa:1", "url": "https://x/"}
        )
    assert r.status_code == 504


def test_open_routes_with_defaults():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"tab_id": 77, "url": "https://x/"}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/open", json={"browser": "br-aaaa", "url": "https://x/"}
        )
    assert r.status_code == 200
    body = r.json()
    assert body == {"tab": "br-aaaa:77", "url": "https://x/"}
    assert seen["args"][0] == "br-aaaa"
    assert seen["args"][1] == "OPEN"
    assert seen["payload"]["url"] == "https://x/"
    assert seen["payload"]["wait"] == "dom"
    assert seen["payload"]["timeout_ms"] == 15000
    assert seen["payload"]["activate"] is False


def test_open_activate_flag():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"tab_id": 77, "url": "https://x/"}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/open",
            json={"browser": "br-aaaa", "url": "https://x/", "activate": True},
        )
    assert r.status_code == 200
    assert seen["payload"]["activate"] is True


def test_open_malformed_browser_is_400():
    r = TestClient(app).post(
        "/open", json={"browser": "bad-id", "url": "https://x/"}
    )
    assert r.status_code == 400


def test_open_unknown_browser_is_404():
    manager.connections.clear()
    r = TestClient(app).post(
        "/open", json={"browser": "br-ffff", "url": "https://x/"}
    )
    assert r.status_code == 404


def test_open_malformed_url_is_400():
    setup_single_browser()
    r = TestClient(app).post(
        "/open", json={"browser": "br-aaaa", "url": "javascript:alert(1)"}
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_navigate_open.py -v`
Expected: many failures — endpoints and request models don't exist yet.

- [ ] **Step 3: Add request models and endpoints**

In `src/saidkick/server.py`, add new imports at the top:

```python
from typing import Literal
```

Add request models (place them with the other Pydantic models):

```python
WaitMode = Literal["dom", "full", "none"]


class NavigateRequest(BaseModel):
    tab: str
    url: str
    wait: WaitMode = "dom"
    timeout_ms: int = 15000


class OpenRequest(BaseModel):
    browser: str
    url: str
    wait: WaitMode = "dom"
    timeout_ms: int = 15000
    activate: bool = False
```

Add a browser-ID validator helper near `parse_tab_id`:

```python
_BROWSER_ID_RE = re.compile(r"^br-[0-9a-f]{4}$")


def _validate_browser_id(browser_id: str) -> None:
    if not isinstance(browser_id, str) or not _BROWSER_ID_RE.match(browser_id):
        raise HTTPException(
            status_code=400,
            detail=f"invalid browser ID: expected 'br-XXXX', got {browser_id!r}",
        )
```

Add the endpoints at the bottom of the endpoint block (after `/text`, before `/tabs`):

```python
@app.post("/navigate")
async def post_navigate(req: NavigateRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    _validate_http_url(req.url)
    response = await manager.send_command(
        browser_id, "NAVIGATE",
        payload={
            "tab_id": tab_id, "url": req.url,
            "wait": req.wait, "timeout_ms": req.timeout_ms,
        },
        timeout=_command_timeout(timeout_ms=req.timeout_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.post("/open")
async def post_open(req: OpenRequest):
    _validate_browser_id(req.browser)
    _validate_http_url(req.url)
    response = await manager.send_command(
        req.browser, "OPEN",
        payload={
            "url": req.url, "wait": req.wait,
            "timeout_ms": req.timeout_ms, "activate": req.activate,
        },
        timeout=_command_timeout(timeout_ms=req.timeout_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    ext_payload = response.get("payload") or {}
    ext_tab_id = ext_payload.get("tab_id")
    return {
        "tab": f"{req.browser}:{ext_tab_id}",
        "url": ext_payload.get("url"),
    }
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest tests/ --ignore=tests/test_saidkick_e2e.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/saidkick/server.py tests/test_navigate_open.py
git commit -m "feat(server): POST /navigate and POST /open with wait/timeout"
```

---

## Task 9: Python client methods

**Files:**
- Modify: `src/saidkick/client.py`

No new Python tests — thin wrapper; endpoints tested server-side, behavior surfaced via CLI in Task 12.

- [ ] **Step 1: Replace `SaidkickClient` in full**

Write `src/saidkick/client.py`:

```python
import httpx
from typing import List, Dict, Any, Optional


class SaidkickClient:
    def __init__(self, base_url: str = "http://localhost:6992"):
        self.base_url = base_url

    # Introspection

    def list_tabs(self, active: bool = False) -> List[Dict[str, Any]]:
        params = {"active": "true" if active else "false"}
        r = httpx.get(f"{self.base_url}/tabs", params=params)
        r.raise_for_status()
        return r.json()

    def get_logs(
        self, limit: int = 100, grep: Optional[str] = None,
        browser: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit}
        if grep:
            params["grep"] = grep
        if browser:
            params["browser"] = browser
        r = httpx.get(f"{self.base_url}/console", params=params)
        r.raise_for_status()
        return r.json()

    # Navigation

    def navigate(
        self, tab: str, url: str,
        wait: str = "dom", timeout_ms: int = 15000,
    ) -> Dict[str, Any]:
        r = httpx.post(
            f"{self.base_url}/navigate",
            json={"tab": tab, "url": url, "wait": wait, "timeout_ms": timeout_ms},
            timeout=timeout_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def open(
        self, browser: str, url: str,
        wait: str = "dom", timeout_ms: int = 15000, activate: bool = False,
    ) -> Dict[str, Any]:
        r = httpx.post(
            f"{self.base_url}/open",
            json={
                "browser": browser, "url": url,
                "wait": wait, "timeout_ms": timeout_ms, "activate": activate,
            },
            timeout=timeout_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    # DOM inspection

    def get_dom(
        self, tab: str, css: Optional[str] = None,
        xpath: Optional[str] = None, all_matches: bool = False,
        wait_ms: int = 0,
    ) -> str:
        params: Dict[str, Any] = {"tab": tab, "all": all_matches, "wait_ms": wait_ms}
        if css:
            params["css"] = css
        if xpath:
            params["xpath"] = xpath
        r = httpx.get(
            f"{self.base_url}/dom", params=params,
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def text(
        self, tab: str, css: Optional[str] = None, wait_ms: int = 0,
    ) -> str:
        params: Dict[str, Any] = {"tab": tab, "wait_ms": wait_ms}
        if css:
            params["css"] = css
        r = httpx.get(
            f"{self.base_url}/text", params=params,
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    # JS execution

    def execute(self, tab: str, code: str) -> Any:
        r = httpx.post(
            f"{self.base_url}/execute", json={"tab": tab, "code": code}
        )
        r.raise_for_status()
        return r.json()

    # Interaction

    def click(
        self, tab: str, css: Optional[str] = None,
        xpath: Optional[str] = None, wait_ms: int = 0,
    ) -> str:
        r = httpx.post(
            f"{self.base_url}/click",
            json={"tab": tab, "css": css, "xpath": xpath, "wait_ms": wait_ms},
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def type(
        self, tab: str, text: str,
        css: Optional[str] = None, xpath: Optional[str] = None,
        clear: bool = False, wait_ms: int = 0,
    ) -> str:
        r = httpx.post(
            f"{self.base_url}/type",
            json={
                "tab": tab, "css": css, "xpath": xpath,
                "text": text, "clear": clear, "wait_ms": wait_ms,
            },
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def select(
        self, tab: str, value: str,
        css: Optional[str] = None, xpath: Optional[str] = None,
        wait_ms: int = 0,
    ) -> str:
        r = httpx.post(
            f"{self.base_url}/select",
            json={
                "tab": tab, "css": css, "xpath": xpath,
                "value": value, "wait_ms": wait_ms,
            },
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 2: Sanity — existing server tests still green**

Run: `uv run pytest tests/ --ignore=tests/test_saidkick_e2e.py -q`
Expected: all pass. (Client is unexercised by server tests; this run is just paranoia about stray import failures.)

- [ ] **Step 3: Commit**

```bash
git add src/saidkick/client.py
git commit -m "feat(client): navigate, open, text methods; wait_ms on selector methods"
```

---

## Task 10: CLI commands and flags

**Files:**
- Modify: `src/saidkick/cli.py`

- [ ] **Step 1: Add the three new commands and `--wait-ms` on existing ones**

Open `src/saidkick/cli.py`. Update `dom`, `click`, `type`, `select` signatures to include `wait_ms`, and add `navigate`, `open`, `text` as new commands.

Replace the existing `dom` command:

```python
@app.command()
def dom(
    tab: str = typer.Option(..., "--tab", help="Target tab (br-XXXX:N)"),
    css: str = typer.Option(None, help="CSS selector"),
    xpath: str = typer.Option(None, help="XPath selector"),
    all_matches: bool = typer.Option(False, "--all", help="Return all matches"),
    wait_ms: int = typer.Option(0, "--wait-ms", help="Poll up to N ms for the selector to resolve"),
):
    """Get the current page DOM of the targeted tab."""
    try:
        result = client.get_dom(
            tab=tab, css=css, xpath=xpath,
            all_matches=all_matches, wait_ms=wait_ms,
        )
        sys.stdout.write(str(result))
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)
```

Replace `click`:

```python
@app.command()
def click(
    tab: str = typer.Option(..., "--tab", help="Target tab (br-XXXX:N)"),
    css: str = typer.Option(None, help="CSS selector"),
    xpath: str = typer.Option(None, help="XPath selector"),
    wait_ms: int = typer.Option(0, "--wait-ms", help="Poll up to N ms for the selector"),
):
    """Click an element in the targeted tab."""
    try:
        result = client.click(tab=tab, css=css, xpath=xpath, wait_ms=wait_ms)
        console.print(f"[success]{result}[/success]")
    except Exception as e:
        handle_client_error(e)
```

Replace `type`:

```python
@app.command()
def type(
    text: str = typer.Argument(..., help="Text to type"),
    tab: str = typer.Option(..., "--tab", help="Target tab (br-XXXX:N)"),
    css: str = typer.Option(None, help="CSS selector"),
    xpath: str = typer.Option(None, help="XPath selector"),
    clear: bool = typer.Option(False, "--clear", help="Clear field before typing"),
    wait_ms: int = typer.Option(0, "--wait-ms", help="Poll up to N ms for the selector"),
):
    """Type text into an element in the targeted tab."""
    try:
        result = client.type(
            tab=tab, text=text, css=css, xpath=xpath,
            clear=clear, wait_ms=wait_ms,
        )
        console.print(f"[success]{result}[/success]")
    except Exception as e:
        handle_client_error(e)
```

Replace `select`:

```python
@app.command()
def select(
    value: str = typer.Argument(..., help="Value or text to select"),
    tab: str = typer.Option(..., "--tab", help="Target tab (br-XXXX:N)"),
    css: str = typer.Option(None, help="CSS selector"),
    xpath: str = typer.Option(None, help="XPath selector"),
    wait_ms: int = typer.Option(0, "--wait-ms", help="Poll up to N ms for the selector"),
):
    """Select an option in a <select> element in the targeted tab."""
    try:
        result = client.select(
            tab=tab, value=value, css=css, xpath=xpath, wait_ms=wait_ms,
        )
        console.print(f"[success]{result}[/success]")
    except Exception as e:
        handle_client_error(e)
```

Add three new commands (put them after `select`):

```python
@app.command()
def navigate(
    url: str = typer.Argument(..., help="URL to navigate to"),
    tab: str = typer.Option(..., "--tab", help="Target tab (br-XXXX:N)"),
    wait: str = typer.Option("dom", "--wait", help="dom | full | none"),
    timeout_ms: int = typer.Option(15000, "--timeout-ms", help="Navigation timeout in ms"),
):
    """Send the targeted tab to a URL."""
    try:
        result = client.navigate(tab=tab, url=url, wait=wait, timeout_ms=timeout_ms)
        sys.stdout.write(result.get("url", ""))
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


@app.command("open")
def open_cmd(
    url: str = typer.Argument(..., help="URL to open"),
    browser: str = typer.Option(..., "--browser", help="Target browser (br-XXXX)"),
    wait: str = typer.Option("dom", "--wait", help="dom | full | none"),
    timeout_ms: int = typer.Option(15000, "--timeout-ms", help="Navigation timeout in ms"),
    activate: bool = typer.Option(False, "--activate", help="Focus the new tab"),
):
    """Open a URL in a new tab; prints the composite br-XXXX:N."""
    try:
        result = client.open(
            browser=browser, url=url, wait=wait,
            timeout_ms=timeout_ms, activate=activate,
        )
        sys.stdout.write(result.get("tab", ""))
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


@app.command()
def text(
    tab: str = typer.Option(..., "--tab", help="Target tab (br-XXXX:N)"),
    css: str = typer.Option(None, "--css", help="Optional CSS scope (innerText of matched element)"),
    wait_ms: int = typer.Option(0, "--wait-ms", help="Poll up to N ms for the selector"),
):
    """Print the readable (innerText) content of a tab or a CSS-scoped element."""
    try:
        result = client.text(tab=tab, css=css, wait_ms=wait_ms)
        sys.stdout.write(str(result))
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)
```

Note the `@app.command("open")` explicit name — Python's `open` built-in is shadowed by the function name, so we name the Typer command "open" but the Python symbol `open_cmd` to avoid colliding inside this module.

- [ ] **Step 2: Smoke — help text renders**

Run: `uv run saidkick --help`
Expected: commands listed include `navigate`, `open`, `text`.

Run: `uv run saidkick dom --help`
Expected: help output contains `--wait-ms`.

Run: `uv run saidkick navigate --help`
Expected: help lists `--tab`, `--wait`, `--timeout-ms` and `URL` argument.

- [ ] **Step 3: Commit**

```bash
git add src/saidkick/cli.py
git commit -m "feat(cli): navigate, open, text commands; --wait-ms on dom/click/type/select"
```

---

## Task 11: Docs, CHANGELOG, version bump

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`
- Modify: `docs/user-guide.md`
- Modify: `docs/design.md`

- [ ] **Step 1: Version bump**

Edit `pyproject.toml`: change `version = "0.2.0"` to `version = "0.3.0"`.

- [ ] **Step 2: CHANGELOG**

Prepend a new entry under `## [Unreleased]` in `CHANGELOG.md`:

```markdown
## [0.3.0] - 2026-04-21

### Features

- `POST /navigate` and `saidkick navigate --tab ID URL [--wait dom|full|none] [--timeout-ms N]` — send a tab to a URL. Returns the final URL after redirects.
- `POST /open` and `saidkick open --browser BR URL [--wait ...] [--timeout-ms N] [--activate]` — open a URL in a new tab; stdout is the composite `br-XXXX:N`, pipe-ready.
- `GET /text` and `saidkick text --tab ID [--css SCOPE] [--wait-ms N]` — return `innerText` of the page or a CSS-scoped element.
- `--wait-ms N` on `dom`, `click`, `type`, `select`, `text`: content-script polls the selector (every 100ms up to `N`ms) before acting. Default 0 preserves prior behavior.

### Fixes

- HTTP status codes are correct now. 0.2.0 returned `500` for caller-observable failures (`Element not found`, `Ambiguous selector`, `Option not found`, `Element is not a <select>`). These now return `404` (not found) and `400` (malformed / ambiguous) respectively. Upstream browser errors that we can't classify return `502`; timeouts return `504`. `500` is reserved for server bugs.
```

- [ ] **Step 3: Update `docs/user-guide.md`**

Append a new section after the existing "Identifying a tab" section, before "CLI Reference":

```markdown
## Driving a flow from scratch

`saidkick open` creates a new tab and returns its composite ID, so you can pipe a whole flow:

```bash
TAB=$(saidkick open --browser br-a1b2 "https://example.com/login")
saidkick type "alex" --tab "$TAB" --css "#username"
saidkick type "hunter2" --tab "$TAB" --css "#password"
saidkick click --tab "$TAB" --css "button[type=submit]" --wait-ms 3000
saidkick navigate --tab "$TAB" "https://example.com/dashboard"
saidkick text --tab "$TAB" --css "main" --wait-ms 5000
```

`--wait-ms` polls the selector until it resolves (or the timeout expires), which is what you want on any SPA or lazy-rendered page.
```

Then add entries for the three new commands in the CLI Reference section. After the existing `saidkick select` section, insert:

```markdown
### `saidkick navigate`
Send the targeted tab to a URL.
- `URL`: (Argument) The URL.
- `--tab` (required): Target tab (`br-XXXX:N`).
- `--wait`: `dom` (default — wait for DOMContentLoaded), `full` (wait for the load event), or `none` (return as soon as the URL is dispatched).
- `--timeout-ms`: Abort the wait after N ms (default 15000).

### `saidkick open`
Open a URL in a new tab. Stdout is the new composite `br-XXXX:N` for piping.
- `URL`: (Argument) The URL.
- `--browser` (required): Target browser (`br-XXXX`).
- `--wait`: Same semantics as `navigate` (default `dom`).
- `--timeout-ms`: Default 15000.
- `--activate`: Focus the new tab. Default is background.

### `saidkick text`
Print the readable (innerText) content of a tab.
- `--tab` (required): Target tab (`br-XXXX:N`).
- `--css`: Restrict to the innerText of the matched element (first match).
- `--wait-ms`: Poll up to N ms for the CSS selector.
```

Update the existing `dom`, `click`, `type`, `select` sections to mention `--wait-ms`. For each, add a bullet after the selector bullets:

```markdown
- `--wait-ms`: Poll up to N ms for the selector to resolve before acting (default 0 = fail immediately).
```

Finally, update the REST API table at the bottom:

```markdown
| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/tabs` | `GET` | List tabs across connected browsers. Query: `active`. |
| `/console` | `GET` | Get log history. Query: `limit`, `grep`, `browser`. |
| `/dom` | `GET` | Get page HTML. Query: `tab` (required), `css`, `xpath`, `all`, `wait_ms`. |
| `/text` | `GET` | Get innerText. Query: `tab` (required), `css`, `wait_ms`. |
| `/navigate` | `POST` | Send tab to URL. Body: `{"tab": ..., "url": ..., "wait": "dom\|full\|none", "timeout_ms": N}`. |
| `/open` | `POST` | Open URL in new tab. Body: `{"browser": ..., "url": ..., "wait": ..., "timeout_ms": N, "activate": bool}`. Returns `{"tab": "br-XXXX:N", "url": "..."}`. |
| `/execute` | `POST` | Execute JS. JSON body: `{"tab": ..., "code": ...}`. |
| `/click` | `POST` | Click element. Body: `{"tab": ..., "css": ..., "xpath": ..., "wait_ms": N}`. |
| `/type` | `POST` | Type text. Body: `{"tab": ..., "css": ..., "xpath": ..., "text": ..., "clear": bool, "wait_ms": N}`. |
| `/select` | `POST` | Select option. Body: `{"tab": ..., "css": ..., "xpath": ..., "value": ..., "wait_ms": N}`. |
```

Update the error codes section:

```markdown
### Error codes

- `400` — malformed input (bad `tab` or `browser` ID, invalid URL, ambiguous selector, wrong element type for command).
- `404` — referenced resource not found (browser not connected, tab not found, element not found after any `wait_ms`, select option missing).
- `422` — Pydantic validation failure (missing required field, invalid `wait` mode).
- `502` — upstream (browser) error we can't classify (e.g., content-script injection failed, unrecognized `chrome.runtime.lastError`).
- `504` — timeout (command response, navigation, selector never resolved within `wait_ms`).
- `500` — server bug. Reserved; seeing one is a defect report.
```

- [ ] **Step 4: Update `docs/design.md`**

Append a new section after the existing "Server-side state" section:

```markdown
### Navigation waits

`NAVIGATE` and `OPEN` use the `chrome.debugger` Page domain for precise load-state semantics:

- `wait = "dom"` resolves on the first `Page.domContentLoaded` after the navigation.
- `wait = "full"` resolves on the first `Page.loadEventFired`.
- `wait = "none"` returns as soon as `chrome.tabs.update` / `chrome.tabs.create` resolves.

The debugger is attached lazily (reused from `EXECUTE`'s existing logic) and stays attached after the wait resolves. A `timeout_ms` bound caps the wait; on exceeding it the extension replies with `navigation timeout after {N}ms`, which the server maps to HTTP 504.

### Wait-for-element

The content script's `waitForSelector(css, xpath, waitMs)` polls `document.querySelectorAll` (or `document.evaluate` for XPath) every 100ms until exactly one match is found, or `waitMs` elapses. Ambiguous matches (≥2) during polling do *not* throw immediately — the DOM may still be settling; the helper only throws `Ambiguous selector` once the deadline expires. Zero-match with an expired deadline throws `element not found`, which the server maps to HTTP 404.

### Error policy

- 400 = malformed input (bad IDs, invalid URLs, wrong element type, ambiguous selector).
- 404 = resource not found (browser, tab, element, option).
- 422 = Pydantic validation.
- 502 = unrecognized upstream (browser) error.
- 504 = timeout.
- 500 = actual server bug. Reserved.

The server's `_raise_for_extension_error` helper pattern-matches on the extension's failure strings to assign the right 4xx/5xx. Extension-side strings are owned by this repo, so the matching is deterministic.
```

- [ ] **Step 5: Run the full suite once more**

Run: `uv run pytest tests/ --ignore=tests/test_saidkick_e2e.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml CHANGELOG.md docs/user-guide.md docs/design.md
git commit -m "docs: navigation, open, text, wait_ms; bump to 0.3.0"
```

---

## Task 12: Manual end-to-end smoke test

No automation. Each step is verify-and-confirm; file a followup on any failure.

- [ ] **Step 1: Reload the extension**

`chrome://extensions/` → reload Saidkick (or Load unpacked from `src/saidkick/extension/` if not yet installed).

- [ ] **Step 2: Start the server**

Run: `uv run saidkick start`
Expected: server log includes `Browser connected: br-XXXX`.

- [ ] **Step 3: `open` and drive a flow**

```bash
BR=br-XXXX   # from `saidkick tabs` or the server log
TAB=$(uv run saidkick open --browser "$BR" https://example.com/)
echo "opened $TAB"
uv run saidkick text --tab "$TAB" --css "h1"
```

Expected: `$TAB` is `br-XXXX:N`; the `text` output is `"Example Domain"`.

- [ ] **Step 4: `navigate` between URLs**

```bash
uv run saidkick navigate --tab "$TAB" https://www.iana.org/help/example-domains
uv run saidkick text --tab "$TAB" --css "h1"
```

Expected: the URL is echoed; the text contains `"Example Domains"`.

- [ ] **Step 5: `--wait-ms` on a lazy element**

Pick any page with a lazy-rendered element (e.g., a modal that appears after a click). Confirm `saidkick click --tab "$TAB" --css ".modal-button" --wait-ms 3000` either clicks it or returns `element not found` after ~3 seconds (not immediately).

- [ ] **Step 6: Error-code spot check**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:6992/click \
  -H "Content-Type: application/json" \
  -d "{\"tab\":\"$TAB\",\"css\":\".does-not-exist\"}"
```

Expected: `404` (not `500`).

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:6992/navigate \
  -H "Content-Type: application/json" \
  -d "{\"tab\":\"$TAB\",\"url\":\"javascript:alert(1)\"}"
```

Expected: `400`.

- [ ] **Step 7: Journal + workspace-side bookkeeping**

Append to `vault/Calendar/Journal/journal-2026-04-21.md`:

```
> 🤖 HH:MM — milestone: saidkick 0.3.0 landed (navigate/open/text/wait_ms + error taxonomy). Manual smoke passed.
```

Back in `/home/apiad/Workspace`, update `vault/Efforts/Repos/saidkick.md`:
- Mark the Tier-1 "Navigation", "Wait-for-element", and "Full-page readable text" items as done in 0.3.0.
- Add a note under "Current surface" about the 0.3.0 additions.
- Bump `last_sync` in frontmatter.
- Commit: `chore(repos): sync saidkick after 0.3.0 landed`.

---

## Self-Review

**1. Spec coverage.**
- Error taxonomy + classifier → Task 1, applied in Task 2.
- URL validator → Task 1.
- `wait_ms` on existing selector endpoints → Task 3; protocol field in Task 5.
- `send_command` per-request timeout → Task 4.
- `waitForSelector` / `waitForAnyMatches` in content.js → Task 5.
- `GET_TEXT` extension handler → Task 5; `/text` server endpoint → Task 6.
- `NAVIGATE`/`OPEN` extension handlers → Task 7; `/navigate`/`/open` server endpoints → Task 8.
- Python client → Task 9. CLI → Task 10.
- Version bump + CHANGELOG + docs → Task 11. Manual smoke → Task 12.

**2. Placeholder scan.** No "TBD", no "add error handling" (concrete taxonomy used). Every code step shows the code. Expected test outputs are stated.

**3. Type consistency.** `send_command(browser_id, type, payload, timeout)` signature from Task 4 is used consistently in Tasks 6, 8. `_raise_for_extension_error(msg)` from Task 1 used in Tasks 2, 3, 6, 8. `_command_timeout(wait_ms, timeout_ms)` from Task 4 used consistently. `parse_tab_id` returns `(str, int)` — unchanged. Extension response shape `{type: "RESPONSE", id, success, payload}` consistent across all handlers.

**4. Ambiguity check.**
- Task 3's fake_send signatures use `*args, **kwargs` to be forward-compatible with Task 4's `timeout` kwarg — called out explicitly.
- Task 5 `waitForSelector` behavior on `wait_ms === 0` preserves 0.2.0 semantics (polls once, fails immediately) — called out.
- `open` endpoint takes `browser` (string), not `tab`, because there's no tab yet — the only endpoint with this shape.
