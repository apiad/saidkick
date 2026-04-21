# Tab Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace saidkick's implicit "last-connected browser + active tab" targeting with explicit multi-browser, multi-tab addressing via composite `br-XXXX:N` IDs, gated by a new `GET /tabs` endpoint and a `HELLO` handshake. All action endpoints and CLI commands gain a required `tab` parameter.

**Architecture:** The server assigns a `browser_id` on WebSocket handshake, tracks connections in a `Dict[str, WebSocket]`, and routes every action command to the right browser by parsing the composite tab ID at the endpoint boundary. The extension stores its assigned ID, answers `LIST_TABS`, and uses `payload.tab_id` directly (no more client-side heuristic). Python client and Typer CLI propagate `tab` as a required parameter.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, pytest + pytest-asyncio, `fastapi.testclient.TestClient` (incl. `websocket_connect`), `unittest.mock.AsyncMock`. Chrome MV3 extension (vanilla JS). Typer + Rich CLI.

---

## File Structure

**Modified:**
- `src/saidkick/server.py` — state refactor (`connections: Dict`), handshake with HELLO, `parse_tab_id` helper, `GET /tabs`, all action endpoints require `tab`, log tagging, `/console?browser=` filter.
- `src/saidkick/client.py` — `list_tabs`, required `tab` arg on action methods, optional `browser` arg on `get_logs`.
- `src/saidkick/cli.py` — new `tabs` command, required `--tab` on action commands, optional `--browser` on `logs`.
- `src/saidkick/extension/background.js` — `HELLO` handler storing `browserId`, `LIST_TABS` handler, remove heuristic, use `payload.tab_id` in existing handlers.
- `pyproject.toml` — bump version to `0.2.0`.
- `tests/test_saidkick.py` — update existing tests to new signatures.
- `tests/test_saidkick_enhanced.py` — update existing tests to new signatures.
- `docs/user-guide.md`, `docs/design.md` — reflect new protocol.

**Created:**
- `tests/test_tabs.py` — all new coverage: `parse_tab_id`, browser ID generation, HELLO handshake, `GET /tabs` aggregation, error taxonomy.
- `CHANGELOG.md` — Keep-a-Changelog format, seeded with `[0.2.0]`.

**Extension/content script unchanged:** `content.js`, `main_world.js`, `manifest.json`. Tab-scoped delivery is already correct via `chrome.tabs.sendMessage(tabId, ...)`.

---

## Task 1: `parse_tab_id` helper

**Files:**
- Modify: `src/saidkick/server.py` (add helper at module level)
- Create test: `tests/test_tabs.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tabs.py`:

```python
import pytest
from saidkick.server import parse_tab_id


def test_parse_tab_id_valid():
    assert parse_tab_id("br-a1b2:42") == ("br-a1b2", 42)
    assert parse_tab_id("br-0000:1") == ("br-0000", 1)
    assert parse_tab_id("br-ffff:999999") == ("br-ffff", 999999)


@pytest.mark.parametrize("bad", [
    "",
    "br-a1b2",
    "br-a1b2:",
    "br-a1b2:abc",
    "br-XYZ1:42",         # non-hex chars
    "br-a1b:42",          # too few hex chars
    "br-a1b2c:42",        # too many hex chars
    "a1b2:42",            # missing br- prefix
    "br-a1b2:42:extra",   # extra segment
    "br-a1b2:-1",         # negative
])
def test_parse_tab_id_invalid_raises(bad):
    with pytest.raises(ValueError):
        parse_tab_id(bad)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/apiad/Workspace/repos/saidkick && uv run pytest tests/test_tabs.py -v`
Expected: ImportError for `parse_tab_id` (function does not exist yet).

- [ ] **Step 3: Implement `parse_tab_id`**

Add at the top of `src/saidkick/server.py`, just below the existing imports:

```python
import re

_TAB_ID_RE = re.compile(r"^br-[0-9a-f]{4}:(\d+)$")


def parse_tab_id(composite: str) -> tuple[str, int]:
    """Parse 'br-XXXX:N' into (browser_id, tab_id). Raises ValueError on malformed input."""
    if not isinstance(composite, str):
        raise ValueError(f"tab ID must be a string, got {type(composite).__name__}")
    m = _TAB_ID_RE.match(composite)
    if not m:
        raise ValueError(f"invalid tab ID: expected 'br-XXXX:N', got {composite!r}")
    browser_id, tab_str = composite.rsplit(":", 1)
    return browser_id, int(tab_str)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tabs.py -v`
Expected: all parse_tab_id tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/saidkick
git add src/saidkick/server.py tests/test_tabs.py
git commit -m "feat(server): add parse_tab_id helper for composite tab addressing"
```

---

## Task 2: `SaidkickManager` state refactor + `generate_browser_id`

**Files:**
- Modify: `src/saidkick/server.py` (class `SaidkickManager`)
- Modify: `tests/test_tabs.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tabs.py`:

```python
import re
from saidkick.server import SaidkickManager


def test_generate_browser_id_format():
    m = SaidkickManager()
    for _ in range(100):
        bid = m.generate_browser_id()
        assert re.match(r"^br-[0-9a-f]{4}$", bid), f"bad format: {bid}"


def test_generate_browser_id_avoids_collision():
    m = SaidkickManager()
    # Pre-populate connections with all but one ID in a tiny space
    # We can't exhaust 65k, but we can verify the loop rejects an already-used ID.
    m.connections = {"br-aaaa": object()}  # type: ignore[assignment]
    # Force rng to produce 'br-aaaa' first then 'br-bbbb' by monkeypatching
    import itertools
    sequence = iter(["br-aaaa", "br-bbbb"])
    m._random_browser_id = lambda: next(sequence)  # type: ignore[attr-defined]
    bid = m.generate_browser_id()
    assert bid == "br-bbbb"


