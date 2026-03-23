# Saidkick: Remote Browser Inspection & Automation

Saidkick is a lightweight developer tool that bridges your terminal and your web browser. It allows you to:
- Mirror browser console logs to a server.
- Remotely retrieve the DOM of any open tab.
- Execute arbitrary JavaScript in the context of a web page.

## Components
1. **FastAPI Server**: Manages WebSocket connections and provides a REST API.
2. **Chrome Extension**: A content script that connects to the server and executes commands.

## Setup

### 1. Start the Server
From the project root:
```bash
uv run saidkick
```
The server runs on `http://localhost:6992`.

### 2. Install the Extension
1. Open **Google Chrome** and navigate to `chrome://extensions`.
2. Enable **Developer mode** (toggle switch in the top right corner).
3. Click the **Load unpacked** button that appears.
4. In the file selector, navigate to this project and select the following directory:
   `tools/saidkick/extension/`
5. Click **Open** or **Select**.

## CLI Usage (Examples)

### Get Console Logs
```bash
curl http://localhost:6992/console
```

### Get Current DOM
```bash
curl http://localhost:6992/dom
```

### Execute JavaScript
```bash
# Get page title
curl -X POST -H "Content-Type: application/json" -d '{"code": "document.title"}' http://localhost:6992/execute

# Click a button
curl -X POST -H "Content-Type: application/json" -d '{"code": "document.querySelector(\"button\").click()"}' http://localhost:6992/execute
```
