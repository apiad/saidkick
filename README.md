<div align="center">

# 🫰 Saidkick

**A browser for agents, supervised by humans.**

![PyPI - Version](https://img.shields.io/pypi/v/saidkick)
![PyPi - Python Version](https://img.shields.io/pypi/pyversions/saidkick)
![Github - Open Issues](https://img.shields.io/github/issues-raw/apiad/saidkick)
![Github - Commits](https://img.shields.io/github/commit-activity/m/apiad/saidkick)

</div>

---

Saidkick is a browser designed to be driven by AI agents, with a human watching over its
shoulder. Agents open isolated browsing contexts, read pages as accessibility snapshots, click
and type — and when they hit a CAPTCHA, a 2FA prompt, or a login wall, they **ask for a human**,
who takes the wheel in a live cockpit, does the thing, and hands control back.

One Python daemon owns Chromium through Playwright and serves three surfaces: an **MCP** server
for agents, a **REST + WebSocket** API for scripts, and a **web cockpit** for you.

> **Saidkick 2.0 is a rewrite.** The Chrome extension is gone. See
> [Migrating from 1.x](#-migrating-from-1x).

## ⚡ Why

Driving Chrome from outside is a fight with its security model. `chrome.debugger` is the only
route to CDP, MV3 kills the service worker on idle, CSP blocks injection, and the accessibility
tree is unreachable from a content script. Every feature ends up shaped by the sandbox rather
than the problem.

Saidkick 2.0 makes the agent a **first-class principal** in the browser instead of a tolerated
intruder — and gives the human a supervisory seat instead of no seat at all.

## 🚀 Quickstart

```bash
pip install saidkick
playwright install chromium

saidkick serve            # daemon + live dashboard; cockpit on http://localhost:6992
```

Then, in another terminal:

```bash
TAB=$(saidkick quick https://example.com)   # ephemeral context + tab in one call
saidkick snapshot --tab "$TAB"
# - heading "Example Domain" [level=1]
# - paragraph: This domain is for use in documentation examples…
# - link "Learn more"

saidkick click --tab "$TAB" --by-role link --by-text "Learn more"
saidkick screenshot --tab "$TAB" --output /tmp/shot.png
```

Point an MCP client at **`http://localhost:6992/mcp`**.

## 🧩 The model

Three layers, and the distinction between the middle two is what makes end-to-end testing work.

| | |
|---|---|
| **Profile** | A named on-disk storage partition. *(VS3 — not in this release.)* |
| **Context** | An isolated cookie jar and storage partition. Two contexts cannot see each other's logins. |
| **Tab** | A page inside a context, addressed `ctx_a1b2:3`. Tabs in one context share session state. |

Contexts today are **ephemeral**: they start empty and are discarded on close. That is the right
default — reproducible, and incapable of damaging real logged-in state.

## 🙋 The human in the loop

The point of saidkick. An agent that cannot get past a CAPTCHA calls `request_human`:

```
agent  → request_human(context, "enter the 2FA code")
       ← still_waiting            ... and it tells its operator so
you    → open the cockpit, hit "Take over"
agent  → click(...)  ✗ HumanHoldsControl      (but snapshot/find still work)
you    → type the code, hit "Release" with a note
agent  ← resolved, note="code entered"        ... and carries on
```

**Every context has exactly one controller** — `agent` or `human`. While you hold it, the agent's
mutating calls fail fast rather than queueing, because an error it can read beats a silent block.
Read-only calls keep working, so it can watch the rescue it asked for.

`request_human` takes two independent durations: **`deadline_s`** (how long the request stays
open, default 600) and **`poll_s`** (how long the call blocks, default 120). An agent that wants
to wait longer loops on `still_waiting`. A timeout closes the request and touches nothing else —
reaping a half-finished login would destroy exactly the state you were about to rescue.

### How you find out

Three channels, none of which need configuring:

1. **The terminal** running `saidkick serve` shows a live dashboard with pending requests at the
   top — reason, elapsed, remaining, and the cockpit URL.
2. **The agent itself.** Its MCP tool description obliges it to relay the request through its own
   channel to its own operator. This is the one that works when the daemon is headless on a
   server and nobody is watching anything.
3. **The page.** When headful, the tab raises itself, shows a banner, pulses its border, and
   marks its title and favicon. The overlay is `aria-hidden` inside a closed shadow root, so the
   agent never sees it and never tries to click it.

Plus an optional `notify.webhook_url`, off by default, unbranded — wire it to whatever you use.
Saidkick ships no integration with any chat, mail, or push provider.

## 🤖 The agent surface (MCP)

| Group | Tools |
|---|---|
| Contexts | `list_contexts`, `open_context`, `close_context` |
| Tabs | `list_tabs`, `open_tab`, `close_tab`, `navigate` |
| Reading | `snapshot_page`, `screenshot`, `find` |
| Acting | `click`, `type`, `press`, `select`, `highlight` (each also accepts `handle=`) |
| Human loop | `request_human`, `control_state`, `list_pins`, `read_pin` |
| Diagnostics | `get_events` |

**`snapshot_page` is the one that matters**, and it defaults to `mode="aria"`:

```
- heading "Form" [level=1]
- textbox "Username":
  - /placeholder: your name
- combobox "Country"
- button "Send"
```

Every entry carries a role and an accessible name that map *directly* onto a locator — read
`button "Send"`, then click with `by_role="button", by_text="Send"`. `mode="html"` exists and is
a last resort: it floods the context window and tells the agent nothing `aria` doesn't.

## 📍 Pins — pointing the agent at something

When an agent can't figure out which element you mean, **point at it**. In the cockpit, toggle
**Pin**, then click an element or drag a box around one. Saidkick resolves the DOM node, highlights
it so you can confirm, and hands the agent an addressable reference — no takeover required, you
just point while watching.

The agent lists and reads pins (`list_pins`, `read_pin`) and acts on one by passing `handle=`:

```
you    → [cockpit] Pin mode, click the "Send" button
agent  → list_pins(context)         →  [{handle: "el_7f2", label: "...", descriptor: {...}}]
agent  → click(tab, handle="el_7f2")   ✓
```

Each pin carries the element's descriptors, a suggested locator, durable css/xpath fallbacks, and
a clipped screenshot. Acting on a handle that has gone stale (the page changed) raises
`StaleHandle`, and the agent falls back to the selectors in the pin's bundle. **Pins are placed by
humans only** — the agent cannot create one, which is the whole point: it's *you* saying "this."

## 🎯 Locators

Target elements the way a user sees them. Set exactly one of `--css`, `--xpath`, `--by-text`,
`--by-label`, `--by-placeholder`, `--by-role` (which may be combined with `--by-text` as the
accessible name). Refine with `--within-css`, `--nth`, `--exact`, `--regex`, `--wait-ms`.

`--by-text` returns the **leaf-most** match, so nested text doesn't trip a spurious ambiguity.
When a locator does match several elements, the error carries the candidate list so you can pick
with `--nth` instead of guessing.

## 🧭 Command reference

| Command | What it does |
|---|---|
| `saidkick serve` | Run the daemon: engine, REST, MCP at `/mcp`, cockpit, dashboard. |
| `saidkick quick URL` | Ephemeral context + tab in one call; prints the tab id. |
| `saidkick contexts` / `tabs --context C` | List contexts / tabs. |
| `saidkick snapshot --tab T [--mode aria\|text\|html]` | Read the page. |
| `saidkick find --tab T <locator>` | Describe matching elements. |
| `saidkick click / type / press / select / scroll / highlight` | Act on an element. |
| `saidkick screenshot --tab T [--output PATH]` | Capture a PNG. |
| `saidkick navigate URL --tab T` / `open URL --context C` / `close --tab T` | Tab plumbing. |
| `saidkick requests` | Show pending human requests. |

`SAIDKICK_URL` points the CLI and client at a daemon on another port or host.

## 🐍 Python client

```python
from saidkick.client import SaidkickClient

c = SaidkickClient()
tab = c.quick("https://example.com")
print(c.snapshot(tab))
c.click(tab, by_role="link", by_text="Learn more")
open("/tmp/shot.png", "wb").write(c.screenshot(tab))
```

## 🧱 Architecture

```
saidkick.engine     Playwright. Contexts, tabs, locators, actions, screencast.
                    Knows nothing about HTTP, agents, or humans.
saidkick.control    Arbitration, human requests, event bus.
saidkick.api        FastAPI: REST + WebSockets + mounted MCP.
saidkick.cockpit    The human UI: live view, takeover, request queue.
saidkick.client     SaidkickClient + Typer CLI.
```

The seam between `engine` and `control` is deliberate: browser semantics are tested against a
local fixture site without simulating a human, and arbitration is tested as a state machine
without a browser.

The cockpit streams CDP JPEG frames over a WebSocket and draws them to a canvas — quality 60 at
1280px while you watch, 95 at native resolution the moment you take control. Input goes back as
`Input.dispatchMouseEvent` / `dispatchKeyEvent`, with a paste box that uses `Input.insertText`
because a 2FA code should be inserted, not synthesised as thirty keystrokes.

**Note on hostnames:** the MCP SDK enables DNS-rebinding protection and allows `127.0.0.1:*`,
`localhost:*` and `[::1]:*`. Reaching `/mcp` through a reverse proxy on a real hostname needs
`allowed_hosts` widened explicitly.

## 📦 Migrating from 1.x

**The Chrome extension is deleted**, and with it the whole hub-and-spoke design.

| 1.x | 2.0 |
|---|---|
| `saidkick start` | `saidkick serve` |
| `br-a1b2:15` | `ctx_a1b2:3` |
| `saidkick dom` / `text` | `saidkick snapshot --mode html` / `--mode text` |
| `saidkick exec`, `logs`, `doctor`, `mirror` | *removed with the extension that backed them* |
| drives your real Chrome | drives its own Chromium |

**Contexts start logged into nothing.** The first time an agent hits a login wall it pauses, you
authenticate through takeover, and from VS3 onward `save_profile` will make that durable. This is
the same mechanic as the 2FA flow, applied to bootstrapping.

Scripts that hardcode a `br-XXXX:N` id will break. There is no compatibility shim — deleting the
extension is most of the point.

## 🛠️ Development

```bash
uv sync --all-groups
uv run playwright install chromium

uv run pytest -m "not browser"   # fast: no Chromium
uv run pytest -m browser         # against a local fixture site
uv run saidkick serve --headful  # watch it work
```

## 🗺️ Status

Shipped: isolated contexts and tabs, ARIA snapshots, the full locator vocabulary, actions, MCP,
REST, the cockpit with live view and takeover, control arbitration, `request_human`, the terminal
dashboard, the attention overlay, and **pins**.

Next: persistent profiles and `save_profile` (so logins survive a restart); a run log and trace
replay.

## 📜 License

MIT.
