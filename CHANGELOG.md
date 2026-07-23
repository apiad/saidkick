# Changelog

All notable changes to this project are documented here. Format: Keep a Changelog.

## [Unreleased]

## [2.1.0] - 2026-07-23

### Added

- **Pins** — a human points at an element in the cockpit (click, or drag a box)
  and the agent gets an addressable reference to it. The registry resolves the
  DOM node via `DOM.getNodeForLocation`, stamps it, and records a bundle:
  descriptors, a suggested locator, durable css/xpath fallbacks, and a clipped
  screenshot.
- MCP tools `list_pins` and `read_pin`; the acting tools (`click`, `type`,
  `press`, `select`, `highlight`, `find`) gain a `handle=` option.
- REST: `GET /contexts/{cid}/pins`, `GET /pins/{handle}`, `POST /tabs/{tid}/pin`;
  a `pin` message on the control socket. Placing a pin does not require holding
  control.
- Cockpit Pin mode (click or drag to place), and `saidkick pins --context C`.
- A stale handle (the page changed) raises `StaleHandle`; the agent falls back
  to the css/xpath/suggested locator in the pin's bundle.

### Notes

- Pins are human-placed only — no MCP tool mints one.


## [2.0.0] - 2026-07-23

Saidkick is now a browser built for agents, with a human supervising it — not a
tool for driving your existing Chrome.

### Breaking

- **The Chrome MV3 extension is deleted**, and with it the hub-and-spoke design,
  the `br-XXXX` addressing scheme, the content-script locator engine, the
  offscreen document, and console mirroring. There is no compatibility shim.
- `saidkick start` → **`saidkick serve`**.
- Tab ids: `br-a1b2:15` → **`ctx_a1b2:3`**.
- Removed commands: `exec`, `logs`, `doctor`, `mirror` — all backed by the
  extension. `dom` and `text` are folded into `snapshot --mode html|text`.
- **Contexts start logged into nothing.** Saidkick drives its own Chromium, not
  your personal profile.
- `SaidkickClient` is rewritten around contexts and tabs.

### Added

- **Isolated browsing contexts.** Each context is its own cookie jar and storage
  partition; tabs inside one share a session. Ephemeral by default.
- **ARIA snapshots** (`snapshot --mode aria`, the default) — a compact outline
  where every entry carries a role and accessible name that map directly onto a
  locator.
- **MCP server** mounted at `/mcp`, with tool descriptions written as
  obligations rather than summaries.
- **Web cockpit** — live CDP screencast on a canvas, quality 60/1280 while
  observing and 95/native the moment a human takes control.
- **Human-in-the-loop.** `request_human` with independent `deadline_s` and
  `poll_s`; every context has exactly one controller; agent mutations fail fast
  with `HumanHoldsControl` while a human drives, and reads keep working.
- **Takeover** — mouse, keyboard and wheel forwarded over CDP, plus a paste box
  using `Input.insertText` for 2FA codes. Control releases automatically when
  the control socket closes.
- **Three announcement channels**: a live Rich dashboard in the serving
  terminal, an obligation in the `request_human` tool description for the agent
  to relay through its own channel, and an in-page attention overlay that is
  invisible to the accessibility snapshot.
- **`saidkick quick URL`** — context and tab in one call, so one-liners stay
  one-liners.
- `SAIDKICK_URL` to point the CLI and client at another daemon.
- A closed error set with stable codes and HTTP mapping.
- `--headful` for watching the browser work.

### Fixed

- MCP is served at `/mcp` rather than `/mcp/mcp` (`streamable_http_path`
  defaults to `/mcp` inside the mounted app).

### Notes

- Persistent profiles, `save_profile`, pins, the run log and trace replay are
  the next slice and are not in this release.
- The MCP SDK enables DNS-rebinding protection: `127.0.0.1:*`, `localhost:*`
  and `[::1]:*` are allowed, so a reverse proxy on a real hostname needs
  `allowed_hosts` widened.