def test_manager_connections_is_dict():
    m = SaidkickManager()
    assert isinstance(m.connections, dict)
    assert m.connections == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tabs.py -v`
Expected: failures around missing `connections` dict and `generate_browser_id`.

- [ ] **Step 3: Refactor `SaidkickManager`**

Replace the `__init__`, `add_connection`, and `remove_connection` methods in `src/saidkick/server.py`. The old `active_connections: List[WebSocket]` becomes `connections: Dict[str, WebSocket]`. Keep `logs` and `pending_requests` as-is.

```python
import secrets

class SaidkickManager:
    def __init__(self, max_logs: int = 100):
        self.logs = deque(maxlen=max_logs)
        self.connections: Dict[str, WebSocket] = {}
        self.pending_requests: Dict[str, asyncio.Future] = {}

    def _random_browser_id(self) -> str:
        # 4 hex chars = 65k space, ample for expected usage.
        return "br-" + secrets.token_hex(2)

    def generate_browser_id(self) -> str:
        # Avoid collision with an already-connected browser.
        for _ in range(1000):
            bid = self._random_browser_id()
            if bid not in self.connections:
                return bid
        raise RuntimeError("could not generate unique browser_id after 1000 attempts")

    async def add_connection(self, websocket: WebSocket) -> str:
        await websocket.accept()
        browser_id = self.generate_browser_id()
        self.connections[browser_id] = websocket
        logger.info(f"[status] Browser connected: {browser_id}")
        return browser_id

    def remove_connection(self, browser_id: str):
        if browser_id in self.connections:
            del self.connections[browser_id]
            logger.info(f"[status] Browser disconnected: {browser_id}")
```

Also update the imports at top of `server.py`:

```python
import asyncio
import json
import logging
import re
import secrets
import uuid
from collections import deque
from typing import Any, Dict, List, Optional, Tuple
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tabs.py -v`
Expected: all 3 new tests PASS (existing endpoint tests will be broken until Task 5; that's expected).

- [ ] **Step 5: Commit**

```bash
git add src/saidkick/server.py tests/test_tabs.py
git commit -m "refactor(server): connections dict keyed by generated browser_id"
```

---

## Task 3: HELLO handshake on WS accept

**Files:**
- Modify: `src/saidkick/server.py` (websocket_endpoint)
- Modify: `tests/test_tabs.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tabs.py`:

```python
from fastapi.testclient import TestClient
from saidkick.server import app, manager


def test_ws_handshake_sends_hello():
    client = TestClient(app)
    manager.connections.clear()
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "HELLO"
        assert re.match(r"^br-[0-9a-f]{4}$", hello["browser_id"])
        # Connection recorded on the server
        assert hello["browser_id"] in manager.connections


def test_ws_disconnect_removes_connection():
    client = TestClient(app)
    manager.connections.clear()
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        bid = hello["browser_id"]
        assert bid in manager.connections
    # After exiting the context manager the WS is closed
    # Server-side cleanup is async; give the event loop a tick
    import time; time.sleep(0.1)
    assert bid not in manager.connections
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tabs.py::test_ws_handshake_sends_hello tests/test_tabs.py::test_ws_disconnect_removes_connection -v`
Expected: failures — WS does not send HELLO yet.

- [ ] **Step 3: Update `websocket_endpoint`**

Replace the existing `websocket_endpoint` in `src/saidkick/server.py`:

```python
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    browser_id = await manager.add_connection(websocket)
    await websocket.send_text(json.dumps({"type": "HELLO", "browser_id": browser_id}))
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get("type")
            if msg_type == "log":
                manager.handle_log(browser_id, message)
            elif msg_type == "RESPONSE":
                manager.handle_response(message)
    except WebSocketDisconnect:
        manager.remove_connection(browser_id)
    except Exception as e:
        logger.error(f"[error] WebSocket error on {browser_id}: {e}")
        manager.remove_connection(browser_id)
```

Note: `handle_log` now takes `browser_id` as first arg — Task 6 wires that through. For now, temporarily update `handle_log`:

```python
def handle_log(self, browser_id: str, message: Dict[str, Any]):
    level = message.get("level", "info").upper()
    content = message.get("data")
    logger.info(f"[BROWSER {browser_id}] {level}: {content}")
    message = {**message, "browser_id": browser_id}
    self.logs.append(message)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tabs.py -v`
Expected: `test_ws_handshake_sends_hello` and `test_ws_disconnect_removes_connection` PASS.

- [ ] **Step 5: Commit**

```bash
git add src/saidkick/server.py tests/test_tabs.py
git commit -m "feat(server): HELLO handshake assigns browser_id on WS connect"
```

---

## Task 4: `send_command` by browser_id + `GET /tabs` endpoint

**Files:**
- Modify: `src/saidkick/server.py` (send_command signature, new /tabs endpoint)
- Modify: `tests/test_tabs.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tabs.py`:

```python
from unittest.mock import AsyncMock, patch


def test_get_tabs_empty_when_no_browsers():
    manager.connections.clear()
    c = TestClient(app)
    r = c.get("/tabs")
    assert r.status_code == 200
    assert r.json() == []


