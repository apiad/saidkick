<div align="center">

# 🫰 Saidkick

**A self-hosted sidekick that lets your terminal drive your browser.**

![PyPI - Version](https://img.shields.io/pypi/v/saidkick)
![PyPi - Python Version](https://img.shields.io/pypi/pyversions/saidkick)
![Github - Open Issues](https://img.shields.io/github/issues-raw/apiad/saidkick)
![Github - Commits](https://img.shields.io/github/commit-activity/m/apiad/saidkick)

</div>

---

Saidkick is a small, opinionated tool that lets scripts, shells, and AI agents drive a real browser end-to-end — listing tabs, navigating, clicking, typing into any rich-text field, dispatching keyboard events, taking screenshots — without the overhead of a full headless automation framework. It uses a FastAPI server as a hub and a Chrome extension as the spoke; you run `saidkick start`, install the extension once, and then every command in every language with an HTTP client can talk to a real, logged-in Chrome session.

It's the right size for **terminal-driven debugging, agent automation, and personal scripting** — not quite Playwright (which needs its own browser, its own auth, its own set of tricks to look "real") and not quite a remote-control MCP (which is gated on a specific agent runtime). Saidkick lives in the middle: your browser, your session, your cookies, driven from anywhere with `curl`.

## ⚡ Features

* **🎯 Semantic locators.** Target elements by what the user sees: `--by-text "Send"`, `--by-label "Password"`, `--by-placeholder "Search…"`. Falls back to CSS/XPath when you need precision.
* **🧭 Scroll-into-view.** `saidkick scroll --tab $TAB --by-text "Chapter 3"` brings an element into the viewport — essential before screenshotting something offscreen, and handy for pulling more content on infinite-scroll pages.
* **🔴 Highlight.** `saidkick highlight --tab $TAB --by-text "Deploy"` draws a temporary red ring around an element. Use it to point the user at *exactly* what to click when you're guiding them — pair with `screenshot` and they see the ring in the image.
* **🔤 Real keyboard events.** `saidkick press Enter --tab $TAB` dispatches a native CDP `Input.dispatchKeyEvent` — frameworks (Lexical, ProseMirror, React) treat it as a real keystroke, not a synthesised blob.
* **📸 Screenshots.** `saidkick screenshot --tab $TAB --output /tmp/shot.png` via CDP `Page.captureScreenshot`. Optional locator clips to an element; `--full-page` captures beyond the viewport.
* **✍️ Rich-text input.** `saidkick type` understands `contenteditable` via `document.execCommand("insertText", …)` — works on WhatsApp, Slack, Discord, Gmail compose, GitHub comments, Notion, and every other Lexical/ProseMirror/Quill/Slate/Draft-backed editor.
* **⏳ Wait-for-element built in.** `--wait-ms N` on every selector-using command polls the DOM until it resolves. Default 0 preserves fail-fast behaviour.
* **🧵 Multi-browser, multi-tab.** Each extension connection gets an ephemeral `br-XXXX` ID; commands address tabs as `br-XXXX:N` composites. Pipe the output of `saidkick open` straight into the next command.
* **🛡️ CSP bypass.** Runs scripts via `chrome.debugger` on pages that block content-script injection.
* **🐚 Pipe-friendly CLI.** One token per stdout (`saidkick open` prints `br-XXXX:N`; `saidkick screenshot` emits raw PNG bytes). Everything composes in bash.

## 🚀 Quickstart

### Install

```bash
pip install saidkick
```

Or pull the latest from GitHub:

```bash
pip install git+https://github.com/apiad/saidkick.git
```

### Load the extension

1. Open `chrome://extensions/` in Chrome.
2. Enable **Developer mode**.
3. Click **Load unpacked** and point at `src/saidkick/extension/` (inside the cloned repo, or inside your installed `saidkick` package — `python -c "import saidkick, os; print(os.path.dirname(saidkick.__file__) + '/extension')"`).

The extension connects to `ws://localhost:6992/ws` and auto-reconnects every 5 seconds if the server comes and goes.

### Start the server and drive

```bash
# Terminal 1: start the hub
$ saidkick start

# Terminal 2: list connected tabs
$ saidkick tabs
br-a1b2:12  https://example.com/  "Example Domain"  (active)
br-a1b2:15  https://docs.python.org/  "Python 3.12 Docs"

# Open a new tab, talk to it, screenshot the result
$ BR=br-a1b2
$ TAB=$(saidkick open --browser "$BR" https://example.com/)
$ saidkick text --tab "$TAB" --css "h1"
Example Domain
$ saidkick screenshot --tab "$TAB" --output /tmp/shot.png
Wrote 28934 bytes to /tmp/shot.png
```

## 🎮 Driving a chat app end-to-end

No `exec`, no selector archaeology — just semantic locators and a keystroke:

```bash
TAB=br-a1b2:15
saidkick click  --tab "$TAB" --by-text "Alice Chen"
saidkick type   "Hello Alice" --tab "$TAB" --by-label "Type a message"
saidkick press  Enter --tab "$TAB"
saidkick screenshot --tab "$TAB" --output /tmp/sent.png
```

That's WhatsApp Web, Slack, Discord, Gmail compose, or any similar app, in four lines.

## 🧭 Pointing the user at something

When an agent is guiding the user through an app, it often needs to say "click *this* button." Two primitives make that precise:

```bash
# Scroll the element into view (it may be offscreen)
saidkick scroll --tab "$TAB" --by-text "Deploy"

# Draw a temporary red ring around it (default 2s)
saidkick highlight --tab "$TAB" --by-text "Deploy"

# Screenshot so the user sees the ring in the image too
saidkick screenshot --tab "$TAB" --output /tmp/click-this.png
```

Good uses:

- **"Click that button"** — highlight + screenshot + send the image to the user.
- **"The error is in this field"** — `highlight --color "#f59e0b"` (amber) on a form field the user needs to correct.
- **Pre-screenshot framing** — `scroll` before `screenshot` so what you want to capture is actually in the viewport.
- **Checklist walkthroughs** — highlight each step as you narrate it; use `--duration-ms 0` to keep the ring up until you place the next one.
- **Infinite-scroll content extraction** — scroll to the last visible item, wait for more to load, repeat.

`scroll` takes `--block {center|start|end|nearest}` and `--behavior {auto|smooth}`. `highlight` takes `--color` (any CSS color) and `--duration-ms` (0 = persist until page reload).

## 🧭 Command reference

| Command | What it does |
|---|---|
| `saidkick start` | Start the FastAPI hub (defaults to `0.0.0.0:6992`). |
| `saidkick tabs` | List tabs across connected browsers (`--active` filter). |
| `saidkick find --tab T --by-text X` | Return JSON list of matching elements (debug). |
| `saidkick dom --tab T --css X` | Outer-HTML of matched element(s). |
| `saidkick text --tab T [--css X]` | `innerText` of the tab or a scoped region. |
| `saidkick click --tab T --by-text X` | Click. |
| `saidkick type "msg" --tab T --by-label X` | Type (contenteditable-aware). |
| `saidkick select "value" --tab T --css X` | Select an `<option>`. |
| `saidkick press Enter --tab T [--mod ctrl,shift]` | Dispatch a keyboard event. |
| `saidkick scroll --tab T --by-text X [--block center\|start\|end]` | Scroll element into view. |
| `saidkick highlight --tab T --by-text X [--color red] [--duration-ms N]` | Temporary ring around an element. |
| `saidkick screenshot --tab T [--output PATH]` | Capture PNG. |
| `saidkick navigate URL --tab T [--wait dom\|full\|none]` | Redirect a tab. |
| `saidkick open URL --browser BR` | New tab; prints the composite `br-XXXX:N`. |
| `saidkick exec --tab T "return …"` | Arbitrary JS via CDP (must `return` a value). |
| `saidkick logs [--grep X] [--browser BR]` | Console-log buffer. |

Every selector-using command accepts the same locator options: `--css`, `--xpath`, `--by-text`, `--by-label`, `--by-placeholder`, `--within-css`, `--nth`, `--exact`, `--regex`, `--wait-ms`. Exactly one locator must be set (400 otherwise).

## 🐍 Python client

Everything the CLI does is also available as a library:

```python
from saidkick.client import SaidkickClient
c = SaidkickClient()

tabs = c.list_tabs(active=True)
tab = tabs[0]["tab"]

# Search for something on DuckDuckGo
c.type(tab, "saidkick", css="input[name=q]")
c.press(tab, "Enter")

# Screenshot the results
shot = c.screenshot(tab)
import base64; open("/tmp/ddg.png", "wb").write(base64.b64decode(shot["png_base64"]))
```

## 🧱 Architecture

**Hub-and-spoke.** The FastAPI server is the hub; the Chrome extension (MV3) is the spoke.

```
┌──────────────┐      WebSocket       ┌────────────────────────────┐
│ Your CLI/    │◀───────▶ hub ────────│ Chrome MV3 extension       │
│ agent/script │    REST              │  • service worker          │
└──────────────┘                      │  • content + main-world    │
                                      │  • popup w/ reconnect      │
                                      └────────────────────────────┘
```

- The **hub** is stateless between restarts except for a circular log buffer and the set of live WebSocket connections.
- The **spoke** stores an ephemeral `br-XXXX` ID on handshake, runs content scripts in every tab on demand (with lazy injection fallback), and drives CDP via `chrome.debugger` for JS execution, keyboard events, screenshots, and page-load waits.
- Tabs are addressed by the composite `br-XXXX:N` — `br-XXXX` identifies the browser connection; `N` is Chrome's native `tab.id`.

The extension popup shows current connection state and a reconnect button — useful when the MV3 service worker goes idle.

## 📖 Docs

- [User Guide](docs/user-guide.md) — full CLI / REST / client reference.
- [Design Doc](docs/design.md) — architecture, error policy, protocol details.
- [Deploy Guide](docs/deploy.md) — server + extension setup.
- [SKILL.md](SKILL.md) — how an AI agent should use saidkick.
- [CHANGELOG](CHANGELOG.md) — release history.

## 🤝 Why saidkick (vs. …)

- **vs. Playwright / Selenium.** Those spawn their own browser with a fresh profile — no cookies, no logins, no browser extensions. Saidkick drives *your* Chrome, logged in, with the session state you already have. Trade-off: you're automating the real thing, so destructive actions are real.
- **vs. `claude-in-chrome` / MCP browser tools.** Saidkick is self-hosted and agent-agnostic. Anything with an HTTP client can use it — shell scripts, cron jobs, arbitrary Python, any LLM runtime. Not gated on a specific agent host or credential.
- **vs. raw Chrome DevTools Protocol.** CDP is powerful but verbose. Saidkick wraps the patterns you actually use (locators, keyboard, screenshots, waits) behind one-line CLI commands.

## 🛠️ Development

```bash
git clone https://github.com/apiad/saidkick
cd saidkick
uv sync --all-groups
uv run pytest -m "not e2e"   # unit + integration
uv run saidkick start        # hub
```

## 📜 License

MIT — see [LICENSE](LICENSE) if present, otherwise standard MIT applies.
