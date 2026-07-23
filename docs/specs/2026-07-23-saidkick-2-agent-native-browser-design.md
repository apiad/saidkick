# saidkick 2.0 — an agent-native browser

**Status:** approved 2026-07-23.
**Scope:** replaces the hub-and-spoke architecture (FastAPI hub + Chrome MV3 extension) with a
single Python daemon that owns Chromium through Playwright, serves an MCP agent surface, and
serves a human cockpit for supervision and takeover. Breaking: saidkick 2.0.

---

## 1. Why

Saidkick 1.x drives the user's real Chrome from the terminal. It works, but every capability it has
was smuggled past a security model designed to keep it out. `chrome.debugger` is the only way to
reach CDP. MV3 kills the service worker on idle. CSP blocks content-script injection, so
execution falls back to the debugger again. The accessibility tree is unreachable from the
content script, so `by_role` has to resolve background-side and be rewritten into a CSS selector
before it can be dispatched. Every feature is shaped by the sandbox rather than by the problem.

The premise of 2.0 is that the agent should be a **first-class principal** in the browser rather
than a tolerated intruder, and that the human's role is supervisory: solve the captcha, enter the
2FA code, point at the thing that matters, take the wheel when the agent is about to do something
stupid. The browser is driven by agents and watched by a human.

A second motivation is end-to-end testing. Isolated, reproducible browsing contexts with clean
storage are a requirement for testing one's own applications, and 1.x — which shares one real
Chrome profile across everything — cannot provide them.

## 2. Non-goals

Explicitly out of scope for 2.0:

- **Daily-driver browsing.** No bookmarks, no password manager, no history UI, no sync, no ad
  blocking. A conventional browser remains the tool for browsing.
- **Chrome extension support.** The MV3 extension is deleted, and no extension-loading mechanism
  replaces it.
- **An approval-policy engine.** Gating risky actions (purchases, deletions, sends) behind a
  policy is a natural extension of the human-in-the-loop machinery, but it is not v1.
- **Multi-user auth on the cockpit.** Single operator, bound to localhost by default.
- **Integrations with any notification service.** saidkick announces on the terminal, through the
  agent's own words, and in the page itself (§5.1). Anything beyond that is a generic outbound
  webhook the user configures; no chat, mail, or push provider is shipped or named.
- **Containerized per-context isolation.** Storage partitioning inside one Chromium is sufficient.
  If per-context containers are ever needed, `sandbox` is the natural owner, not this repo.
- **Driving an existing Chrome installation.** See §12.

## 3. Object model

Five objects. Three are addressable by agents.

### Profile

A named on-disk storage partition at `~/.saidkick/profiles/<name>/`: cookies, localStorage,
sessionStorage, IndexedDB, service workers. Created empty and populated over time by human
takeover. This is the durable thing — `personal`, `test-clean`, `client-acme`.

### Context

A live browsing session in one of two modes.

**Attached** — Playwright `launch_persistent_context` against the profile directory. Writes back.
This is "act as the real signed-in user on GitHub." **Exactly one attached context per profile
may be live at a time**, and a second attempt raises `ProfileLocked`.

Correction verified on Playwright 1.61 / headless Chromium: contrary to an earlier assumption
here, a second `launch_persistent_context` on the same user-data-dir **does not** fail — Chromium
does not take the exclusive `SingletonLock` in this configuration. So `ProfileLocked` is a
**daemon-enforced invariant**, not a reflection of an OS lock: the engine tracks which profiles
have a live attached context and refuses a second. This is more predictable than depending on
Chromium's behaviour, which varies by platform and headless mode.

**Ephemeral** — a fresh context seeded from a snapshot of the profile's storage state, discarded
on close. Unlimited parallel instances. This is the e2e-testing mode and the safe default for
anything destructive.

**Verified limit on seeding (Playwright 1.61).** `BrowserContext.storage_state()` returns
`{cookies, origins}`, where `origins` carries **localStorage only** — sessionStorage, IndexedDB
and service workers are not portable through it. So the two modes differ in what they preserve,
and the difference is not cosmetic:

| | attached | ephemeral (seeded) |
|---|---|---|
| cookies | yes | yes |
| localStorage | yes | yes |
| sessionStorage | yes | **no** |
| IndexedDB | yes | **no** |
| service workers | yes | **no** |