def test_get_tabs_aggregates_across_browsers():
    manager.connections.clear()
    # Register two stubs so the endpoint sees two connected browsers
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]
    manager.connections["br-bbbb"] = object()  # type: ignore[assignment]

    async def fake_send(browser_id, command_type, payload=None):
        assert command_type == "LIST_TABS"
        return {
            "success": True,
            "payload": [
                {"id": 1, "url": "https://a.com/", "title": "A",
                 "active": True, "windowId": 10},
                {"id": 2, "url": "https://b.com/", "title": "B",
                 "active": False, "windowId": 10},
            ],
        } if browser_id == "br-aaaa" else {
            "success": True,
            "payload": [
                {"id": 5, "url": "https://c.com/", "title": "C",
                 "active": True, "windowId": 20},
            ],
        }

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/tabs")
    assert r.status_code == 200
    data = r.json()
    tabs = {entry["tab"]: entry for entry in data}
    assert "br-aaaa:1" in tabs
    assert "br-aaaa:2" in tabs
    assert "br-bbbb:5" in tabs
    assert tabs["br-aaaa:1"]["browser_id"] == "br-aaaa"
    assert tabs["br-aaaa:1"]["tab_id"] == 1
    assert tabs["br-aaaa:1"]["url"] == "https://a.com/"
    assert tabs["br-aaaa:1"]["active"] is True


def test_get_tabs_active_filter():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    async def fake_send(browser_id, command_type, payload=None):
        return {"success": True, "payload": [
            {"id": 1, "url": "a", "title": "A", "active": True,  "windowId": 10},
            {"id": 2, "url": "b", "title": "B", "active": False, "windowId": 10},
        ]}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/tabs?active=true")
    assert r.status_code == 200
    tabs = r.json()
    assert len(tabs) == 1
    assert tabs[0]["tab"] == "br-aaaa:1"


