---
name: saidkick
description: Drive a real Chrome session (logged-in tabs, cookies, extensions) from the terminal via saidkick. Use for any browser-automation task when the user has saidkick running — list tabs, navigate, semantic-click, type into rich editors, dispatch keyboard shortcuts, screenshot, execute JS. Saidkick is the preferred tool for browser work over MCP-based alternatives when available.
---

# Using saidkick

This is the how-to for an AI agent working with saidkick. Saidkick is a self-hosted CLI + extension that drives the user's actual Chrome (not a headless browser) end-to-end.

## When to use

Prefer saidkick for any browser task where:

- The user has `saidkick start` running and the extension connected (check with `saidkick doctor`).
- The task involves a real user session — something logged in, something with cookies, something that requires the browser extensions the user already has.
- You're scripting a multi-step flow (open → click → type → screenshot).
- You want to read what the user is looking at right now without them copy-pasting it.

Reach for `claude-in-chrome` or another MCP browser tool only when saidkick is genuinely missing the capability (currently: `by_role` locators + full accessibility-tree dump — coming in 0.5.0).

## Preflight

Before any saidkick command, verify the server is up and at least one browser is connected:

```bash
saidkick doctor
```

`doctor` names the exact state and the fix:
- **server down** → run `saidkick start`.
- **server up, 0 browsers** → ask the user to click the Saidkick extension icon and hit **Reconnect**. Do *not* restart the server — that orphans the extension WebSocket.
- **connected** → it lists each browser id and tab count (shown even when a browser reports 0 tabs, so you can `open --browser <id>` without a `tabs` listing first).

Don't guess at tab IDs; discover them with `saidkick tabs` or capture them from `saidkick open`.

## The composite tab ID

Every command that touches a tab needs `--tab br-XXXX:N`:
- `br-XXXX` — 4 hex chars identifying the connected browser (assigned at handshake; not persistent across reconnects).
- `N` — Chrome's native `tab.id` int.

Never hardcode either half. Always discover via `saidkick tabs` or capture from `saidkick open`:

```bash
TAB=$(saidkick open --browser br-a1b2 https://example.com/)
saidkick text --tab "$TAB" --css "h1"
```

## Locators — the idea that matters most

Every selector-using command (`dom`, `text`, `click`, `type`, `select`, `find`, `press`, `screenshot`) takes exactly one locator. Prefer *semantic* over CSS when you can:

| Intent | Locator |
|---|---|
| "Click the thing labeled Send" | `--by-text "Send"` |
| "Type in the password field" | `--by-label "Password"` |
| "The search box with placeholder 'Search…'" | `--by-placeholder "Search"` |
| "The exact CSS class I know" | `--css ".my-class"` |
| "Inside the modal only" | `--within-css ".modal" --by-text "Confirm"` |
| "The 3rd match" | `--by-text "Item" --nth 2` (0-indexed) |
| "Full-string equal only" | `--by-text "OK" --exact` |
| "Regex match" | `--by-text "^Save.*" --regex` |

Substring match is case-insensitive by default — good enough for 90% of "the button that says X." Escape hatches (`--exact`, `--regex`) for the rest. `--by-text` resolves to the **leaf-most** match, so nested markup like `<li><span>Welcome.md</span></li>` targets the `<span>` rather than erroring "ambiguous" on every ancestor.

**Exactly one** of the locator options must be set per command. Zero → 400. Two → 400. Ambiguous match (2+ elements) → 400 unless you pass `--nth` to disambiguate.

When a locator doesn't work, use `saidkick find --tab T --by-text X` to see what matches (returns JSON with `selector`, `name`, `text`, `rect`, `visible`).

## Common patterns

### Read a page

```bash
saidkick text --tab "$TAB"                         # whole body
saidkick text --tab "$TAB" --css "main"            # just the main content
saidkick text --tab "$TAB" --by-label "Article"    # semantic scope
```

### Drive a form