Most authentication survives seeding, because most session tokens live in cookies or
localStorage. Applications that keep auth in IndexedDB (notably some Firebase and Supabase
setups) will appear logged out in an ephemeral context, and that is expected behaviour, not a
bug. `save_profile` (§7) is the way to move state the other direction.

### Tab

A Playwright `Page` inside a context. Addressed as `ctx_a1b2:3`.

### Session

The bookkeeping wrapper around a context: which agent owns it, what it is doing, whether a human
currently holds the wheel, its event sequence number. Not a browser concept — it is what the
cockpit lists and what makes control arbitration coherent.

### Pin

A human-placed, agent-addressable reference to a DOM element or region. See §6.

## 4. Architecture

One long-lived process (`saidkick serve`) owning Playwright, serving three surfaces off one
FastAPI app: MCP at `/mcp` (streamable HTTP), REST + WebSocket for machine clients, and the
cockpit web UI. A `saidkick mcp` stdio shim is provided for MCP clients that only speak stdio.

```
saidkick.engine    Playwright. Profiles, contexts, tabs, locators, input, screencast.
                   Knows nothing about HTTP, agents, or humans.
saidkick.session   Ownership, control arbitration, event bus, run log (beaver).
saidkick.api       FastAPI: REST + WS + mounted MCP server.
saidkick.cockpit   Human UI: session list, live view, takeover, request queue, pins.
saidkick.client    SaidkickClient + Typer CLI.
```

The seam that matters is **engine ↔ session**. The engine is a pure browser-driving library with
no notion that a human might interrupt; the session layer is where "the agent asked for help and
is now blocked" lives. Keeping them apart is what allows browser automation to be tested against
a fixture server without simulating a human, and control arbitration to be tested as a state
machine without a browser.

**Dependencies added:** `playwright`, `beaver-db`. The cockpit vendors `syalia-ui` rather than
growing its own CSS. `fastapi`, `uvicorn`, `typer`, `rich`, `httpx`, `pydantic` are retained;
`websockets` remains for the cockpit's event and screencast sockets.

## 5. Control arbitration and the human-in-the-loop

Every context has exactly one controller: `agent`, `human`, or `none`. All input — the agent's
and the human's — routes through the same arbitration point. There is no state in which both can
act.

### Acquiring control

**The polite path.** The agent calls `request_human(context, reason, deadline_s, poll_s)`. The
context enters `awaiting_human` and the request is announced on every channel in §5.1.

Two independent durations, and conflating them is the obvious trap:

- **`deadline_s`** (default 600) is how long *the request stays open* for a human to answer.
- **`poll_s`** (default 120) is how long *this call blocks* before returning.

The call returns `resolved` (a human handled it — with their optional note), `still_waiting`
(`poll_s` elapsed, the request is still open, call again), or `timeout` (`deadline_s` elapsed,
the request is closed unanswered). An agent that wants to wait the full ten minutes loops on
`still_waiting`.

The `poll_s` bound is deliberate: an unbounded blocking MCP tool call would trip harness timeouts
in every agent runtime that consumed this browser. Re-calling `request_human` on the same context
while a request is open **rejoins the existing request** rather than creating a second one.

**The barge-in path.** The operator, watching a session, presses "Take over." Same state
transition, no request required.

### While the human holds control

Agent calls targeting that context **fail fast** with `HumanHoldsControl`. They do not queue and
do not block. An agent silently retrying into a queue has no model of what is happening to it; an
error it can read and reason about is strictly better.

### Releasing

Control returns to the agent and any pending request resolves, optionally carrying a free-text
note the operator types ("logged in", "captcha solved") which lands in the agent's return value.

### On timeout

The request resolves as `timeout` and the agent decides what to do next. The context is **not**
killed and the browser is not touched — reaping a half-finished login would destroy exactly the
state the human was about to rescue.

### 5.1 How the human finds out

The weakest possible design is "a card appears in the cockpit", because it assumes the operator
is already looking at the cockpit — which they usually are not. And routing the alert to a
specific chat service would bake one user's setup into an open-source tool. So the announcement
goes out on **three channels that need no configuration**, plus one optional escape hatch.

**1. The terminal running the daemon.** `saidkick serve` is not a log spew; it renders a live
Rich dashboard in the terminal it was started from — contexts and their tabs, which agent owns
each one, the controller state, a tail of recent actions, and, occupying the top of the screen
whenever one exists, the **pending human requests**: context, reason, elapsed, remaining, and the
cockpit URL to open. This is the default surface for a developer who started the daemon in a
terminal and is working nearby. `--quiet` falls back to plain log lines for daemonized runs.

