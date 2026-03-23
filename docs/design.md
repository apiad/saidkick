# Saidkick Design Document

This document describes the high-level architectural design and implementation details of Saidkick.

## Architectural Overview

Saidkick follows a **Hub-and-Spoke** architecture, where a central FastAPI server acts as the hub, and multiple browser extensions (on different machines or browser profiles) act as spokes.

### 1. Central Server (FastAPI)
The server maintains bi-directional WebSocket connections with connected browser extensions and provides a stateless REST API for automation clients (CLI, Python scripts, etc.).
- **`SaidkickManager`**: A central class that manages active WebSocket connections, maintains a circular log buffer (`deque`), and correlates asynchronous requests with their responses using `uuid.uuid4()` and `asyncio.Future`.

### 2. Browser Extension (Chrome MV3)
The extension consists of three layers:
- **`background.js` (Service Worker)**: Maintains the WebSocket connection to the server. It handles routing commands to the correct tab and uses the `chrome.debugger` API to execute scripts in contexts with strict Content Security Policy (CSP).
- **`content.js`**: Injected into every page, it performs DOM operations such as `CLICK`, `TYPE`, `SELECT`, and `GET_DOM`.
- **`main_world.js`**: Executes in the page's main context to intercept and mirror `console.log` calls back to the server.

### 3. Automation Client (Python)
- **`SaidkickClient`**: A reusable Python class that wraps the server's REST API. It handles HTTP request/response cycles, error handling, and parameter serialization.
- **`SaidkickCLI`**: A terminal-based user interface built with **Typer** and **Rich** that uses `SaidkickClient` to provide a human-friendly way to interact with the browser.

## Communication Protocol

### WebSocket Communication (Extension -> Server)
Messages are sent as JSON objects. A typical command message looks like this:
```json
{
  "type": "GET_DOM",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "payload": { "css": "#my-element" }
}
```

The extension responds with a `RESPONSE` type:
```json
{
  "type": "RESPONSE",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "success": true,
  "payload": "<div>...</div>"
}
```

### REST API (Client -> Server)
The server provides endpoints for all major browser interactions. For example, a `POST /execute` will block until the server receives the matching `RESPONSE` from the browser extension or a timeout occurs.

## Technology Stack

- **Backend**: Python 3.12+, FastAPI, Uvicorn, Pydantic.
- **Frontend/Extension**: JavaScript (Vanilla), Chrome Extension Manifest V3.
- **CLI**: Typer, Rich, HTTPX.
- **Build/Package Management**: UV, Hatchling.