## [0.7.2] - 2026-07-02

### Added

- **`close` command** — `saidkick close --tab T` closes a tab (`/close`
  endpoint + `SaidkickClient.close`). The debugger is detached automatically by
  the existing `tabs.onRemoved` listener. Enables e2e harnesses to clean up the
  tabs they open instead of accumulating them across runs.

## [0.7.1] - 2026-07-02

### Fixed

- **`--wait dom` / `--wait full` no longer time out on fast pages.** The
  navigation wait used CDP `chrome.debugger` `Page.domContentLoaded` /
  `Page.loadEventFired` events, which were routinely missed across a
  `tabs.update` navigation (the page execution context is replaced) — causing a
  spurious 15s "navigation timeout" even on instant localhost pages. Reworked to
  `chrome.tabs.onUpdated` (status) plus a `document.readyState` poll for `dom`,
  using only the existing `tabs` + `scripting` permissions (no manifest change).

### Added

- **`exec --arg`** (repeatable) passes values into the executed JS, read via a
  named `args` array (`args[0]`, `args[1]`, …). Each `--arg` is JSON-parsed,
  else kept as a raw string. Previously `exec` had no way to pass data to the
  page function. Threaded CLI → client → server → extension.

## [0.7.0] - 2026-07-02

### Added

- **`/doctor` endpoint + `saidkick doctor` CLI.** Names the exact connection
  state (server up/down, browsers connected, tab counts) and the next action.
  Surfaces connected browser ids even when a browser reports 0 tabs, so scripts
  can `open` without a prior `tabs` listing. `SaidkickClient.doctor()` added.

### Changed

- **`tabs` distinguishes empty states.** Instead of the single misleading
  "No tabs. Is a browser connected?", it now separates "server up, 0 browsers"
  (needs a Reconnect) from "connected, 0 tabs" (use `open --browser <id>`).

### Fixed

- **Leaf-most `by_text` / `by_label` / `by_placeholder`.** An ancestor that
  matches only because a descendant does is now dropped, so nested text no
  longer trips a spurious "Ambiguous locator: N matches". css/xpath unchanged.
- **`press` help** now steers callers to pass a locator for deterministic
  focus-before-keystroke.

## [0.6.0] - 2026-04-29

### Changed

- **Extension: WebSocket moved to an offscreen document.** The persistent WS to the saidkick server now lives in a Chrome offscreen page (`chrome.offscreen` API) instead of the MV3 service worker. The SW becomes a thin command executor reachable via a long-lived `chrome.runtime.connect()` Port named `"saidkick-cmd"`.

  This closes the SW-idle race that 0.4.5's PING/PONG keepalive could not fully close: in practice, after ~30 seconds of CLI inactivity the SW would die mid-keepalive and `saidkick tabs` would return `[]` for up to 30 seconds (the alarm-watchdog window) before the next reconnect. Offscreen documents are not subject to the SW idle timer, so `browser_id` is now stable across SW deaths and idle windows.

  No CLI changes. Server protocol unchanged. Existing 114 pytest tests pass.

  Manual smoke: load extension → `saidkick tabs` returns within 1s → wait 5 minutes idle → `saidkick tabs` returns within 1s and yields the same `br-XXXX`. Kill the saidkick server and restart it → within ~5s a new `br-XXXX` is minted and commands work.

### Migration notes

- Manifest version bumped from `1.2` to `1.3`. Reload the extension on `chrome://extensions/` after upgrading the package.
- New permission: `"offscreen"`. Chrome will surface a permission warning on extension reload — expected.
- Behavior change for tools observing reconnect events: 0.4.5's popup hint "reconnected as new br-XXXX" will fire less often (only on real WS reconnects: server restart, network blip, manual Reconnect click).

## [0.5.1] - 2026-04-21

### Fixes