def test_get_tabs_skips_browser_on_timeout():
    from fastapi import HTTPException
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]
    manager.connections["br-bbbb"] = object()  # type: ignore[assignment]

    async def fake_send(browser_id, command_type, payload=None):
        if browser_id == "br-aaaa":
            raise HTTPException(status_code=504, detail="Browser response timeout")
        return {"success": True, "payload": [
            {"id": 5, "url": "c", "title": "C", "active": True, "windowId": 20},
        ]}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/tabs")
    assert r.status_code == 200
    tabs = {entry["tab"]: entry for entry in r.json()}
    assert "br-bbbb:5" in tabs
    assert not any(t.startswith("br-aaaa:") for t in tabs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tabs.py -v -k "get_tabs"`
Expected: failures — `/tabs` endpoint and new `send_command` signature don't exist yet.

- [ ] **Step 3: Update `send_command` and add `/tabs`**

Replace `SaidkickManager.send_command` in `src/saidkick/server.py`:

```python
async def send_command(
    self, browser_id: str, command_type: str, payload: Any = None
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
            status_code=503, detail=f"browser send failed: {e}"
        ) from e

    try:
        response = await asyncio.wait_for(future, timeout=10.0)
        return response
    except asyncio.TimeoutError as e:
        self.pending_requests.pop(request_id, None)
        raise HTTPException(
            status_code=504, detail="Browser response timeout"
        ) from e
```

Add `/tabs` endpoint at the bottom of `server.py`:

```python
@app.get("/tabs")
async def get_tabs(active: bool = False):
    browser_ids = list(manager.connections.keys())
    tabs: List[Dict[str, Any]] = []

    async def _fetch(bid: str):
        try:
            resp = await manager.send_command(bid, "LIST_TABS")
        except HTTPException:
            return bid, None
        if not resp.get("success"):
            return bid, None
        return bid, resp.get("payload") or []

    results = await asyncio.gather(*(_fetch(bid) for bid in browser_ids))
    for bid, raw_tabs in results:
        if raw_tabs is None:
            continue
        for t in raw_tabs:
            if active and not t.get("active"):
                continue
            tabs.append({
                "tab": f"{bid}:{t['id']}",
                "browser_id": bid,
                "tab_id": t["id"],
                "url": t.get("url"),
                "title": t.get("title"),
                "active": bool(t.get("active")),
                "windowId": t.get("windowId"),
            })
    return tabs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tabs.py -v -k "get_tabs"`
Expected: all four `get_tabs_*` tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/saidkick/server.py tests/test_tabs.py
git commit -m "feat(server): GET /tabs aggregates LIST_TABS across connected browsers"
```

---

## Task 5: Action endpoints require `tab`

**Files:**
- Modify: `src/saidkick/server.py` (pydantic models + endpoints)
- Modify: `tests/test_saidkick.py`, `tests/test_saidkick_enhanced.py`
- Modify: `tests/test_tabs.py`

- [ ] **Step 1: Write the failing tests for error taxonomy**

Append to `tests/test_tabs.py`:

```python
def test_execute_missing_tab_is_400():
    r = TestClient(app).post("/execute", json={"code": "1+1"})
    assert r.status_code == 422  # pydantic rejects missing required field


def test_execute_malformed_tab_is_400():
    r = TestClient(app).post(
        "/execute", json={"tab": "not-a-tab", "code": "1+1"}
    )
    assert r.status_code == 400
    assert "invalid tab ID" in r.json()["detail"]


def test_execute_unknown_browser_is_404():
    manager.connections.clear()
    r = TestClient(app).post(
        "/execute", json={"tab": "br-zzzz:1", "code": "1+1"}
    )
    assert r.status_code == 404
    assert "not connected" in r.json()["detail"]


def test_execute_routes_to_correct_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    seen = {}
    async def fake_send(browser_id, command_type, payload=None):
        seen["browser_id"] = browser_id
        seen["type"] = command_type
        seen["payload"] = payload
        return {"success": True, "payload": "ok"}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/execute", json={"tab": "br-aaaa:42", "code": "1+1"}
        )
    assert r.status_code == 200
    assert seen["browser_id"] == "br-aaaa"
    assert seen["type"] == "EXECUTE"
    # payload should carry tab_id + code for the extension
    assert seen["payload"]["tab_id"] == 42
    assert seen["payload"]["code"] == "1+1"


def test_dom_routes_to_correct_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    seen = {}
    async def fake_send(browser_id, command_type, payload=None):
        seen["browser_id"] = browser_id
        seen["payload"] = payload
        return {"success": True, "payload": "<div/>"}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/dom?tab=br-aaaa:7&css=.foo")
    assert r.status_code == 200
    assert seen["browser_id"] == "br-aaaa"
    assert seen["payload"]["tab_id"] == 7
    assert seen["payload"]["css"] == ".foo"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tabs.py -v -k "execute_ or dom_routes"`
Expected: failures — endpoints don't enforce `tab` yet.

- [ ] **Step 3: Update endpoint models and handlers**

In `src/saidkick/server.py`, update the Pydantic models and endpoint bodies. Replace the existing models:

```python
class ExecuteRequest(BaseModel):
    tab: str
    code: str


class SelectorRequest(BaseModel):
    tab: str
    css: Optional[str] = None
    xpath: Optional[str] = None


class TypeRequest(SelectorRequest):
    text: str
    clear: bool = False


class SelectRequest(SelectorRequest):
    value: str
```

Replace every action endpoint. Each one parses the tab, pulls out `browser_id` and `tab_id`, and forwards both (with the rest of the payload) to the extension.

```python
def _parse_or_400(tab: str) -> Tuple[str, int]:
    try:
        return parse_tab_id(tab)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/dom")
async def get_dom(
    tab: str,
    css: Optional[str] = None,
    xpath: Optional[str] = None,
    all: bool = False,
):
    browser_id, tab_id = _parse_or_400(tab)
    response = await manager.send_command(
        browser_id, "GET_DOM",
        payload={"tab_id": tab_id, "css": css, "xpath": xpath, "all": all},
    )
    return response.get("payload")


@app.post("/execute")
async def post_execute(req: ExecuteRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    response = await manager.send_command(
        browser_id, "EXECUTE",
        payload={"tab_id": tab_id, "code": req.code},
    )
    if not response.get("success"):
        raise HTTPException(status_code=500, detail=response.get("payload"))
    return response.get("payload")


@app.post("/click")
async def post_click(req: SelectorRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    response = await manager.send_command(
        browser_id, "CLICK",
        payload={"tab_id": tab_id, "css": req.css, "xpath": req.xpath},
    )
    if not response.get("success"):
        raise HTTPException(status_code=500, detail=response.get("payload"))
    return response.get("payload")


@app.post("/type")
async def post_type(req: TypeRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    response = await manager.send_command(
        browser_id, "TYPE",
        payload={
            "tab_id": tab_id,
            "css": req.css,
            "xpath": req.xpath,
            "text": req.text,
            "clear": req.clear,
        },
    )
    if not response.get("success"):
        raise HTTPException(status_code=500, detail=response.get("payload"))
    return response.get("payload")


@app.post("/select")
async def post_select(req: SelectRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    response = await manager.send_command(
        browser_id, "SELECT",
        payload={
            "tab_id": tab_id,
            "css": req.css,
            "xpath": req.xpath,
            "value": req.value,
        },
    )
    if not response.get("success"):
        raise HTTPException(status_code=500, detail=response.get("payload"))
    return response.get("payload")
```

Note: old `/execute` accepted bare `{"code": ...}` body. Now it's `{"tab": ..., "code": ...}`. Old `/dom` had no tab param. Now `tab` is required in the query string.

- [ ] **Step 4: Update the pre-existing tests to pass `tab`**

Replace `tests/test_saidkick.py` entirely:

```python
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from saidkick.server import app, manager

client = TestClient(app)


def test_get_console_empty():
    manager.logs.clear()
    response = client.get("/console")
    assert response.status_code == 200
    assert response.json() == []


def test_get_console_with_logs():
    manager.logs.clear()
    manager.logs.append({
        "level": "log", "data": "test message",
        "timestamp": "2024-01-01", "url": "test",
        "browser_id": "br-aaaa",
    })
    response = client.get("/console")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["data"] == "test message"


def test_post_execute_routes_through_send_command():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    with patch.object(manager, "send_command", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"success": True, "payload": "result"}
        response = client.post(
            "/execute", json={"tab": "br-aaaa:1", "code": "console.log(1)"}
        )
    assert response.status_code == 200
    assert response.json() == "result"
    mock_send.assert_called_with(
        "br-aaaa", "EXECUTE", payload={"tab_id": 1, "code": "console.log(1)"}
    )
```

Replace `tests/test_saidkick_enhanced.py` entirely:

```python
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from saidkick.server import app, manager

client = TestClient(app)


def test_console_filtering():
    manager.logs.clear()
    manager.logs.append({"level": "info", "data": "Hello World", "browser_id": "br-aaaa"})
    manager.logs.append({"level": "error", "data": "Something failed", "browser_id": "br-aaaa"})
    manager.logs.append({"level": "info", "data": "Goodbye World", "browser_id": "br-bbbb"})

    response = client.get("/console?limit=1")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["data"] == "Goodbye World"

    response = client.get("/console?grep=World")
    assert response.status_code == 200
    assert len(response.json()) == 2

    response = client.get("/console?grep=World&limit=1")
    assert len(response.json()) == 1
    assert response.json()[0]["data"] == "Goodbye World"


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
        payload={"tab_id": 3, "css": ".test", "xpath": None, "all": True},
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
            payload={"tab_id": 1, "css": "#btn", "xpath": None},
        )

        response = client.post("/type", json={
            "tab": "br-aaaa:2", "css": "#input", "text": "hello", "clear": True,
        })
        assert response.status_code == 200
        mock_send.assert_called_with(
            "br-aaaa", "TYPE",
            payload={
                "tab_id": 2, "css": "#input", "xpath": None,
                "text": "hello", "clear": True,
            },
        )

        response = client.post("/select", json={
            "tab": "br-aaaa:3", "xpath": "//select", "value": "opt1",
        })
        assert response.status_code == 200
        mock_send.assert_called_with(
            "br-aaaa", "SELECT",
            payload={
                "tab_id": 3, "css": None, "xpath": "//select", "value": "opt1",
            },
        )
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/ -v --ignore=tests/test_saidkick_e2e.py`
Expected: all pass. (The e2e file is skipped — it has `@pytest.mark.e2e` and needs a real browser.)

- [ ] **Step 6: Commit**

```bash
git add src/saidkick/server.py tests/test_saidkick.py tests/test_saidkick_enhanced.py tests/test_tabs.py
git commit -m "feat(server): action endpoints require 'tab' and route by browser_id"
```

---

## Task 6: Log tagging + `/console?browser=` filter

**Files:**
- Modify: `src/saidkick/server.py` (`get_logs`, `/console` endpoint)
- Modify: `tests/test_tabs.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tabs.py`:

```python
def test_console_browser_filter():
    manager.logs.clear()
    manager.logs.append({"level": "info", "data": "from A", "browser_id": "br-aaaa"})
    manager.logs.append({"level": "info", "data": "from B", "browser_id": "br-bbbb"})
    manager.logs.append({"level": "info", "data": "also A", "browser_id": "br-aaaa"})

    r = TestClient(app).get("/console?browser=br-aaaa")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert all(e["browser_id"] == "br-aaaa" for e in data)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tabs.py::test_console_browser_filter -v`
Expected: fail — filter not implemented.

- [ ] **Step 3: Implement filter**

Update `SaidkickManager.get_logs` in `src/saidkick/server.py`:

```python
def get_logs(
    self, limit: int = 100,
    grep: Optional[str] = None, browser: Optional[str] = None,
) -> List[Dict[str, Any]]:
    all_logs = list(self.logs)
    if browser:
        all_logs = [l for l in all_logs if l.get("browser_id") == browser]
    if grep:
        import re
        pattern = re.compile(grep)
        all_logs = [l for l in all_logs if pattern.search(str(l.get("data", "")))]
    return all_logs[-limit:] if limit > 0 else all_logs
```

Update the `/console` endpoint signature:

```python
@app.get("/console")
async def get_console(
    limit: int = 100,
    grep: Optional[str] = None,
    browser: Optional[str] = None,
):
    return manager.get_logs(limit=limit, grep=grep, browser=browser)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ -v --ignore=tests/test_saidkick_e2e.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/saidkick/server.py tests/test_tabs.py
git commit -m "feat(server): /console?browser= filter and browser_id stamping"
```

---

## Task 7: Extension — HELLO handler + browserId storage

**Files:**
- Modify: `src/saidkick/extension/background.js`

No automated tests for JS; verify by manual smoke (Task 14).

- [ ] **Step 1: Update `background.js` `socket.onopen` and `socket.onmessage`**

At the top of `background.js`, add module-scope state:

```javascript
let socket = null;
let browserId = null;
const SERVER_URL = "ws://localhost:6992/ws";
const logQueue = [];
```

Update the `socket.onmessage` handler to intercept `HELLO` as the first branch. Full replacement for the `onmessage` body (the `onopen`, `onclose`, `onerror` stay; we will rewrite the body in Tasks 8–9):

```javascript
socket.onmessage = async (event) => {
    const message = JSON.parse(event.data);
    const { type, id, payload } = message;

    if (type === "HELLO") {
        browserId = payload?.browser_id ?? message.browser_id;
        console.log(`Saidkick: connected as ${browserId}`);
        return;
    }

    // ... existing GET_DOM/CLICK/TYPE/SELECT/EXECUTE handlers below ...
};
```

Note: the server sends `{"type": "HELLO", "browser_id": "..."}` at the top level, not inside `payload`. We read `message.browser_id`.

- [ ] **Step 2: Commit**

```bash
git add src/saidkick/extension/background.js
git commit -m "feat(extension): handle HELLO frame and store assigned browser_id"
```

---

## Task 8: Extension — `LIST_TABS` handler

**Files:**
- Modify: `src/saidkick/extension/background.js`

- [ ] **Step 1: Add LIST_TABS branch to `socket.onmessage`**

Inside `socket.onmessage`, after the `HELLO` branch and before the existing command branches, insert:

```javascript
if (type === "LIST_TABS") {
    try {
        const rawTabs = await chrome.tabs.query({});
        const tabs = rawTabs
            .filter(t => t.url && !t.url.startsWith("chrome://")
                && !t.url.startsWith("chrome-extension://")
                && !t.url.startsWith("devtools://"))
            .map(t => ({
                id: t.id,
                url: t.url,
                title: t.title,
                active: t.active,
                windowId: t.windowId,
            }));
        socket.send(JSON.stringify({
            type: "RESPONSE", id, success: true, payload: tabs,
        }));
    } catch (err) {
        socket.send(JSON.stringify({
            type: "RESPONSE", id, success: false, payload: err.toString(),
        }));
    }
    return;
}
```

- [ ] **Step 2: Commit**

```bash
git add src/saidkick/extension/background.js
git commit -m "feat(extension): LIST_TABS handler enumerates scriptable tabs"
```

---

## Task 9: Extension — remove heuristic, use `payload.tab_id`

**Files:**
- Modify: `src/saidkick/extension/background.js`

- [ ] **Step 1: Rewrite the remaining `socket.onmessage` body**

Replace everything in `socket.onmessage` after the `HELLO` and `LIST_TABS` branches. Delete the old `chrome.tabs.query` heuristic block and the localhost fallbacks. New body:

```javascript
// All remaining commands target a specific tab_id supplied in payload.
const tabId = payload?.tab_id;
if (typeof tabId !== "number") {
    socket.send(JSON.stringify({
        type: "RESPONSE", id, success: false, payload: "tab_id required",
    }));
    return;
}

// Verify the tab still exists
let tab;
try {
    tab = await chrome.tabs.get(tabId);
} catch (err) {
    socket.send(JSON.stringify({
        type: "RESPONSE", id, success: false,
        payload: `tab not found: ${tabId}`,
    }));
    return;
}

const debugTarget = { tabId: tab.id };

if (["GET_DOM", "CLICK", "TYPE", "SELECT"].includes(type)) {
    try {
        chrome.tabs.sendMessage(tab.id, { type, payload }, (response) => {
            if (chrome.runtime.lastError) {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: false,
                    payload: chrome.runtime.lastError.message,
                }));
            } else {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id,
                    success: response.success, payload: response.payload,
                }));
            }
        });
    } catch (err) {
        socket.send(JSON.stringify({
            type: "RESPONSE", id, success: false, payload: err.toString(),
        }));
    }
} else if (type === "EXECUTE") {
    try {
        await new Promise((resolve, reject) => {
            chrome.debugger.attach(debugTarget, "1.3", () => {
                if (chrome.runtime.lastError) {
                    if (chrome.runtime.lastError.message.includes("already attached")) {
                        resolve();
                    } else {
                        reject(chrome.runtime.lastError);
                    }
                } else {
                    resolve();
                }
            });
        });
        await new Promise(resolve =>
            chrome.debugger.sendCommand(debugTarget, "Runtime.enable", {}, resolve)
        );
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
            type: "RESPONSE", id, success: false, payload: error.toString(),
        }));
    }
}
```

Note: `EXECUTE` now reads from `payload.code` instead of the whole `payload` being the JS string — matches the server's new shape.

Also: delete the `checkInitialTabs()` function and its call at the bottom of the file. It relied on the localhost:8000/8088 hardcode.

- [ ] **Step 2: Commit**

```bash
git add src/saidkick/extension/background.js
git commit -m "feat(extension): target tabs by payload.tab_id, drop localhost heuristic"
```

---

## Task 10: Python client library

**Files:**
- Modify: `src/saidkick/client.py`

No new Python tests for the client itself — it's thin HTTP wrapping and is covered by CLI integration + manual smoke. Server-side tests already cover the endpoints it calls.

- [ ] **Step 1: Rewrite `SaidkickClient`**

Full replacement of `src/saidkick/client.py`:

```python
import httpx
from typing import List, Dict, Any, Optional


class SaidkickClient:
    def __init__(self, base_url: str = "http://localhost:6992"):
        self.base_url = base_url

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

    def get_dom(
        self, tab: str, css: Optional[str] = None,
        xpath: Optional[str] = None, all_matches: bool = False,
    ) -> str:
        params: Dict[str, Any] = {"tab": tab, "all": all_matches}
        if css:
            params["css"] = css
        if xpath:
            params["xpath"] = xpath
        r = httpx.get(f"{self.base_url}/dom", params=params)
        r.raise_for_status()
        return r.json()

    def execute(self, tab: str, code: str) -> Any:
        r = httpx.post(
            f"{self.base_url}/execute", json={"tab": tab, "code": code}
        )
        r.raise_for_status()
        return r.json()

    def click(
        self, tab: str,
        css: Optional[str] = None, xpath: Optional[str] = None,
    ) -> str:
        r = httpx.post(
            f"{self.base_url}/click",
            json={"tab": tab, "css": css, "xpath": xpath},
        )
        r.raise_for_status()
        return r.json()

    def type(
        self, tab: str, text: str,
        css: Optional[str] = None, xpath: Optional[str] = None,
        clear: bool = False,
    ) -> str:
        r = httpx.post(
            f"{self.base_url}/type",
            json={
                "tab": tab, "css": css, "xpath": xpath,
                "text": text, "clear": clear,
            },
        )
        r.raise_for_status()
        return r.json()

    def select(
        self, tab: str, value: str,
        css: Optional[str] = None, xpath: Optional[str] = None,
    ) -> str:
        r = httpx.post(
            f"{self.base_url}/select",
            json={"tab": tab, "css": css, "xpath": xpath, "value": value},
        )
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 2: Commit**

```bash
git add src/saidkick/client.py
git commit -m "feat(client): required tab arg on actions, new list_tabs method"
```

---

## Task 11: CLI — `tabs` command

**Files:**
- Modify: `src/saidkick/cli.py`

- [ ] **Step 1: Add `tabs` command**

Open `src/saidkick/cli.py` and add the following command (after the existing `logs` command, before `dom`):

```python
@app.command()
def tabs(
    active: bool = typer.Option(False, "--active", help="Only list active tabs"),
):
    """List all tabs across connected browsers."""
    try:
        entries = client.list_tabs(active=active)
        if not entries:
            console.print("[warning]No tabs. Is a browser connected?[/warning]")
            return
        for e in entries:
            tab = e["tab"]
            title = e.get("title") or ""
            url = e.get("url") or ""
            marker = "  [success](active)[/success]" if e.get("active") else ""
            console.print(f"[cmd]{tab}[/cmd]  {url}  [info]\"{title}\"[/info]{marker}")
    except Exception as e:
        handle_client_error(e)
```

- [ ] **Step 2: Manual smoke — the command loads cleanly**

Run: `uv run saidkick tabs --help`
Expected: help text lists `tabs`. Exit 0.

- [ ] **Step 3: Commit**

```bash
git add src/saidkick/cli.py
git commit -m "feat(cli): add 'tabs' command with --active filter"
```

---

## Task 12: CLI — `--tab` on actions, `--browser` on logs

**Files:**
- Modify: `src/saidkick/cli.py`

- [ ] **Step 1: Rewrite action commands and `logs`**

Replace the `logs`, `dom`, `click`, `type`, `select`, and `exec` commands in `src/saidkick/cli.py`:

```python
@app.command()
def logs(
    limit: int = typer.Option(100, "--limit", "-n", help="Limit number of logs"),
    grep: str = typer.Option(None, "--grep", "-g", help="Filter logs by regex"),
    browser: str = typer.Option(None, "--browser", help="Filter to one browser_id"),
):
    """Fetch and display browser console logs."""
    try:
        logs_data = client.get_logs(limit=limit, grep=grep, browser=browser)
        for log in logs_data:
            level = log.get("level", "info").upper()
            data = log.get("data")
            bid = log.get("browser_id", "")
            console.print(f"[browser]{bid} {level}: {data}[/browser]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def dom(
    tab: str = typer.Option(..., "--tab", help="Target tab (br-XXXX:N)"),
    css: str = typer.Option(None, help="CSS selector"),
    xpath: str = typer.Option(None, help="XPath selector"),
    all_matches: bool = typer.Option(False, "--all", help="Return all matches"),
):
    """Get the current page DOM of the targeted tab."""
    try:
        result = client.get_dom(tab=tab, css=css, xpath=xpath, all_matches=all_matches)
        sys.stdout.write(str(result))
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


@app.command()
def click(
    tab: str = typer.Option(..., "--tab", help="Target tab (br-XXXX:N)"),
    css: str = typer.Option(None, help="CSS selector"),
    xpath: str = typer.Option(None, help="XPath selector"),
):
    """Click an element in the targeted tab."""
    try:
        result = client.click(tab=tab, css=css, xpath=xpath)
        console.print(f"[success]{result}[/success]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def type(
    text: str = typer.Argument(..., help="Text to type"),
    tab: str = typer.Option(..., "--tab", help="Target tab (br-XXXX:N)"),
    css: str = typer.Option(None, help="CSS selector"),
    xpath: str = typer.Option(None, help="XPath selector"),
    clear: bool = typer.Option(False, "--clear", help="Clear field before typing"),
):
    """Type text into an element in the targeted tab."""
    try:
        result = client.type(tab=tab, text=text, css=css, xpath=xpath, clear=clear)
        console.print(f"[success]{result}[/success]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def select(
    value: str = typer.Argument(..., help="Value or text to select"),
    tab: str = typer.Option(..., "--tab", help="Target tab (br-XXXX:N)"),
    css: str = typer.Option(None, help="CSS selector"),
    xpath: str = typer.Option(None, help="XPath selector"),
):
    """Select an option in a <select> element in the targeted tab."""
    try:
        result = client.select(tab=tab, value=value, css=css, xpath=xpath)
        console.print(f"[success]{result}[/success]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def exec(
    tab: str = typer.Option(..., "--tab", help="Target tab (br-XXXX:N)"),
    code: Optional[str] = typer.Argument(
        None, help="JS code to execute. Reads from stdin if not provided."
    ),
):
    """Execute JavaScript in the targeted tab."""
    if code is None:
        if sys.stdin.isatty():
            console.print(
                "[warning]Waiting for JS from stdin... (Ctrl+D to finish)[/warning]"
            )
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
            sys.stdout.write(str(result))
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)
```

- [ ] **Step 2: Manual smoke — help text loads**

Run: `uv run saidkick --help`
Expected: lists `start, logs, tabs, dom, click, type, select, exec`. Each action command's help shows `--tab` as required.

Run: `uv run saidkick dom --help | grep -i "tab"`
Expected: output contains the `--tab` option with `br-XXXX:N` in the help string.

- [ ] **Step 3: Commit**

```bash
git add src/saidkick/cli.py
git commit -m "feat(cli): --tab required on actions, --browser filter on logs"
```

---

## Task 13: Docs and version bump

**Files:**
- Create: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: `docs/user-guide.md`, `docs/design.md`

- [ ] **Step 1: Bump version**

Edit `pyproject.toml` — change `version = "0.1.0"` to `version = "0.2.0"`.

- [ ] **Step 2: Create CHANGELOG**

Create `CHANGELOG.md`:

```markdown
# Changelog

