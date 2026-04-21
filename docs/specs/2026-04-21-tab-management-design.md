# Tab Management — Design

**Status:** approved, ready for implementation plan
**Date:** 2026-04-21
**Version target:** 0.2.0 (breaking)

## Problem

Saidkick v0.1.0 has no tab model. The server's `active_connections` is a list of WebSockets; commands silently hit `active_connections[-1]`. Tab selection happens inside `background.js` via a hardcoded heuristic (active non-chrome tab → localhost:8000/8088 → first non-chrome tab). This means:

- Commands are non-deterministic whenever more than one tab is open.
- Multiple browser instances (multi-profile, multi-device) silently clobber each other.
- Callers can't list what's connected or target a specific tab.

## Goals

- Address browsers and tabs explicitly, every command.
- Enumerate what's available (`saidkick tabs`) and identify the "currently looking at" tab (`saidkick tabs --active`).
- Design for multi-browser from day one — the `browser_id` dimension is first-class.
- Keep the extension, server, client, and CLI in lockstep; no drift.

## Non-goals

Deferred to later Tier-1 chunks: tab creation, `navigate` to URL, `wait-for-element`. Deferred indefinitely: tab closing, focus switching, persistent IDs across browser restarts.

## Data model

### Browser ID

A string of the form `br-XXXX`, where `XXXX` is 4 hex characters (`[0-9a-f]{4}`). ~65k ID space; collision at reassignment time is handled by regenerating until free.

- **Ephemeral.** Generated server-side on WS handshake. Forgotten on disconnect. Reconnect → new ID.
- **Server-authoritative.** The extension does not propose an ID; the server assigns and tells the extension.

### Tab composite ID

A string of the form `br-XXXX:N`, where `N` is Chrome's native `tab.id` integer. Parsed at endpoint boundaries into `(browser_id: str, tab_id: int)`.

Format regex: `^br-[0-9a-f]{4}:\d+$`. Malformed → 400 Bad Request.

### Server state

```python
# saidkick/server.py
connections: Dict[str, WebSocket] = {}          # browser_id -> ws
pending_requests: Dict[str, asyncio.Future] = {}  # request_id -> future (unchanged)
logs: deque = deque(maxlen=100)                 # tagged with browser_id (see below)
```

### Log entries

Existing shape: `{level, data, timestamp, url}`. Add `browser_id` field, stamped by the server at receive time (from the WS the message arrived on). No change to extension-side log shape.

## Handshake protocol

1. Extension opens WS to `ws://localhost:6992/ws`.
2. Server `websocket.accept()`.
3. Server generates `browser_id` (retry on collision), stores `connections[browser_id] = ws`.
4. Server sends first frame: `{"type": "HELLO", "browser_id": "br-a1b2"}`.
5. Extension stores ID in service-worker module scope.
6. Extension emits one `console.log`-level notice (via normal log channel): `"Saidkick: connected as br-a1b2"`.

Existing `log`/`RESPONSE` message handling on the WS continues unchanged.

## REST API

### New

- `GET /tabs` — list all tabs across all connected browsers.
  - Query params: `active` (bool, default false): filter to active tabs only.
  - Response: `[{"tab": "br-XXXX:N", "browser_id": "br-XXXX", "tab_id": N, "url": "...", "title": "...", "active": bool, "windowId": N}, ...]`
  - Server implementation: for each connection in `connections.values()`, send `LIST_TABS`, await response, aggregate, stamp each with its `browser_id`.
  - Empty connection set → empty list (not an error).

### Modified — all action endpoints gain a required `tab` field

- `GET /dom?tab=br-XXXX:N&css=...` (query param).
- `POST /execute` body: `{"tab": "br-XXXX:N", "code": "..."}`.
- `POST /click` body: `{"tab": "...", "css": ..., "xpath": ...}`.
- `POST /type` body: `{"tab": "...", "text": ..., "css": ..., "xpath": ..., "clear": bool}`.
- `POST /select` body: `{"tab": "...", "value": ..., "css": ..., "xpath": ...}`.

Missing `tab` → 400. Malformed `tab` → 400. Unknown `browser_id` → 404. Tab not found within that browser → 502 with extension's error message.

### Modified — console filter

- `GET /console?browser=br-XXXX&limit=...&grep=...`
- `browser` is optional; omitted = all browsers.

## Extension changes

### `background.js`

- Add module-scope `let browserId = null;`.
- On WS message where `type === "HELLO"`: `browserId = message.browser_id;`, emit one info log.
- Add command handler for `type === "LIST_TABS"`:
  - `const tabs = await chrome.tabs.query({});`
  - Filter: drop any `chrome://`, `chrome-extension://`, `devtools://` URL entries.
  - Return `[{id: t.id, url: t.url, title: t.title, active: t.active, windowId: t.windowId}]`.
