# Saidkick User Guide

This guide provides a detailed reference for all the ways you can interact with Saidkick: via the CLI, the Python client, or the direct REST API.

## 1. CLI Reference

The Saidkick CLI (`saidkick`) is the primary way to interact with the browser from your terminal.

### `saidkick start`
Starts the central FastAPI server.
- `--host`: Bind address (default: `0.0.0.0`).
- `--port`: Port number (default: `6992`).
- `--reload`: Auto-reload for development.

### `saidkick logs`
Fetch and display console logs from the connected browser.
- `--limit` (`-n`): Limit the number of logs (default: 100).
- `--grep` (`-g`): Filter logs by regex.

### `saidkick dom`
Get the current page's DOM or a specific element.
- `--css`: Target a specific element with a CSS selector.
- `--xpath`: Target a specific element with an XPath selector.
- `--all`: Return all matches (concatenated by newlines).

### `saidkick click`
Click an element on the page.
- `--css`: CSS selector.
- `--xpath`: XPath selector.

### `saidkick type`
Type text into an input field.
- `text`: (Argument) The text to type.
- `--css`: CSS selector.
- `--xpath`: XPath selector.
- `--clear`: Clear the field before typing.

### `saidkick select`
Select an option in a `<select>` element.
- `value`: (Argument) The value or text of the option to select.
- `--css`: CSS selector.
- `--xpath`: XPath selector.

### `saidkick exec`
Execute arbitrary JavaScript and return the result as JSON.
- `code`: (Optional argument) The JS code to run. If not provided, reads from stdin.
- **Example**: `echo "document.title" | saidkick exec`

---

## 2. Programmatic Python Client

You can use `SaidkickClient` in your own Python projects.

```python
from saidkick.client import SaidkickClient

client = SaidkickClient(base_url="http://localhost:6992")

# Get page title
title = client.execute("document.title")
print(f"Title: {title}")

# Interaction example
client.type("#search", "Saidkick", clear=True)
client.click("#search-button")

# Fetch logs
logs = client.get_logs(limit=10, grep="error")
for log in logs:
    print(log["data"])
```

---

## 3. REST API Reference

The server exposes the following endpoints (default base URL: `http://localhost:6992`).

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/console` | `GET` | Get log history. Query params: `limit`, `grep`. |
| `/dom` | `GET` | Get page HTML. Query params: `css`, `xpath`, `all`. |
| `/execute` | `POST` | Execute JS. JSON body: `{"code": "..."}`. |
| `/click` | `POST` | Click element. JSON body: `{"css": "...", "xpath": "..."}`. |
| `/type` | `POST` | Type text. JSON body: `{"css": "...", "xpath": "...", "text": "...", "clear": bool}`. |
| `/select` | `POST` | Select option. JSON body: `{"css": "...", "xpath": "...", "value": "..."}`. |

All `POST` endpoints return a JSON response containing the result of the operation or an error message.
