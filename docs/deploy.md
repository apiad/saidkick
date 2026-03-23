# Saidkick Deployment & Execution Guide

This guide covers everything you need to know to get Saidkick up and running.

## Prerequisites

- **Python 3.12+**: For running the server and CLI.
- **UV Package Manager**: Highly recommended for managing dependencies.
- **Google Chrome**: (Or any Chromium-based browser) to run the extension.

## 1. Start the Saidkick Server

The server acts as a central hub, managing WebSocket connections from browser extensions and exposing a REST API for automation clients.

From the project root (or inside the `tools/saidkick/` directory):

```bash
uv run saidkick start
```

By default, the server will listen on `0.0.0.0:6992`.

### Configuration Options
You can configure the server using CLI flags:
- `--host <host>`: Bind address (default: `0.0.0.0`).
- `--port <port>`: Port number (default: `6992`).
- `--reload`: Enable auto-reload for development.

## 2. Install the Chrome Extension

The extension is the component that interacts with the browser tabs.

1.  Open **Google Chrome** and navigate to `chrome://extensions`.
2.  Enable **Developer mode** (the toggle switch in the top right corner).
3.  Click the **Load unpacked** button.
4.  Navigate to the following directory in your file selector:
    `tools/saidkick/src/saidkick/extension/`
5.  Click **Open** or **Select Folder**.

Once installed, the background script will automatically attempt to connect to `ws://localhost:6992/ws`.

## 3. Verify the Connection

You can verify the setup by running the `logs` command in your terminal:

```bash
uv run saidkick logs
```

If the server is running and the extension is connected, you should see a message:
`[browser]LOG: Saidkick: Background script connected to server`

## 4. Troubleshooting

- **Server Connection Errors**: Ensure the server is running and port `6992` is not blocked by a firewall.
- **Extension Not Connecting**: If you are using a non-standard port, you must update the `SERVER_URL` in `tools/saidkick/src/saidkick/extension/background.js` and reload the extension.
- **CSP Issues**: If the `exec` command fails on certain websites (like GitHub), ensure the extension has appropriate permissions. Saidkick uses the `chrome.debugger` API to bypass most CSP restrictions automatically.