- **`--full-page` screenshots now cap at 10,000 pixels of height by default.** Override via `--max-height-px N`. Prevents accidentally 30MB+ PNGs on infinite-scroll pages. Locator-clipped screenshots are unaffected (they already clip to the element).
- **`logQueue` bounded at 500 entries.** Drops oldest-first when over. Prevents SW memory growth when the server is down for a long time.
- **`uniqueSelector` fallback** for edge cases where the ancestor walk produces an empty parts array (e.g. element IS `document.body` or lives outside it). Now returns just the tag name instead of the invalid `"body > "`.

### Internal

- Popup status poll: 1500ms → 500ms for snappier feedback while open.

## [0.5.0] - 2026-04-21

### BREAKING CHANGES

- **Console mirroring is opt-in per tab.** `saidkick mirror on --tab TAB` activates; `saidkick mirror off --tab TAB` deactivates; `saidkick mirror status --tab TAB` queries. Previously every page's console.log was auto-mirrored to the server. Callers relying on auto-mirrored logs must now opt in per tab. Main-world still wraps `console.*` so the instrumentation overhead is unchanged — only the server-side forwarding is gated.

### Features

- **`--by-role` locator** — resolves via CDP `Accessibility.getFullAXTree`. Pair with `--by-text` to disambiguate ("the button named Send"). Background.js translates the AXTree match into a unique CSS selector via `DOM.resolveNode` + `Runtime.callFunctionOn`, then forwards to the content script via the existing CSS-locator path.
- **`--pierce-shadow` locator flag** — when set, text/label/placeholder scans walk into open shadow roots (default off for perf + back-compat). CSS selectors run separately on each shadow root when pierce is enabled.

### Fixes

- **Highlight prev-style preserved on back-to-back calls.** A `WeakMap<Element, {prev, activeCount}>` captures the original outline/box-shadow/transition on the first highlight of an element; subsequent highlights reuse it and refcount. Only the last-expiring timeout restores, so the element actually returns to its original state instead of stuck-red.
- **`ensureDebuggerAttached` propagates `Page.enable` / `Runtime.enable` errors.** Previously swallowed via an unused callback; now rejects the promise so navigation / execute commands surface the failure instead of hanging.

### Internal

- `Locator` mixin gains `by_role` and `pierce_shadow`. Client `_locator_params` and CLI `_locator_kwargs` forward them. All selector-using REST endpoints carry them through to the extension payload unchanged.

## [0.4.5] - 2026-04-21

### Fixes

- **MV3 service-worker session durability.** The extension now sends a `PING` frame every 20s; the server replies with `PONG`. Active WebSocket traffic within Chrome's 30s SW-idle window keeps the service worker awake — prior to this, `br-XXXX` IDs changed silently every time the SW went idle, invalidating any `$TAB` captured from earlier commands.
- **Alarm-based reconnection watchdog.** `chrome.alarms` fires every 30s; if the socket isn't `OPEN`, re-runs `connect()`. Survives SW death (the prior `setTimeout(connect, 5000)` chain died with the SW).
- **Debugger detach on tab close.** Tracks attached tab IDs in a Set; on `chrome.tabs.onRemoved`, calls `chrome.debugger.detach`. Stops the "Saidkick started debugging this browser" yellow banner from accumulating across closed tabs.

### Features

- **Popup shows "reconnected as new br-XXXX".** When the SW reconnects and the server issues a different browser ID, the popup surfaces both so callers know their previous `$TAB` is stale.

### Internal

- `SaidkickManager` tracks `last_seen: Dict[str, float]` per browser, updated on every inbound frame. Not exposed in an endpoint yet; plumbing for future health views.
- Server routes `PING` → `PONG` in the WS endpoint; every inbound frame touches `last_seen`.
- `manifest.json` adds `alarms` permission.

## [0.4.4] - 2026-04-21

### Security