All notable changes to this project are documented here. Format: Keep a Changelog.

## [Unreleased]

## [0.2.0] - 2026-04-21

### BREAKING CHANGES

- All action endpoints (`/dom`, `/execute`, `/click`, `/type`, `/select`) now require a `tab` parameter in the form `br-XXXX:N` (query param on GET, body field on POST).
- All CLI action commands (`dom`, `click`, `type`, `select`, `exec`) now require a `--tab br-XXXX:N` flag.
- `SaidkickClient` action methods now require `tab` as their first positional argument.
- Extension ↔ server protocol adds a `HELLO` handshake frame; the extension must be reinstalled from this version of the repo.
- The old tab-selection heuristic inside `background.js` (active tab → localhost:8000/8088 → first non-chrome tab) has been removed.

### Features

- `GET /tabs` endpoint aggregates tabs across all connected browsers. Optional `?active=true` filter.
- `saidkick tabs` CLI command; `--active` filter for the currently-focused tab.
- Multi-browser support: server assigns an ephemeral `br-XXXX` ID on each WS connection and tracks them in a dict, keyed by `browser_id`.
- `/console` and `saidkick logs` support a `browser` / `--browser` filter. Every stored log entry is stamped with its source `browser_id`.

## [0.1.0]

- Initial release: FastAPI server, Chrome MV3 extension, Typer CLI, Python client for remote browser inspection and automation.
```

- [ ] **Step 3: Update `docs/user-guide.md`**

Open `docs/user-guide.md`. Anywhere that shows a CLI command or REST call without a `tab` parameter, update it to pass `--tab br-XXXX:N`. Add a new first section before the existing usage examples:

```markdown
## Identifying a tab

