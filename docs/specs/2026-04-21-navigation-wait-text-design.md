# Navigation, Wait-for-Element, Readable Text, and Error Cleanup — Design

**Status:** approved, ready for implementation plan
**Date:** 2026-04-21
**Version target:** 0.3.0 (additive — no breaking changes for existing 0.2.0 callers, error-code changes are a defect fix)

## Problem

After 0.2.0 (tab management), saidkick can address specific tabs, but it cannot:

- **Send a tab to a URL** (`navigate`) or **create a new tab** (`open`). Today you can only operate on whatever's already open. Rules out scripting a flow from scratch.
- **Survive async DOM changes.** `dom`/`click`/`type`/`select` require the selector to resolve on the first query. Any SPA, lazy render, or modal-after-transition is a coin flip.
- **Return the readable content of a page.** `dom` gives raw HTML; there's no "just tell me what the page says."

Also, 0.2.0 misuses HTTP `500` for client-observable error conditions (`"Element not found"`, `"Ambiguous selector"`, etc.). Policy should reserve 5xx for bugs and use 4xx for caller-resolvable failures. Fix as part of this chunk while we're adding new surface.

## Goals

- Add `navigate`, `open`, and `text` — one endpoint each, one CLI command each, one client method each.
- Add a shared `wait_ms` option to every selector-using command (`dom`, `click`, `type`, `select`, and the new `text`).
- Rewrite the server's error taxonomy: `500` reserved for actual bugs; `400`/`404`/`502`/`504` cover everything else. Apply to old endpoints too.

## Non-goals

Deferred: tab close, back/forward, reload, focus-switch/activate-existing-tab, `networkidle` or arbitrary predicate waits, Mozilla Readability-style cleanup. Out entirely: keyboard events, scroll, screenshots, file upload, `find` by text/role (those are separate Tier 2 items).

## Error taxonomy (server-wide)

Applies to every endpoint, new and existing.

| Situation | HTTP | Message pattern |
|---|---|---|
| Malformed input (tab ID, URL, wrong element type for command, ambiguous selector with ≥2 matches) | 400 | same as today for tab ID; new messages otherwise |
| Missing required field | 422 | Pydantic default, unchanged |
| Resource not found (browser not connected, tab not found, element not found, select option missing) | 404 | `"browser not connected: br-XXXX"`, `"tab not found in br-XXXX: N"`, `"element not found"`, `"option not found: X"` |
| Upstream browser error we can't classify (WS send failed, content-script injection failed after retry, unknown `chrome.runtime.lastError`) | 502 | `"browser send failed: {reason}"`, `"content script injection failed: {reason}"` |
| Timeout (command response, navigation, selector wait) | 504 | `"browser response timeout"`, `"navigation timeout after {N}ms"`, `"selector not resolved within {N}ms"` |
| Server bug | 500 | reserved; intentional use is a code smell |

**Implementation.** The server's current pattern `if not response.get("success"): raise HTTPException(500, ...)` is replaced by a helper that classifies the extension's error message against a small set of keywords:

```python
def _raise_for_extension_error(payload: str) -> None:
    """Map a failure string from content.js / background.js into an HTTPException."""
    m = (payload or "").lower()
    # 404: resource not found (element, option, tab)
    if ("element not found" in m
        or "option not found" in m
        or "tab not found" in m):
        raise HTTPException(404, payload)
    # 400: caller-resolvable input problem
    if ("ambiguous selector" in m
        or "element is not a" in m
        or "no selector provided" in m
        or "invalid url" in m):
        raise HTTPException(400, payload)
    # 504: timeout
    if "timeout" in m or "not resolved within" in m:
        raise HTTPException(504, payload)
    # Default: upstream error we don't recognize — 502
    raise HTTPException(502, payload)
```

Extension-side message strings are ours, so keyword matches are deterministic. If noise emerges later, upgrade to structured error codes.

## Feature 1 — Navigation

### `POST /navigate`

Body:

```json
{
  "tab": "br-XXXX:N",
  "url": "https://example.com/",
  "wait": "dom" | "full" | "none",
  "timeout_ms": 15000
}
```

`wait` default `"dom"`. `timeout_ms` default 15000.

Response (200): `{"url": "<final-url-after-redirects>"}`.

Server flow: parse tab; validate `url` is a well-formed HTTP(S) URL (else 400); `send_command(browser_id, "NAVIGATE", {tab_id, url, wait, timeout_ms})`. Extension does the work.

Extension flow (`background.js`):