- **Default host is now `127.0.0.1` instead of `0.0.0.0`.** Previously anyone on the LAN could hit `/execute` and run arbitrary JS in the user's logged-in Chrome session. Opting into LAN/remote access requires `--host 0.0.0.0` and prints a ⚠ warning at startup. **Breaking for anyone who relied on the default LAN exposure.**

### Fixes

- **React-compatible `type`.** The non-contenteditable branch now calls the native value setter via `Object.getOwnPropertyDescriptor(proto, "value").set` instead of assigning `.value` directly. Fixes React / Preact / Vue-2 / Svelte inputs where framework state tracked through a prototype-level setter was bypassed, causing typed text to vanish on next re-render.
- **Double-injection guard.** `content.js` and `main_world.js` now no-op if already installed on the same page — the manifest `content_scripts` entry and the programmatic `chrome.scripting.executeScript` fallback used to race and install twice on fresh tabs, producing duplicate RESPONSEs and compound console overrides.
- **Extension-side error hygiene.** `socket.onmessage` wraps the command dispatch in a top-level try/catch; unhandled exceptions now bubble up as an error `RESPONSE` instead of silently 504-ing the server. Binary WebSocket frames and malformed JSON are skipped cleanly.

### Internal

- `manifest.json` sets `minimum_chrome_version: "111"` — we depend on MV3 content-script `world: "MAIN"` (Chrome 111+) and CDP features that shipped later.

## [0.4.3] - 2026-04-21

### Features

- **`POST /scroll`** + `saidkick scroll --tab T --by-text X [--block center|start|end|nearest] [--behavior auto|smooth]` — bring a located element into the viewport. Essential before screenshotting offscreen content; useful for infinite-scroll content extraction.
- **`POST /highlight`** + `saidkick highlight --tab T --by-text X [--color red] [--duration-ms 2000]` — draw a temporary ring around a located element to point the user at it. Pair with `screenshot` to send the user an annotated image. Default duration 2s; `--duration-ms 0` persists until page reload. Uses `outline` (no layout shift) + soft halo `box-shadow`; restores original styles on timeout.

Both accept the full locator surface (`--by-text`, `--by-label`, `--by-placeholder`, `--css`, `--xpath`, `--within-css`, `--nth`, `--exact`, `--regex`, `--wait-ms`).

## [0.4.2] - 2026-04-21

### Internal

- Release workflow switched to PyPI Trusted Publishing (OIDC). No longer relies on a `PYPI_TOKEN` repo secret — uses the `pypi` environment and `id-token: write` permission to authenticate against PyPI via GitHub's OIDC provider. v0.4.1 tagged but publish-pypi failed due to missing secret; v0.4.2 is the first tag to actually reach PyPI.

## [0.4.1] - 2026-04-21

### Fixes

- `navigate` and `open` with `--wait dom|full` no longer race the page-load event on fast pages. Previously `chrome.tabs.update`/`create` would fire the navigation before the debugger listener was armed, and on fast-loading pages (e.g. play2048.co) the `Page.domContentLoaded` event would already be past by the time we subscribed, producing a spurious `navigation timeout after 15000ms`. Now we attach the debugger and arm the listener BEFORE dispatching the real navigation — `open` additionally starts on `about:blank` so the initial tab-creation navigation doesn't consume our event. Surfaced by a real-world smoke test on play2048.co.

## [0.4.0] - 2026-04-21

### BREAKING CHANGES

- `exec` now wraps user code in `(async () => { ... })()` so scope doesn't leak between calls. **Callers must `return` their result**; a bare expression like `document.title` no longer becomes the response payload — use `return document.title`. Fixes the silent scope-collision footgun where `const x = 1` in one call caused the next to throw on redeclaration.

### Features