Every command operates on a specific tab, addressed as `br-XXXX:N` (browser ID + Chrome tab ID). List what's available:

    $ saidkick tabs
    br-a1b2:12  https://example.com/  "Example Domain"  (active)
    br-a1b2:15  https://docs.python.org/  "Python 3.12 Docs"

To grab the currently-focused tab for scripting:

    $ TAB=$(saidkick tabs --active | awk '{print $1}' | head -1)
    $ saidkick dom --tab "$TAB" --css "h1"
```

- [ ] **Step 4: Update `docs/design.md`**

In `docs/design.md`, update the "Communication Protocol" section. Below the existing message example, add:

```markdown
### Handshake

Immediately after the WebSocket connects, the server sends a HELLO frame:

    { "type": "HELLO", "browser_id": "br-a1b2" }

The extension stores this ID in its service-worker memory. Subsequent commands from the server include a `tab_id` in their payload; the extension uses `chrome.tabs.get(tab_id)` directly — no heuristic selection.

### Server-side state

The `SaidkickManager` holds connections in a `Dict[str, WebSocket]` keyed by `browser_id`. Every action endpoint parses the caller's `tab` parameter (`br-XXXX:N`) at the boundary, routes the command to `connections[browser_id]`, and passes `tab_id: N` through to the extension.
```

- [ ] **Step 5: Run full test suite once more**

Run: `uv run pytest tests/ -v --ignore=tests/test_saidkick_e2e.py`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml CHANGELOG.md docs/user-guide.md docs/design.md
git commit -m "docs: document tab management, bump to 0.2.0, add CHANGELOG"
```

