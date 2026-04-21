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

### `saidkick click`
Click an element on the page.
- `--tab` (required): Target tab (`br-XXXX:N`).
- `--css`: CSS selector.
- `--xpath`: XPath selector.

### `saidkick type`
Type text into an input field.
- `text`: (Argument) The text to type.
- `--tab` (required): Target tab (`br-XXXX:N`).
- `--css`: CSS selector.
- `--xpath`: XPath selector.
- `--clear`: Clear the field before typing.

### `saidkick select`
Select an option in a `<select>` element.
- `value`: (Argument) The value or text of the option to select.
- `--tab` (required): Target tab (`br-XXXX:N`).
- `--css`: CSS selector.
- `--xpath`: XPath selector.

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
| `/dom` | `GET` | Get page HTML. Query: `tab` (required), `css`, `xpath`, `all`. |
| `/execute` | `POST` | Execute JS. JSON body: `{"tab": "...", "code": "..."}`. |
| `/click` | `POST` | Click element. JSON body: `{"tab": "...", "css": "...", "xpath": "..."}`. |
| `/type` | `POST` | Type text. JSON body: `{"tab": "...", "css": "...", "xpath": "...", "text": "...", "clear": bool}`. |
| `/select` | `POST` | Select option. JSON body: `{"tab": "...", "css": "...", "xpath": "...", "value": "..."}`. |

### Error codes

- `400` — malformed `tab` (must match `br-XXXX:N`).
- `404` — unknown `browser_id` (no browser is currently connected under that ID).
- `422` — missing required field (e.g., `tab` or `code`).
- `500` — extension returned a command failure (e.g., selector not found).
- `503` — extension WebSocket send failure.
- `504` — extension did not respond within 10 seconds.