1. `await chrome.tabs.update(tabId, {url})`. Chrome validates the URL; thrown error → `{success: false, payload: "invalid url"}` → 400.
2. If `wait === "none"`, reply immediately with the current `tab.url`.
3. Wait for the chosen event within `timeout_ms`, using `chrome.debugger` for precision (`chrome.tabs.onUpdated` conflates DOMContentLoaded and load — both land at `status === "complete"`, which is wrong for our `dom` semantics). Attach the debugger (reusing existing EXECUTE attach-idempotency logic), enable the `Page` domain, and register `chrome.debugger.onEvent` listeners:
   - `"dom"` resolves on the first `Page.domContentLoaded` event after the navigation.
   - `"full"` resolves on the first `Page.loadEventFired` event.
   - The debugger stays attached after resolution (consistent with EXECUTE's existing behavior).
4. On event → reply `{success: true, payload: {url: updatedTab.url}}`.
5. On timeout → reply `{success: false, payload: "navigation timeout after {N}ms"}` → 504 via the new classifier.

### CLI

`saidkick navigate --tab ID URL [--wait dom|full|none] [--timeout-ms N]`

Prints the final URL on success. Exits non-zero on any 4xx/5xx.

## Feature 2 — Open new tab

### `POST /open`

Body:

```json
{
  "browser": "br-XXXX",
  "url": "https://example.com/",
  "wait": "dom" | "full" | "none",
  "timeout_ms": 15000,
  "activate": false
}
```

Validation: `browser` must match `^br-[0-9a-f]{4}$` (400 if not). `url` validated as in navigate.

Response (200): `{"tab": "br-XXXX:N", "url": "<final-url>"}`.

Server: `send_command(browser_id, "OPEN", {url, wait, timeout_ms, activate})`. Note the payload has no `tab_id` — that's the whole point.

Extension:

1. `const tab = await chrome.tabs.create({url, active: activate})`. Thrown/rejected → `{success: false, payload: "tab create failed: {reason}"}` → 502.
2. If `wait === "none"`, reply `{success: true, payload: {tab_id: tab.id, url: tab.url}}`.
3. Otherwise reuse the same wait machinery as navigate, scoped to the new `tab.id`, up to `timeout_ms`.
4. Reply with the final URL.

Server wraps the returned `tab_id` into the composite: `{"tab": f"{browser_id}:{tab_id}", "url": url}`.

### CLI

`saidkick open --browser BR URL [--wait dom|full|none] [--timeout-ms N] [--activate]`

Prints the composite `br-XXXX:N` on success (one token, newline-terminated). This is the sole stdout output so `TAB=$(saidkick open --browser BR URL)` works without filtering.

## Feature 3 — Wait-for-element on selector commands

### Protocol change

All command payloads for `GET_DOM`, `CLICK`, `TYPE`, `SELECT`, and the new `GET_TEXT` gain an optional `wait_ms: int` field (default `0`).

### Content-script helper

In `content.js`, new internal helper:

```javascript
async function waitForSelector(css, xpath, waitMs) {
    const start = Date.now();
    const POLL = 100;
    while (true) {
        let matches;
        try {
            matches = collectMatches(css, xpath);  // existing CSS+XPath resolution
        } catch (e) {
            matches = [];
        }
        if (matches.length === 1) return matches[0];
        if (matches.length > 1) throw new Error(`Ambiguous selector: found ${matches.length} matches`);
        // matches.length === 0
        if (Date.now() - start >= waitMs) {
            throw new Error("element not found");
        }
        await new Promise(r => setTimeout(r, POLL));
    }
}
```

`wait_ms === 0` is not a "poll once with zero wait" — it's "poll exactly once, fail immediately if not resolved." Preserves today's behavior when callers don't opt in.

Existing `findElement` in `content.js` gets replaced by `await waitForSelector(css, xpath, payload.wait_ms || 0)`. Command handlers become async to accommodate.

`GET_DOM` uses the same helper — when `wait_ms > 0` and the selector returns nothing, polls until it does or times out. When `all === true`, the helper is different: we want "any matches" rather than "exactly one" — add a second helper `waitForAnyMatches(css, xpath, waitMs)` that returns as soon as `matches.length >= 1` or times out.

### Timeout semantics

Extension returns `success: false, payload: "selector not resolved within {N}ms"` on timeout. Server classifier maps to 504.

The ambient 10s server-side `send_command` timeout is too short for long waits. Bump it: the server should use `max(10.0, (wait_ms + timeout_ms + 2000) / 1000)` as its `asyncio.wait_for` timeout, so it doesn't prematurely 504 a command that the extension is legitimately polling.

### CLI/client surface

Every selector-using command gains `--wait-ms N`:

- `saidkick dom --tab ID --css X --wait-ms 5000`
- `saidkick click --tab ID --css X --wait-ms 5000`
- `saidkick type TEXT --tab ID --css X --wait-ms 5000 [--clear]`
- `saidkick select VALUE --tab ID --css X --wait-ms 5000`
- `saidkick text --tab ID [--css SCOPE] --wait-ms 5000`  (Feature 4 below)

REST: same `wait_ms` query param / body field. Default 0.

Python client methods gain a `wait_ms: int = 0` kwarg.

### Not a separate `wait` command

Explicitly YAGNI. If you want "block until X appears," do `saidkick dom --tab ID --css X --wait-ms 5000 > /dev/null && saidkick click ...`. The `--wait-ms` option on `dom` itself covers the sentinel-poll case.

## Feature 4 — Readable text

### `GET /text`

Query params: `tab` (required, `br-XXXX:N`), `css` (optional scope), `wait_ms` (optional, default 0).

Response (200): JSON string of the `innerText`.

Server: `send_command(browser_id, "GET_TEXT", {tab_id, css, wait_ms})`.

Extension (content.js new handler):

1. If `css` provided, `element = await waitForSelector(css, null, wait_ms)`; else `element = document.body` (with `wait_ms > 0` implying "wait for body to have children"? — YAGNI; `document.body` always exists once content.js runs).
2. Return `element.innerText`.

`innerText` preserves visible text reasonably well (respects CSS display, skips hidden elements) without requiring a library bundle. Mozilla Readability-style article extraction is out of scope — if we want that later, add a separate `saidkick article --tab ID` command.

### CLI

`saidkick text --tab ID [--css SCOPE] [--wait-ms N]`

Prints the text to stdout. No trailing newline munging — we emit `innerText` verbatim plus a trailing `\n`, matching `dom`.

No XPath option — CSS is the conventional scope mechanism for "show me this region"; adding XPath to this one command for parity is over-cautious.

## Server changes — summary

- New endpoints: `POST /navigate`, `POST /open`, `GET /text`.
- Existing endpoints gain `wait_ms` field on request bodies / query strings.
- Error classifier helper `_raise_for_extension_error` replaces every `raise HTTPException(500, ...)`.
- `send_command` grows a per-request timeout override so long-wait commands don't trip the 10s default.

## Extension changes — summary

- `background.js`:
  - New `NAVIGATE` command handler — `chrome.tabs.update` + await-load via `chrome.tabs.onUpdated` or `Page.domContentLoaded`.
  - New `OPEN` command handler — `chrome.tabs.create` + same wait logic.
  - Debugger attach/detach logic factored into a helper (it's used by EXECUTE already; `dom` navigation wait will share it).
- `content.js`:
  - `waitForSelector` and `waitForAnyMatches` helpers.
  - `findElement` replaced everywhere by `waitForSelector` with `wait_ms` passed through.
  - New `GET_TEXT` handler.

`main_world.js`, `popup.html`, `popup.js`, `manifest.json` — unchanged.

## Python client — summary

```python
class SaidkickClient:
    def navigate(self, tab: str, url: str, wait: str = "dom", timeout_ms: int = 15000) -> dict: ...
    def open(self, browser: str, url: str, wait: str = "dom", timeout_ms: int = 15000,
             activate: bool = False) -> dict: ...
    def text(self, tab: str, css: Optional[str] = None, wait_ms: int = 0) -> str: ...

    # Existing methods gain wait_ms
    def get_dom(self, tab: str, ..., wait_ms: int = 0) -> str: ...
    def click(self, tab: str, ..., wait_ms: int = 0) -> str: ...
    def type(self, tab: str, text: str, ..., wait_ms: int = 0) -> str: ...
    def select(self, tab: str, value: str, ..., wait_ms: int = 0) -> str: ...
```

## CLI — summary

New commands: `navigate`, `open`, `text`. Existing commands `dom`/`click`/`type`/`select` gain `--wait-ms`. No other changes.

## Testing

### Unit / integration (pytest, existing style)

- **Error classifier:** table-driven tests for `_raise_for_extension_error` across the message set we emit.
- **Updated error codes:** the existing 0.2.0 endpoints now expect `404` (not found), `400` (ambiguous/wrong-type), `502` (upstream) instead of `500`. Update `test_saidkick_enhanced.py` / any test that asserts on codes.
- **`/navigate`:** mock `send_command` and assert the payload carries `tab_id`, `url`, `wait`, `timeout_ms`. Malformed URL → 400. Unknown browser → 404. Timeout from extension → 504.
- **`/open`:** similar, plus: server wraps the extension's `tab_id` into the composite `tab` field in the response.
- **`wait_ms` propagation:** existing endpoints carry `wait_ms` through to the extension payload.
- **`send_command` timeout override:** with a high `wait_ms`, the server's `asyncio.wait_for` budget expands; a mock extension that responds after 15s does not trigger the server's own 504 when `wait_ms >= 15000`.
- **`/text`:** mock returns a string, asserts payload shape includes `css`, `wait_ms`.

### Extension (no unit tests; manual smoke)

Each new command gets a smoke line in the user-guide manual checklist:

- `saidkick navigate --tab ID https://example.com/` returns the final URL.
- `saidkick open --browser BR https://example.com/` prints a composite that's immediately usable: `TAB=$(saidkick open --browser BR URL); saidkick dom --tab "$TAB" --css h1`.
- `saidkick click --tab ID --css ".button-that-appears-late" --wait-ms 3000` on an SPA that lazy-renders the button.
- `saidkick text --tab ID` on a news article — returns stripped text.

## Success criteria

1. Full pytest suite green after the error-code updates.
2. Manual smoke: each of the four new affordances exercised against a real Chrome + extension.
3. A 0.2.0 caller that doesn't opt into `wait_ms` continues to behave identically (no silent waits, same error messages where preserved).
4. `CHANGELOG.md` flags the error-code shift as a "defect fix" (not a breaking change from the user's perspective — no one was relying on 500 responses) plus the additive feature set.