---

## Task 14: Manual end-to-end smoke test

No automation; this is a verification checklist. Run each step and confirm before marking done. If any fails, file a followup.

- [ ] **Step 1: Reinstall the extension**

In Chrome: `chrome://extensions/` → Developer mode on → Load unpacked → point at `src/saidkick/extension/`. Reload if already installed.

- [ ] **Step 2: Start the server**

Run: `uv run saidkick start`
Expected: uvicorn output includes `Browser connected: br-XXXX` within a few seconds (the extension auto-connects).

- [ ] **Step 3: List tabs**

In another terminal: `uv run saidkick tabs`
Expected: one line per open non-chrome tab, prefixed with the same `br-XXXX:` you saw in the server log.

- [ ] **Step 4: Filter active**

Run: `uv run saidkick tabs --active`
Expected: at least one entry, marked `(active)`.

- [ ] **Step 5: Action against a specific tab**

Pick a tab ID from `tabs` output (e.g., `br-a1b2:15`). Run:

```bash
TAB=br-a1b2:15   # substitute your value
uv run saidkick dom --tab "$TAB" --css "h1"
```

Expected: the `<h1>…</h1>` of that tab's page.

- [ ] **Step 6: Execute JS**

```bash
echo "document.title" | uv run saidkick exec --tab "$TAB"
```