**2. The agent's own words.** The agent already has a channel to its human — a chat window, a
CLI, a notification — and it is the only channel that is guaranteed to exist. saidkick makes use
of it by **writing the instruction into the MCP tool descriptions**: `request_human`'s
description tells the agent that it must relay `reason` to its own operator through its own
channel before waiting, and that on `still_waiting` it should surface the wait rather than loop
silently. The return payloads carry a ready-to-relay `human_message` string and the cockpit URL
so the agent has something concrete to pass along.

This is the channel that works when the daemon is headless on a server and nobody is watching the
terminal — which is the common case for a scheduled job — and it costs nothing but careful
prose. Tool descriptions are the integration point, not an afterthought.

**3. The page itself.** When the browser is headful, the tab that needs help asks for attention
directly: `Page.bringToFront` raises it, an injected overlay banner names the reason, the viewport
gets a pulsing border, and the document title and favicon are swapped for an attention marker
that is visible in the tab strip even when the window is buried. If the request resolves or times
out, all of it is reverted.

The overlay must be **invisible to the agent**, or the next accessibility snapshot will contain
saidkick's own banner and the agent will try to interact with it. It is therefore mounted in a
shadow root on a single injected host element, marked `aria-hidden="true"` and
`role="presentation"` so Playwright's a11y snapshot skips it entirely, and given
`pointer-events: none` except on its own dismiss control. That invariant is a test, not a hope
(§11).

Sound is deliberately omitted: browser autoplay policy blocks audio without a prior user gesture,
so a notification chime would work unpredictably and be worse than no chime at all.

**4. An optional webhook.** `notify.webhook_url` in the config posts a JSON payload
(`{context, reason, url, deadline}`) when a request opens. Unbranded and off by default — it is
how a user wires up whatever they personally use, and saidkick ships no integration with any
particular service.

## 6. Seeing and driving the page

### Screencast

CDP `Page.startScreencast` streams JPEG frames over a WebSocket to a canvas in the cockpit,
acknowledged per frame (`Page.screencastFrameAck`) for backpressure. Started lazily when a viewer
opens a session and stopped when the last viewer disconnects — screencasting idle contexts to
nobody would burn CPU for nothing.

**The ack is mandatory, not an optimization.** Verified on Playwright 1.61 / Chromium: a session
that starts a screencast and never acks receives **exactly one frame**, forever. The backpressure
loop *is* the transport, so a bug in the ack path does not degrade the stream, it flatlines it
after the first image — which looks exactly like a working screenshot. §11 covers this with a
test asserting frame count grows past one.

Each frame arrives as `{data (base64 JPEG), metadata, sessionId}`, where `metadata` carries
`deviceWidth`, `deviceHeight`, `pageScaleFactor`, `scrollOffsetX/Y` and `offsetTop` — everything
needed to map canvas coordinates back to page coordinates for takeover and pins.

**JPEG, not PNG.** On localhost, bandwidth is free; the cost is CPU in Chromium's encoder on
every compositor update, and PNG's lossless deflate is roughly an order of magnitude more
expensive per frame than JPEG at q80. Resolution is an independent knob (`maxWidth`/`maxHeight`),
so full native viewport resolution and JPEG are not in tension. The only artifact is 4:2:0 chroma
subsampling softening colored text edges, which disappears at high quality.

Quality is **adaptive**:

| Mode | Quality | Max width |
|---|---|---|
| Observing | ~60 | 1280 |
| Human holds control | ~95 | native |

Fidelity is bought exactly when precise work is about to happen. Because `startScreencast` is
event-driven rather than fixed-rate (it emits on compositor update and waits for the ack), a
static page costs approximately nothing and high quality during takeover is affordable.

These defaults are reasoned from the encoder's mechanics, not measured. The implementation plan
includes a benchmark (PNG vs JPEG at q60/80/95: encode time and frame latency on a real page) and
the measured numbers set the final defaults.

**WebGL is not used.** The frames are JPEG, the browser's decoder is native, and the 2D canvas
path is already GPU-composited. The optimizations that pay are `createImageBitmap` (decode off
the main thread) and rendering in an OffscreenCanvas in a worker. An h264/WebRTC transport would
be better at high framerate but is far more machinery and is not offered by Chromium's screencast
directly; not v1.