```bash
saidkick click --tab "$TAB" --by-text "Sign in"
saidkick type "user@example.com" --tab "$TAB" --by-label "Email"
saidkick type "hunter2" --tab "$TAB" --by-label "Password" --clear
saidkick press Enter --tab "$TAB"
```

### Wait for an element that may render late (SPAs)

```bash
saidkick click --tab "$TAB" --by-text "Load more" --wait-ms 5000
```

All selector-using commands take `--wait-ms N` — they'll poll every 100ms up to N milliseconds before failing. Default is 0 (fail immediately).

### Open a URL from scratch and drive it

```bash
TAB=$(saidkick open --browser br-a1b2 https://news.ycombinator.com/)
saidkick text --tab "$TAB" --css ".athing:nth-of-type(1) .titleline"
```

`saidkick open` prints the new composite `br-XXXX:N` on stdout — pipe it.

### Navigate an existing tab

```bash
saidkick navigate --tab "$TAB" https://another.example.com/
```

Both `open` and `navigate` take `--wait dom` (default — fires on `Page.domContentLoaded`), `--wait full` (load event fired, slower), or `--wait none` (return immediately; you poll yourself).

### Take a screenshot (great for checking state)

```bash
saidkick screenshot --tab "$TAB" --output /tmp/state.png   # viewport, to file
saidkick screenshot --tab "$TAB" > /tmp/state.png          # viewport, stdout raw bytes
saidkick screenshot --tab "$TAB" --by-text "Article" --output /tmp/clip.png   # clipped to element
saidkick screenshot --tab "$TAB" --full-page --output /tmp/full.png           # beyond viewport
```

Screenshots are cheap and extremely useful for verifying a multi-step flow landed — much more informative than probing the DOM for expected strings.

### Pointing the user at a specific element

When you're guiding the user — "click the Deploy button" — you have two complementary primitives:

```bash
saidkick scroll    --tab "$TAB" --by-text "Deploy"           # bring it into the viewport
saidkick highlight --tab "$TAB" --by-text "Deploy"           # 2-second red ring around it
saidkick screenshot --tab "$TAB" --output /tmp/point.png     # capture so the user sees the ring
```

Rules of thumb:

- **Always `scroll` before `screenshot` on an offscreen element.** The screenshot captures the viewport; an element you find in the DOM isn't necessarily on-screen.
- **`highlight` takes `--duration-ms`** — default 2000ms (fades on its own), `0` = persist until page reload. Use `0` when you're about to send the user a screenshot; default when you're narrating live and moving on.
- **`--color` is any CSS color.** Use meaning: red (`#ff3b30`) = attention / danger, amber (`#f59e0b`) = warning / error in form field, green (`#34c759`) = success / "you just did this." Keep it consistent across a session.
- **`scroll --block`**: `center` (default — most forgiving), `start` (pin to top — good for reading sequential content), `end` (bottom of viewport), `nearest` (minimal scroll).
- **`scroll --behavior`**: `auto` (instant — default, good for scripting) or `smooth` (animated — good when the *user* is watching). On `smooth`, saidkick waits ~400ms so the returned rect reflects the final position.

Typical pattern for "point at this":

```bash
TAB=br-XXXX:N
LABEL="Invite team member"
saidkick scroll    --tab "$TAB" --by-text "$LABEL"
saidkick highlight --tab "$TAB" --by-text "$LABEL" --duration-ms 0
saidkick screenshot --tab "$TAB" --output /tmp/next-step.png
# show /tmp/next-step.png to the user; the ring is visible in the image
```

Typical pattern for infinite-scroll content extraction:

```bash
# Scroll to the last known item, wait for the next batch to load, read it
saidkick scroll --tab "$TAB" --css ".feed-item:last-child" --behavior auto
saidkick dom --tab "$TAB" --css ".feed-item" --all --wait-ms 3000
```

### Keyboard shortcuts (Ctrl+K command palettes, etc.)