Expected: the page title, printed to stdout.

- [ ] **Step 7: Two-browser check (optional, if convenient)**

Open a second Chrome profile and install the same unpacked extension. Run `saidkick tabs` again.
Expected: two distinct `br-XXXX` prefixes visible, each with their own tabs.

- [ ] **Step 8: Update journal**

Append to `vault/Calendar/Journal/journal-2026-04-21.md`:

```
> 🤖 HH:MM — milestone: saidkick tab management landed, 0.2.0 tagged locally (not released). Manual smoke passed.
```

- [ ] **Step 9: Final workspace-level commit — update repo node**

Back in the workspace repo (`/home/apiad/Workspace`), update `vault/Efforts/Repos/saidkick.md`:
- Strike-through the Tier-1 item "tab management" (or move it to a "done in 0.2.0" subsection).
- Update `last_sync` in frontmatter to the current ISO timestamp.
- Commit: `chore(repos): sync saidkick after tab management landed`.

---

## Self-Review

- **Spec coverage.** Every spec requirement maps to a task:
  - Data model (Dict, ephemeral IDs) → Task 2.
  - Handshake (HELLO) → Task 3 (server) + Task 7 (extension).
  - `GET /tabs` with `active` filter → Task 4.
  - Action endpoints require `tab` → Task 5.
  - Extension LIST_TABS handler → Task 8.
  - Extension removes heuristic, uses `payload.tab_id` → Task 9.
  - Client library changes → Task 10.
  - CLI changes → Tasks 11–12.
  - Log tagging + `/console?browser=` → Task 6 (server) + `handle_log` change in Task 3.
  - Error taxonomy (400/404/502/504) → Tasks 4–5 (tests cover the main cases).
  - Version bump + CHANGELOG + docs → Task 13.
  - Manual smoke → Task 14.

- **Placeholder scan.** No "TBD", no vague "add error handling" — every step has concrete code or a concrete command.

- **Type consistency.** `browser_id` is a `str`, `tab_id` is an `int`, composite is a `str` parsed by `parse_tab_id`. `send_command(browser_id, command_type, payload)` signature is used consistently from Task 4 onward. `connections: Dict[str, WebSocket]` name is consistent.

- **Gap found and filled during review.** The `handle_log` signature change (taking `browser_id`) is introduced in Task 3 alongside the handshake, because that's where we first know the `browser_id` at log-receipt time. The filter endpoint in Task 6 consumes the stamped field.
