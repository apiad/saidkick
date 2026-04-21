# Saidkick User Guide

This guide provides a detailed reference for all the ways you can interact with Saidkick: via the CLI, the Python client, or the direct REST API.

## Identifying a tab

Every command operates on a specific tab, addressed as `br-XXXX:N` (browser ID + Chrome tab ID). The browser ID is assigned by the server when the extension connects; the tab ID is Chrome's native `tab.id` integer.

List what's available:

```
$ saidkick tabs
br-a1b2:12  https://example.com/  "Example Domain"  (active)
br-a1b2:15  https://docs.python.org/  "Python 3.12 Docs"
```

To grab the currently-focused tab for scripting:

```
$ TAB=$(saidkick tabs --active | awk '{print $1}' | head -1)
$ saidkick dom --tab "$TAB" --css "h1"
```

All IDs are ephemeral: reconnecting the extension yields a new `br-XXXX`, and Chrome assigns new tab integers on browser restart. Always list fresh before addressing.

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

## 1. CLI Reference

The Saidkick CLI (`saidkick`) is the primary way to interact with the browser from your terminal.

### `saidkick start`
Starts the central FastAPI server.
- `--host`: Bind address (default: `0.0.0.0`).
- `--port`: Port number (default: `6992`).
- `--reload`: Auto-reload for development.

### `saidkick tabs`
List tabs across all connected browsers.
- `--active`: Filter to only the active (focused) tab(s).

### `saidkick logs`
Fetch and display console logs from the connected browsers.
- `--limit` (`-n`): Limit the number of logs (default: 100).
- `--grep` (`-g`): Filter logs by regex.
- `--browser`: Filter to logs from one `br-XXXX` browser.

### `saidkick dom`
Get the current page's DOM or a specific element.
- `--tab` (required): Target tab (`br-XXXX:N`).
- `--css`: Target a specific element with a CSS selector.
- `--xpath`: Target a specific element with an XPath selector.
- `--all`: Return all matches (concatenated by newlines).
- `--wait-ms`: Poll up to N ms for the selector to resolve before acting (default 0 = fail immediately).

### `saidkick text`
Print the readable (innerText) content of a tab.
- `--tab` (required): Target tab (`br-XXXX:N`).
- `--css`: Restrict to the innerText of the matched element (first match).
- `--wait-ms`: Poll up to N ms for the CSS selector.

### `saidkick click`
Click an element on the page.
- `--tab` (required): Target tab (`br-XXXX:N`).
- `--css`: CSS selector.
- `--xpath`: XPath selector.
- `--wait-ms`: Poll up to N ms for the selector to resolve before acting.

### `saidkick type`
Type text into an input field.
- `text`: (Argument) The text to type.
- `--tab` (required): Target tab (`br-XXXX:N`).
- `--css`: CSS selector.
- `--xpath`: XPath selector.
- `--clear`: Clear the field before typing.
- `--wait-ms`: Poll up to N ms for the selector to resolve before acting.

### `saidkick select`
Select an option in a `<select>` element.
- `value`: (Argument) The value or text of the option to select.
- `--tab` (required): Target tab (`br-XXXX:N`).
- `--css`: CSS selector.
- `--xpath`: XPath selector.
- `--wait-ms`: Poll up to N ms for the selector to resolve before acting.

### `saidkick navigate`
Send the targeted tab to a URL. Prints the final URL (after redirects).
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

### `saidkick exec`
Execute arbitrary JavaScript and return the result as JSON.
- `code`: (Optional argument) The JS code to run. If not provided, reads from stdin.
- `--tab` (required): Target tab (`br-XXXX:N`).
- **Example**: `echo "document.title" | saidkick exec --tab br-a1b2:12`

---

## 2. Programmatic Python Client

You can use `SaidkickClient` in your own Python projects.

```python
from saidkick.client import SaidkickClient

client = SaidkickClient(base_url="http://localhost:6992")

# Pick a tab
tabs = client.list_tabs(active=True)
tab = tabs[0]["tab"]  # e.g. "br-a1b2:12"

# Get page title
title = client.execute(tab, "document.title")
print(f"Title: {title}")

# Interaction example
client.type(tab, "Saidkick", css="#search", clear=True)
client.click(tab, css="#search-button")

# Fetch logs
logs = client.get_logs(limit=10, grep="error", browser="br-a1b2")
for log in logs:
    print(log["data"])
```

---

## 3. REST API Reference

The server exposes the following endpoints (default base URL: `http://localhost:6992`).

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

### Error codes

- `400` — malformed input (bad `tab` or `browser` ID, invalid URL, ambiguous selector, wrong element type for command).
- `404` — referenced resource not found (browser not connected, tab not found, element not found after any `wait_ms`, select option missing).
- `422` — Pydantic validation failure (missing required field, invalid `wait` mode).
- `502` — upstream (browser) error we can't classify (e.g., content-script injection failed, unrecognized `chrome.runtime.lastError`).
- `504` — timeout (command response, navigation, selector never resolved within `wait_ms`).
- `500` — server bug. Reserved; seeing one is a defect report.