- Remove the tab-selection heuristic (active → localhost:8000 → fallback) and `checkInitialTabs()`.
- All existing handlers (`GET_DOM`, `CLICK`, `TYPE`, `SELECT`, `EXECUTE`) now require `payload.tab_id`. If missing, respond with `{success: false, payload: "tab_id required"}`. Otherwise, use that tab_id directly as the target. If `chrome.tabs.get(tab_id)` fails, respond with `{success: false, payload: "tab not found: " + tab_id}`.

### `content.js`, `main_world.js`

No changes. The content script listens via `chrome.runtime.onMessage`, which is already tab-scoped — `chrome.tabs.sendMessage(tabId, ...)` from background delivers to that tab's content script, which is exactly the behavior we want.

## Python client changes

```python
class SaidkickClient:
    def list_tabs(self, active: bool = False) -> List[Dict]: ...
    def get_logs(self, limit: int = 100, grep: Optional[str] = None,
                 browser: Optional[str] = None) -> List[Dict]: ...
    def get_dom(self, tab: str, css: Optional[str] = None,
                xpath: Optional[str] = None, all_matches: bool = False) -> str: ...
    def execute(self, tab: str, code: str) -> Any: ...
    def click(self, tab: str, css: Optional[str] = None, xpath: Optional[str] = None) -> str: ...
    def type(self, tab: str, text: str, ...) -> str: ...
    def select(self, tab: str, value: str, ...) -> str: ...
```

`tab` is positional-first on every action method. Breaking change; bump major-version-ish (pre-1.0 → 0.2.0).

## CLI changes

- New: `saidkick tabs [--active]` — prints one tab per line: `br-XXXX:N  URL  "TITLE"  [active]`. `--active` filters. Output format is deliberately pipe-friendly.
- All action commands gain a required `--tab BR-XXXX:N` flag: `dom`, `click`, `type`, `select`, `exec`.
- `logs` gains optional `--browser BR-XXXX` filter.
- `start` unchanged.

CLI help text shows the composite format in examples.

## Error taxonomy

| Situation | HTTP | Message |
|---|---|---|
| `tab` missing on action endpoint | 400 | `"tab is required"` |
| `tab` malformed (bad format) | 400 | `"invalid tab ID: expected 'br-XXXX:N'"` |
| Unknown `browser_id` | 404 | `"browser not connected: br-XXXX"` |
| Tab ID not found in that browser | 502 | `"tab not found in br-XXXX: N"` |
| Zero browsers connected + `/tabs` | 200 | `[]` (empty list, not an error) |
| Zero browsers connected + action | 404 | `"browser not connected: br-XXXX"` (same as unknown) |
| WS disconnect during in-flight | 503 | `"browser disconnected mid-request"` |
| Extension response timeout | 504 | `"browser response timeout"` (existing) |

## Testing

### Unit

- `parse_tab_id("br-a1b2:42") → ("br-a1b2", 42)`. Malformed inputs raise `ValueError`.
- `SaidkickManager.generate_browser_id()` avoids collisions against existing `connections`.
- `SaidkickManager.send_command(browser_id, ...)` routes to the right WS; raises on unknown `browser_id`.
- Log-receive path stamps `browser_id` on stored entries.

### Integration (FastAPI TestClient + mock WS)

Fixture: a pytest-asyncio fixture that opens a WS connection to the app, awaits `HELLO`, exposes a `respond(request_id, payload)` helper so tests can simulate extension responses. Tests:

- `GET /tabs` with zero connections returns `[]`.
- `GET /tabs` with one mock extension returns aggregated list with `browser_id` stamped.
- `GET /tabs?active=true` filters correctly.
- `GET /tabs` with two mock extensions aggregates across both.
- `GET /dom?tab=br-xxxx:42&css=...` routes to the right mock.
- Malformed `tab` → 400. Unknown `browser_id` → 404.
- `/console` receives logs stamped with `browser_id`; `?browser=` filter works.

### E2E

`e2e` marker exists in `pyproject.toml`. Leave real-browser tests for a later chunk; this one ships with unit + integration coverage.

## Breaking changes

- All REST action endpoints require `tab`.
- All CLI action commands require `--tab`.
- Extension protocol: adds `HELLO`, adds `LIST_TABS`, removes tab-selection heuristic.
- No migration shim. Version bump to 0.2.0. Changelog entry flagged as BREAKING.

## Open implementation questions

None blocking. The plan step will decide: file layout for tests (new `tests/test_tabs.py` vs extending an existing file), whether the mock-extension fixture lives in `conftest.py` or a dedicated helper module, and the exact CLI output format for `tabs` (tab-delimited vs space-padded). Those are tactical choices for the plan.

## Success criteria

1. All unit + integration tests pass.
2. Manual smoke: install the updated extension into Chrome, `saidkick start` in a terminal, `saidkick tabs` lists every open (non-chrome) tab prefixed with the browser ID. `saidkick tabs --active` narrows to the active ones. `saidkick dom --tab <composite> --css body` returns the body of the targeted tab.
3. Two simultaneous Chrome profiles produce two distinct `br-XXXX` entries in `saidkick tabs`; commands hit the right one.
4. Killing one Chrome profile silently drops its entry from `saidkick tabs` within one poll.