```bash
saidkick press k --tab "$TAB" --mod ctrl        # Ctrl+K (Slack, Linear, VS Code-for-web)
saidkick press Escape --tab "$TAB"              # close modal
saidkick press ArrowDown --tab "$TAB"           # navigate a dropdown
```

Modifiers: `ctrl`, `shift`, `alt`, `meta`. Comma-separated (`--mod ctrl,shift`) or repeated (`--mod ctrl --mod shift`) both work.

### Arbitrary JS (the escape hatch)

When no primitive fits (code runs in an `async function` — you must `return`):

```bash
echo 'return document.cookie' | saidkick exec --tab "$TAB"
echo 'return Array.from(document.scripts).map(s => s.src)' | saidkick exec --tab "$TAB"

# Pass values in with --arg (repeatable); read them as args[0], args[1], …
# Each --arg is JSON-parsed (else a raw string). Safer than string-building code.
saidkick exec --tab "$TAB" 'return document.querySelector(args[0])?.value' --arg '#email'
```

### Closing a tab

Dispose of a tab you opened (e.g. cleaning up after a scripted flow):

```bash
saidkick close --tab "$TAB"
```

**Important:** user code must `return` a value — `exec` wraps it in `(async () => { ... })()` so scope doesn't leak between calls. A bare `document.title` does nothing; use `return document.title`. Top-level `await` works because of the async wrapper.

## Gotchas

- **Connection state.** Run `saidkick doctor` when anything looks off — it separates "server down" (run `start`), "server up, 0 browsers" (user clicks **Reconnect**), and "connected". Since 0.6.0 the WebSocket lives in an offscreen document, so the old MV3 service-worker idle drops are rare. Never restart the server to fix a browser-side disconnect — that orphans the extension WS; a Reconnect click is the fix.
- **Content-script not in older tabs.** Tabs that were already open before the extension was installed/reloaded don't have `content.js` yet — the extension injects it lazily on first command. No action needed; it works.
- **Locator ambiguity = 400.** If a locator matches multiple elements and you don't pass `--nth`, you get a 400 with the match count. Use `saidkick find` to inspect matches, pick an `--nth`, tighten with `--within-css`, or use `--exact`/`--regex`.
- **Rich-text editors.** `saidkick type` correctly handles `contenteditable` via `document.execCommand("insertText", ...)` — works on Lexical, ProseMirror, Slate, Quill, Draft. For older versions or edge cases, fall back to `saidkick exec` with a targeted `execCommand`.
- **Destructive actions are real.** This is the user's actual browser. A "click Send" is a real send. A "delete" button is a real deletion. Pause for confirmation on anything that affects shared state or external people, even if the user asked for it earlier in the conversation (they may not be thinking about this specific tab).
- **exec IIFE-wrap requires return.** 0.4.0 breaking change. `document.title` as a bare expression no longer returns anything — write `return document.title`.

## Status and debug

```bash
saidkick doctor                           # connection state + next action
saidkick tabs                             # who's connected
saidkick logs --limit 20                  # last 20 browser console lines
saidkick logs --grep "error" --browser br-a1b2   # filter
saidkick find --tab "$TAB" --by-text "X"  # locator debugging
```

Error codes you'll see:
- `400` — malformed input (bad tab ID, bad URL, ambiguous locator, wrong element type).
- `404` — resource not found (browser not connected, tab closed, element missing).
- `422` — pydantic validation (missing required field).
- `502` — upstream (browser) hit an error we can't classify.
- `504` — timeout (command, navigation, or wait_ms).
- `500` — **server bug**. Report it.

## Workflow shape

For a typical driving task:

1. `saidkick tabs` — confirm connection, capture the composite ID you need.
2. `saidkick find --tab $TAB --by-text X` — sanity-check your locator *before* firing the destructive command.
3. Run the primitive(s).
4. `saidkick screenshot --tab $TAB --output /tmp/after.png` to verify, then `Read` the PNG to inspect.
5. If it failed, look at `saidkick logs --grep ERROR` for browser-side errors.