- Semantic locators — `--by-text`, `--by-label`, `--by-placeholder` on every selector-using command (`dom`, `text`, `click`, `type`, `select`, plus the new `find`, `press`, `screenshot`). `--within-css` scopes the search; `--nth N` disambiguates multi-matches; `--exact` and `--regex` adjust match semantics. Ambiguity without `--nth` returns 400.
- `GET /find` + `saidkick find --tab X --by-text ...` — debug tool that returns up to 50 matches as JSON with `selector`, `tag`, `role`, `name`, `text`, `rect`, `visible`.
- `POST /press` + `saidkick press KEY --tab X [--mod ctrl,shift] [--by-* ...]` — dispatches keyboard events via CDP `Input.dispatchKeyEvent`. Optional locator focuses the target first.
- `GET /screenshot` + `saidkick screenshot --tab X [--output PATH]` — captures a PNG via CDP `Page.captureScreenshot`. Optional locator clips to an element's bounding rect; `--full-page` captures beyond the viewport.
- `type` on `contenteditable` elements now uses `document.execCommand("insertText", ...)` — fixes WhatsApp, Slack, Discord, Gmail compose, GitHub comments, Notion, and every other Lexical / ProseMirror / Quill / Slate / Draft-backed rich-text field.

### Internal

- New `Locator` Pydantic mixin and `_validate_locator` / `_validate_required_locator` helpers. Every selector-using endpoint now validates "exactly one locator is set" at the boundary with a 400 on zero or multiple.
- Content-script `collectMatches` → `collectLocator`; single resolver backs CSS, XPath, text, label, and placeholder locators.
- `FOCUS` and `RESOLVE_RECT` content-script helpers so background-side `PRESS` / `SCREENSHOT` can target specific elements.

## [0.3.0] - 2026-04-21

### Features

- `POST /navigate` and `saidkick navigate --tab ID URL [--wait dom|full|none] [--timeout-ms N]` — send a tab to a URL. Returns the final URL after redirects.
- `POST /open` and `saidkick open --browser BR URL [--wait ...] [--timeout-ms N] [--activate]` — open a URL in a new tab; stdout is the composite `br-XXXX:N`, pipe-ready.
- `GET /text` and `saidkick text --tab ID [--css SCOPE] [--wait-ms N]` — return `innerText` of the page or a CSS-scoped element.
- `--wait-ms N` on `dom`, `click`, `type`, `select`, `text`: content-script polls the selector (every 100ms up to `N`ms) before acting. Default 0 preserves prior behavior.

### Fixes

- HTTP status codes are correct now. 0.2.0 returned `500` for caller-observable failures (`Element not found`, `Ambiguous selector`, `Option not found`, `Element is not a <select>`). These now return `404` (not found) and `400` (malformed / ambiguous) respectively. Upstream browser errors that we can't classify return `502`; timeouts return `504`. `500` is reserved for server bugs.

## [0.2.0] - 2026-04-21

### BREAKING CHANGES

- All action endpoints (`/dom`, `/execute`, `/click`, `/type`, `/select`) now require a `tab` parameter in the form `br-XXXX:N` (query param on GET, body field on POST).
- All CLI action commands (`dom`, `click`, `type`, `select`, `exec`) now require a `--tab br-XXXX:N` flag.
- `SaidkickClient` action methods now require `tab` as their first argument.
- Extension ↔ server protocol adds a `HELLO` handshake frame; the extension must be reinstalled from this version of the repo.
- The old tab-selection heuristic inside `background.js` (active tab → localhost:8000/8088 → first non-chrome tab) has been removed.

### Features

- `GET /tabs` endpoint aggregates tabs across all connected browsers. Optional `?active=true` filter.
- `saidkick tabs` CLI command; `--active` filter for the currently-focused tab.
- Multi-browser support: server assigns an ephemeral `br-XXXX` ID on each WS connection and tracks them in a dict keyed by `browser_id`.
- `/console` and `saidkick logs` support a `browser` / `--browser` filter. Every stored log entry is stamped with its source `browser_id`.

## [0.1.0]

- Initial release: FastAPI server, Chrome MV3 extension, Typer CLI, Python client for remote browser inspection and automation.