### Takeover input

The cockpit forwards mouse and key events over the same socket into `Input.dispatchMouseEvent` /
`Input.dispatchKeyEvent`, with coordinates scaled from canvas to viewport, accepted only while
`controller == human`. A paste box uses `Input.insertText` — for a 2FA code, inserting the string
is more reliable than synthesizing thirty keystrokes.

### Pins

A pin is a human-placed, agent-addressable reference to a DOM element or region. The operator
clicks (or drags a rectangle) on the canvas to say "this is the thing."

**Resolution.** Canvas coordinates map to viewport coordinates through the same transform
takeover uses, and the daemon calls CDP **`DOM.getNodeForLocation(x, y)`** — the mechanism behind
DevTools' inspect-element. For a drag, the rectangle is evaluated against `getBoundingClientRect`
in the page and reduced to the leaf-most fully-enclosed elements, or their common ancestor.

**Confirmation.** The screencast frame may be a few hundred milliseconds stale, so on an
animating page a hit test can land on the wrong node. On click, the daemon resolves the node,
highlights it in the live page (reusing saidkick's existing highlight primitive), and the
operator sees the echo in the stream before it becomes a pin.

**What the agent receives** is a bundle, not an XPath:

- a **live handle** (`el_x7f2`), so the agent calls `click(el_x7f2)` with no selector resolution
  and no ambiguity. Mechanically this is *not* a Playwright `ElementHandle`: CDP hands back a
  `backendNodeId`, and there is no public API to adopt one into a Playwright handle. Instead the
  daemon calls `DOM.resolveNode` → `Runtime.callFunctionOn` to **stamp the element with a
  `data-saidkick-pin="el_x7f2"` attribute**, and the handle resolves as
  `page.locator('[data-saidkick-pin="el_x7f2"]')`. Verified working end to end. This is also more
  durable than an `ElementHandle`, which dies on any navigation, whereas a stamped attribute
  survives re-render as long as the node itself does. The cost is one attribute written into the
  page, which is invisible to the accessibility snapshot.
- the semantic descriptors the engine already speaks — text, `aria-label`, placeholder, role —
  and a suggested `by_text` / `by_label` / `by_role` locator
- a CSS selector and an XPath as durable fallbacks
- a clipped screenshot of the element
- the operator's optional label ("the submit button", "the price table")

Handles go stale on navigation, surfacing as `StaleHandle`; the agent falls back to the selectors
in the same bundle.

Pins are created only by humans. Agents list and read them.

## 7. Agent surface (MCP)

**Targets are unified.** Every acting tool takes a `target` that is either a handle (`el_x7f2`,
from a pin or a prior `find`) or `{tab, locator}`, where the locator vocabulary is saidkick 1.x's
verbatim: `by_text`, `by_label`, `by_placeholder`, `by_role`, `css`, `xpath`, plus `within_css`,
`nth`, `exact`, `regex`, `wait_ms`. That vocabulary is the best asset 1.x has and it survives
intact; it simply resolves through Playwright instead of hand-rolled CDP and content scripts.

| Group | Tools |
|---|---|
| Contexts | `list_profiles`, `open_context(profile, mode)`, `close_context`, `list_contexts`, `save_profile(context, name)` |
| Tabs | `open_tab`, `close_tab`, `list_tabs`, `navigate(tab, url, wait)` |
| Reading | `snapshot(tab, mode)`, `screenshot(tab, locator?, full_page?)`, `find(tab, locator)`, `console(tab, grep?)`, `network(tab, since?)` |
| Acting | `click`, `type`, `press`, `select`, `hover`, `scroll`, `upload`, `highlight` |
| Human loop | `request_human(context, reason, deadline_s, poll_s)`, `control_state(context)`, `list_pins`, `read_pin` |
| Diagnostics | `events(context, since_seq)`, `start_trace`, `stop_trace` |

Four notes.

**Tool descriptions are a deliverable, not documentation.** They are the only mechanism by which
saidkick can influence agent behaviour, and per §5.1 the agent's own channel to its human is the
most reliable notification path in the system. So the descriptions carry obligations, not just
signatures: `request_human` instructs the agent to relay `reason` to its operator before waiting
and to surface a `still_waiting` rather than loop silently; `open_context` explains when
`ephemeral` is the correct default and that `attached` writes back to real credentials;
`snapshot` states that `aria` is preferred and `html` is a last resort. They are reviewed and
revised like code, and they belong in the same file as the tools so they cannot drift.

**`snapshot` defaults to `mode="aria"`** — an ARIA snapshot, not raw HTML and not `innerText`.
It is compact enough to fit in context, and every node carries a role and accessible name that
map directly onto a `by_role` / `by_label` / `by_text` locator, so an agent that has read the
snapshot already knows how to address everything it can see. `mode="text"` and `mode="html"`
remain available; handing an agent 400KB of raw DOM is how 1.x burns context for no gain.

The API is **`locator.aria_snapshot()`**, which returns a YAML-shaped string:

```
- heading "Fixture" [level=1]
- textbox "Username":
  - /placeholder: your name
- button "Send"
```

Note for implementers: `page.accessibility` **does not exist** in Playwright 1.61 — the old
accessibility namespace was removed, and `page._snapshot_for_ai()` is not present either. Every
snapshot goes through `locator.aria_snapshot()`, scoped to `body` by default and to `within_css`
when supplied. All four semantic locators were verified to map onto `get_by_label`,
`get_by_placeholder`, `get_by_text` and `get_by_role` with matching results.

**`save_profile` closes the bootstrapping loop.** The agent opens an ephemeral context, hits a
login wall, calls `request_human`; the operator takes over, authenticates, releases; the agent
calls `save_profile(ctx, "github")` and that authenticated state is durable. Populating an empty
profile becomes a two-minute flow rather than a chore.

**`events` polls rather than pushes.** MCP has a notification mechanism but client support is
uneven, and betting the agent surface on push would make it work well on some runtimes and
mysteriously not on others. A monotonic sequence number with `since_seq` works everywhere. The
WebSocket stream still exists for the cockpit and for non-MCP clients that want real push.

**Dialogs** get a per-context policy: `auto_dismiss` (default), `auto_accept`, or `ask_human`,
which routes a native `confirm()` into the same request queue the captcha flow uses.

## 8. Human surface (CLI and REST)

The CLI keeps its shape: `saidkick click --tab T --by-text "Send"` reads exactly as it does today.

Changes:

- `saidkick serve` replaces `saidkick start`.
- Tab addressing goes `br-a1b2:15` → `ctx_a1b2:15`.
- New `saidkick profiles` and `saidkick contexts` commands.
- `saidkick quick <url>` opens an ephemeral context and a tab in one call and prints the tab id,
  so `TAB=$(saidkick quick https://example.com)` keeps one-liners as one-liners.

REST mirrors the MCP surface and remains the machine API. `SaidkickClient` is updated in place.

## 9. Error policy

A small closed set, each carrying a next action. For an agent-facing API this matters more than
for a human one: the agent's recovery behaviour is entirely determined by whether the error tells
it what to do.

| Error | HTTP | Meaning |
|---|---|---|
| `ProfileLocked` | 409 | An attached context is already live on that profile. |
| `NoSuchContext` | 404 | Unknown or closed context. |
| `NoSuchTab` | 404 | Unknown or closed tab. |
| `StaleHandle` | 410 | Element handle invalidated (usually navigation). Fall back to selectors. |
| `LocatorNotFound` | 404 | No match. |
| `LocatorAmbiguous` | 400 | Multiple matches; returns the candidate list. Retry with `nth`. |
| `NavigationFailed` | 502 | Navigation did not complete. |
| `HumanHoldsControl` | 409 | A human currently holds the wheel on this context. |
| `HumanTimeout` | 504 | `request_human` deadline elapsed with no response. |
| `DialogBlocked` | 409 | A native dialog is open and the policy is `ask_human`. |
| `EngineCrashed` | 502 | Chromium died; the context is invalid. |

`422` remains Pydantic validation and `500` remains an actual daemon bug. Anything not on this
list is a bug in the daemon, not a condition the agent should be handling.

Ambiguity keeps 1.x's leaf-most disambiguation: an ancestor matching only because a descendant
matches is dropped before the ambiguity check.

## 10. Persistence

`beaver-db` holds:

- the **profile registry** — names, creation time, last use, lock state;
- the **run log** — every engine action with context, tab, locator, result and duration;
- **control transitions** — who took the wheel, when, and why;
- **pending and resolved human requests**, with their resolution notes.

The run log is the cockpit's activity panel and the entire debugging story for "why did the agent
do that." Playwright traces (`start_trace` / `stop_trace`) provide step-level replay through
Playwright's own trace viewer and are stored as files, not in beaver.

## 11. Testing

The layering in §4 is what makes each layer cheap to test.

- **Engine** — real Chromium against a local fixture site checked into `tests/fixtures/site/`:
  forms, a contenteditable editor, an iframe, a native `confirm()`, content that appears after a
  delay. Hermetic, no network. This is where locator and action semantics are pinned down.
- **Session** — no browser. Control arbitration is a state machine and is tested as one: agent
  acts while human holds → `HumanHoldsControl`; release resolves the pending request; deadline
  passes → resolves as `timeout`; barge-in mid-action. These are the tests that would otherwise
  only fail in production during a live rescue.
- **API and MCP** — in-process ASGI through `httpx.ASGITransport`. No server, no port.
- **Pins** — `DOM.getNodeForLocation` against the fixture site at fixed coordinates, deterministic
  because the fixture controls its own layout.
- **Takeover** — the input-forwarding path is tested at the message level (canvas coordinates →
  CDP parameters). The pixel path is verified by hand; there is no honest unit test for "does it
  feel right to type into."
- **Attention overlay** — one non-negotiable test against the fixture site: take an `aria`
  snapshot with the overlay injected and assert it is byte-identical to the snapshot without it,
  and that the injected host element is absent from `find` results. If the overlay is ever
  visible to the agent, the agent will try to click saidkick's own banner. Also asserted:
  title and favicon are restored exactly on resolve and on timeout.

Browser tests carry `@pytest.mark.browser` so `pytest -m "not browser"` stays fast. The existing
`e2e` marker is retired in favour of it.

## 12. Migration and breaking changes

**The MV3 extension is deleted.** With it goes the entire `br-XXXX` addressing scheme, the
content-script locator engine, the offscreen document, and the console-mirroring opt-in
machinery. There is no compatibility shim: every script hardcoding a `br-XXXX:N` id breaks.

**Profiles start empty.** A Playwright-owned Chromium is logged into nothing. The first time an
agent hits a login wall it pauses, the operator authenticates through takeover, and
`save_profile` makes it durable. This is the same mechanic as the 2FA flow, applied to
bootstrapping, so it costs no additional machinery — and profiles become genuinely owned by
saidkick rather than a drifting copy of Chrome's state.

**"Drive my real Chrome" is dropped.** A later addition could reintroduce it cheaply via
Playwright's `connect_over_cdp()` against a Chrome launched with `--remote-debugging-port`,
replacing the whole MV3 subsystem with three lines. It would be a degraded mode by nature — one
shared context, no isolation, no ephemeral contexts — and it is not part of 2.0.

Released as **2.0.0** on PyPI, with the breaking changes called out prominently in the CHANGELOG.

## 13. Build order

Vertical slices, each independently useful.

**VS1 — agent drives, human watches.** Engine (ephemeral context, tab, navigate,
`snapshot(aria)`, click, type) + daemon + ~8 MCP tools + a cockpit that lists contexts and
screencasts one. No takeover, no pins, no on-disk profiles, no beaver. *Demonstrable end state:*
an agent fills out the fixture form while the operator watches it live.

**VS2 — human takes the wheel.** Arbitration state machine, takeover input forwarding,
`request_human`, and all three announcement channels from §5.1 (terminal dashboard, tool-description
obligations, in-page attention overlay). *This is where the thesis is true:* the agent hits 2FA,
says so, gets rescued, continues.

**VS3 — profiles persist.** On-disk profiles, attached mode, `ProfileLocked`, `save_profile`.

**VS4 — pins.** Hit-testing, drag-region, handle minting, confirm-highlight, the pin bundle.

**VS5 — fit and finish.** CLI parity, beaver run log, tracing, console and network, dialog
policy, docs, the 2.0.0 release.

VS1 + VS2 is the whole idea working. VS3–VS5 make it something to live with.

## 14. Open questions

- **Screencast defaults** are reasoned, not measured. The VS1 benchmark sets them.
- **Viewport sizing.** Contexts need a default viewport; whether the cockpit can resize a live
  context (and what that does to a running agent's coordinate assumptions) is deferred to VS2.
- **`saidkick` has no `AGENTS.md`**, only a root `SKILL.md`. The repo should grow one; out of
  scope here.
