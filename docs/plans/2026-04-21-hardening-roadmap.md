# Extension Hardening Roadmap

> **Status:** approved direction after the 2026-04-21 extension audit. Orders the ~20 findings into four release chunks by "pain avoided per line of code changed." Each release ships independently. This doc is the umbrella — detailed per-release implementation plans get written at the start of each chunk.

The audit produced 4 critical, 6 high, 6 medium, 3 low-priority items. This plan bundles them into four releases (0.4.4 → 0.5.1) ordered by user-observable pain.

---

## Release 0.4.4 — "silent-failure fixes" (additive, non-breaking)

**Goal:** Eliminate the three silent-failure modes that are losing data *today* — duplicate responses, React-form input dropping, and unauthenticated LAN exposure — without adding any new user-facing surface.

**Scope:**

1. **[Crit #3] Double-injection guard.** Top of `content.js` and `main_world.js`:
   ```js
   if (window.__saidkickInstalled) return;
   window.__saidkickInstalled = true;
   ```
   Prevents the "manifest auto-inject races programmatic inject" case from registering two message listeners / two console overrides. ~4 lines.

2. **[Crit #4] React-compatible input setter.** Rewrite the `TYPE` non-contenteditable branch in `content.js` to call the prototype's native `value` setter, not assign directly. Covers React/Preact/Vue-2/Svelte-controlled inputs. ~10 lines.
   ```js
   const proto = element.tagName === "TEXTAREA"
       ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
   const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
   setter.call(element, payload.clear ? payload.text : element.value + payload.text);
   element.dispatchEvent(new Event("input", { bubbles: true }));
   ```

3. **[Crit #2a] Localhost default.** Change `saidkick start` default host from `0.0.0.0` to `127.0.0.1`. Document `--host 0.0.0.0` as the opt-in for LAN/VPS exposure with a big red warning in the CLI help text and README. **Breaking for anyone relying on LAN access by default** — called out in CHANGELOG as a security fix.

4. **[High #5a] Guard against parse-error / socket-closed races.** Wrap `socket.onmessage` body in try/catch; check `socket?.readyState === WebSocket.OPEN` before every outbound `.send()`. On uncaught exceptions, try one last-ditch error RESPONSE if the socket's alive. Converts current silent 504s into explicit 502s with useful messages.

5. **[Low] Manifest `minimum_chrome_version: "111"`.** We use `world: "MAIN"` content scripts and CDP features that require Chrome 111+. One-line safety net.

**Files touched:** `content.js`, `main_world.js`, `background.js`, `cli.py`, `server.py` (host default), `manifest.json`, `README.md`, `CHANGELOG.md`, `pyproject.toml`.

**Tests:**
- Double-injection: add content-script-side `window.__saidkickInstalled` guard tests via `exec` round-trip (manual smoke).
- React input: integration test via a tiny HTML fixture in `tests/assets/` + pytest-managed headless Chrome? Too much for this chunk. Manual smoke on github.com comment box (Lexical) and `react.dev` playground.
- Localhost default: server unit test that `saidkick start --help` mentions localhost, and that the REST endpoint refuses when bound to 127.0.0.1 from a simulated external IP — actually too involved, skip and rely on integration.
- Socket guards: unit test the guard helpers in `background.js` — actually untestable without a DOM test harness; manual + inspection.

**Breaking changes:** host default. Callout in CHANGELOG. Likely zero real users affected but note it.

**Blast radius:** small, contained to extension JS + one CLI default + one manifest key.

**Estimate:** 0.5 day. Priority: ship this week.

---

## Release 0.4.5 — "session durability" (additive)

**Goal:** Make saidkick survive the MV3 service-worker idle cycle without silently losing state. User-observable payoff: `br-XXXX` stays stable across long idle periods; debugger banners stop accumulating across tabs.

**Scope:**

1. **[Crit #1] SW keepalive.** Every 20s (inside the 30s idle window), the extension sends a `PING` frame to the server; server responds with `PONG`. Active WebSocket message traffic inside 30s resets Chrome's SW idle timer (per MV3 spec since Chrome 116). Implementation:
   - Extension: `setInterval(() => socket.readyState === OPEN && socket.send(JSON.stringify({type: "PING"})), 20000)`.
   - Server: new `PING → PONG` handler in `websocket_endpoint`. Doesn't touch request/response plumbing.
   - Bonus: record `last_seen` per browser on the server; `/tabs` can surface "last seen 25s ago" as a health signal in the popup.

2. **[Crit #1 follow-on] `chrome.alarms` belt-and-braces.** Even with keepalive, if the server goes away the extension will drop the WS and the SW may still idle. Register a `chrome.alarms` entry every 30s that calls `connect()` if `socket?.readyState !== OPEN`. Alarms survive SW death and fire to wake it. ~10 lines.

3. **[Crit #1 follow-on] Expose the reconciliation to the user.** When `browser_id` changes (server saw a new handshake because SW restarted), the popup should flag it: "reconnected as br-XXXX (was br-YYYY)". Users learn to re-capture `$TAB` values. Or the popup gains a "stale? reconnect" hint. Small UX patch.

4. **[High #7] Debugger detach-on-tab-close.** Track attached tab IDs in a `Set`. Listen for `chrome.tabs.onRemoved` → remove from Set + `chrome.debugger.detach`. On SW startup, clean up orphans via `chrome.debugger.getTargets()`. Removes the "yellow banner accumulation" failure mode.

5. **[High #5b] Remaining `socket.send` hardening** missed in 0.4.4 (all the per-branch handlers in `onmessage` that directly call `socket.send`). Centralize via a `sendResponse(id, success, payload)` helper that checks readyState. ~20 lines of refactor.

**Files touched:** `background.js`, `popup.js`, `popup.html`, `server.py` (PING handler + last_seen tracking).

**Tests:** unit test the `last_seen` server-side; extension-side keepalive is manual smoke ("leave saidkick idle for 5 minutes, `br-XXXX` unchanged").

**Breaking changes:** none.

**Estimate:** 1 day.

---

## Release 0.5.0 — "semantic layer + privacy" (minor bump, some breaking)

**Goal:** Ship the deferred AXTree / `by_role` work alongside the privacy-sensitive console-mirror redesign. Both touch content-script plumbing enough to warrant a coordinated chunk.

**Scope:**

1. **[Originally deferred] `by_role` locator + AXTree dump.** Uses CDP `Accessibility.getFullAXTree`. See the 0.4.0 spec's deferred section. ~200-line addition to background.js (background-side AXTree walker + DOM.resolveNode to map back to DOM nodes) + content.js passthrough.

2. **[High #9 + High #15] Opt-in console mirroring.** Stop auto-mirroring every page's console to the server by default. Replace the auto-install in `main_world.js` with an on-demand path:
   - New command `saidkick mirror on --tab X` / `saidkick mirror off --tab X`.
   - Extension tracks mirrored tab IDs in chrome.storage.session.
   - main_world.js reads the set at install time and only overrides console when that tab is mirrored.
   - **Breaking:** `saidkick logs` no longer populates from every tab by default. Callers who relied on it must opt in per tab. Aggressive but correct given the privacy concern.
   - Also handles `console.info` and `console.debug` while we're redoing the overrides.

3. **[High #6] Highlight WeakMap for prev-style hygiene.** Store prev styles on a `WeakMap<Element, OriginalStyles>` on first highlight; subsequent highlights reuse the stored value. Refcount active highlights so the last timeout is the one that restores. ~30 lines in content.js.

4. **[High #8] Shadow-DOM-aware locators.** Add a `pierce_shadow: bool` field to the Locator mixin (default `false` for backcompat). When true, `collectLocator` walks shadow roots recursively. Opt-in because piercing shadow has real perf cost on deep pages. ~40 lines in content.js.

5. **[High #10] `ensureDebuggerAttached` error propagation.** Reject on `chrome.runtime.lastError` in the `Page.enable`/`Runtime.enable` callbacks instead of silently resolving. ~6 lines.

**Files touched:** `background.js`, `content.js`, `main_world.js`, `server.py`, `client.py`, `cli.py`, docs.

**Tests:**
- AXTree resolution: new `tests/test_axtree.py` with fixture tree + server-side locator-resolver tests.
- `pierce_shadow` toggle: server-side test that the field propagates to the extension payload. Content-side is manual smoke.
- Console mirroring opt-in: server-side test for `/mirror` endpoints; behavior is manual smoke.

**Breaking changes:**
- Console auto-mirroring off by default → callers must `saidkick mirror on --tab X`. Major privacy/correctness win, worth the break at 0.5.0.

**Estimate:** 3–4 days.

---

## Release 0.5.1 — "sharp-edge polish" (additive patch)

**Goal:** Wrap up the medium-severity audit items. No new primitives; just rounds off failure modes.

**Scope:**

1. **[Medium #11] `--max-height-px` clamp on `--full-page` screenshots.** Default 10000. Exceeding the cap returns a 400 with "full-page height exceeds --max-height-px; set a larger cap or crop with a locator." Prevents accidentally 30MB payloads.

2. **[Medium #12] Bounded `logQueue`.** Cap at 500; drop-oldest when over. One line in `background.js`.

3. **[Medium #13] Binary WS frame guard.** `if (typeof event.data !== "string") return;` at the top of `onmessage`. One line.

4. **[Medium #14] `uniqueSelector` fallback.** If the ancestor walk produces an empty `parts` array, return `el.tagName.toLowerCase()` with a `:nth-of-type(N)` suffix. ~5 lines.

5. **[Medium #16] Popup poll rate 1500 → 500ms.** One-line change; popup feels more responsive.

6. **[Low] `chrome://` / `chrome-extension://` tab_id guard.** If caller hand-passes a composite pointing at a chrome:// URL, return a clean 400 instead of the opaque `cannot access a chrome:// URL`.

**Files touched:** `background.js`, `content.js`, `popup.js`, `server.py`.

**Tests:** unit tests for the clamp + the guards.

**Breaking changes:** none.

**Estimate:** 0.5 day.

---

## Observed friction (agent sessions)

Running tally of pain points hit while driving saidkick from an agent. Each is a candidate for a future release; not yet sequenced.

- **2026-05-12 — `--by-text` matches every ancestor of the leaf.** Clicking a sidebar item `<li><span class="tree-note">Welcome.md</span></li>` with `--by-text "Welcome.md"` returned "Ambiguous locator: found 8 matches" — the `<span>`, its `<li>`, and ~6 ancestors (`#tree`, `aside`, `main`, `body`, …) all contain the substring. Worked around with `saidkick exec` to find the smallest matching element and `.click()` it. *Proposed:* when `--by-text`/`--by-label`/`--by-placeholder` would be ambiguous purely because of nesting, prefer the leaf-most (smallest-subtree, or smallest bounding-box) match before declaring ambiguity — i.e. an ancestor that matches *only* because a descendant does shouldn't count. Alternatively a `--leaf` flag. Cheap heuristic, removes a very common `--nth`/`exec` fallback.

- **2026-06-01 — no `upload` command; programmatic file inputs on React are flaky.** Driving Substack's post editor (Tiptap on a React-controlled `<input type="file">`) to upload a cover image. Tried `input.files = dt.files` after `new DataTransfer().items.add(file)` — silently no-op. Switched to the React-aware pattern `HTMLInputElement.prototype.files`-descriptor setter + paired `input` + `change` events; Substack went into `Loading…` state and never resolved. Also tried clipboard-image-paste (`xclip -t image/jpeg`) + `saidkick press v --mod ctrl` — synthetic Ctrl+V doesn't carry trusted clipboard data, no paste fired. Ended up surfacing the file path to the user and asking them to drag-drop. *Proposed:* first-class `saidkick upload --tab T <locator> --file /path/to.jpg` that uses CDP `Page.handleFileChooser` (or `DOM.setFileInputFiles`) to drive uploads at the browser-trust layer rather than the DOM. Resolves the entire class of "every page that takes a file" — Substack covers, Gmail attachments, GitHub PR file uploads, Notion media, every form with `<input type=file>`. High-impact, well-scoped against the CDP API; one tool, no flags beyond locator + path.

- **2026-06-01 — synthetic `Ctrl+V` doesn't trigger paste handlers in modern editors.** Same session, tried to paste rich-text HTML (and separately image data) into Tiptap/ProseMirror via `xclip` + `saidkick press v --mod ctrl`. The keypress is delivered (the editor receives a `keydown` event) but the browser's clipboard-paste pipeline requires `isTrusted: true` events to release clipboard contents to JS. Editors that read clipboard via the modern `ClipboardEvent` path therefore see an empty payload. Fallback for HTML was `editor.commands.setContent()` via Tiptap's own API — worked, but only because Tiptap happened to expose `editor` on the contenteditable DOM node. *Proposed:* `saidkick paste --tab T <locator> [--text … | --html … | --image /path]` that does the right thing via CDP `Input.dispatchKeyEvent` *with* a synchronous clipboard write via `Browser.grantPermissions` + a real clipboard write, OR a direct execution of the editor's own paste handler with proper `DataTransfer`. Same shape of fix as `upload` — both are "the synthetic-event path doesn't pass browser-trust gates; need CDP." Could land as a single CDP-backed `input` subcommand family (`upload`, `paste`, `drop`) since they share the trust-layer plumbing.

## Items explicitly out of scope

- **SW crash recovery beyond what's already there** — `chrome.alarms` + keepalive (in 0.4.5) covers the realistic failure modes; a full state-restore protocol would be major engineering with marginal payoff.
- **Network-request inspection** — separate roadmap item (Tier 2 in the repo node), not audit-driven.
- **Record/replay macros** — deferred from 0.4.0 design; still not pulling its weight.
- **Multi-browser switching across profiles** — already out of scope; chrome-only is fine.

## Sequencing and delivery

Ship 0.4.4 and 0.4.5 back-to-back over ~2 days. They're small and self-contained and directly address user-observable pain (data loss + session churn). 0.5.0 is a week's work and gets its own spec + plan in the same shape as 0.4.0. 0.5.1 is cleanup — batch with any other small items that accrue between 0.5.0 and whatever comes next.

Suggested release-note cadence:

- 0.4.4 → "Critical fixes: React forms, double-injection, localhost-default"
- 0.4.5 → "Session durability: SW keepalive, debugger hygiene"
- 0.5.0 → "Accessibility locators + opt-in console mirror"
- 0.5.1 → "Sharp-edge polish"

## Starting point

When Alex says "go on 0.4.4," the next step is writing the detailed implementation plan (spec-then-plan style like we did for 0.4.0) for just the 0.4.4 items, then executing. Nothing in this roadmap commits to the implementation detail — it commits to the *order*.

## Observed friction (running log)

- **2026-06-08 — Ctrl+V paste gap re-confirmed (HTML into Substack/Tiptap).** Pasting a 13KB HTML fragment (a rendered Transcendent Chronicles story) into Substack's post editor. `saidkick press Ctrl+v --tab T --css ".post-editor .ProseMirror"` delivered the keydown but Tiptap saw no clipboard data; same for a manually-dispatched `ClipboardEvent` with `DataTransfer.setData('text/html', …)`. Worked via `document.execCommand('insertHTML', false, html)` after focus + caret-at-end via `Range`/`Selection` — corroborates the 2026-06-01 proposal. One extra wrinkle: HTML with whitespace between block tags (`</p>\n<p>`) caused Tiptap to insert visible empty paragraphs between every block, doubling visual spacing; `re.sub(r'>\s+<', '><', html)` before insert produced clean output. *Adds to the existing `saidkick paste` proposal:* the command should also normalize block-element whitespace before delegating to the editor, or document the gotcha. Same session also hit a first-call duplication (insertHTML produced two copies) — couldn't reproduce on the retry, possibly a stale focus/range from a prior `saidkick click`. Worth keeping an eye on.

- **2026-06-09 — `<label for="…">` not picked up by `--by-label` + cross-form id collisions.** Configuring PyPI's trusted-publisher form (`https://pypi.org/manage/account/publishing/`). `--by-label "PyPI Project Name"` returned `element not found` even though the page has standard `<label for="project_name">PyPI Project Name (required)</label><input id="project_name">` markup. Fell back to `--css "#project_name"` — but that selector matches 4 elements because PyPI ships four sibling forms (GitHub / GitLab / Google / ActiveState), each with the same set of input ids. Fixed by adding `--within-css "#pending-github-publisher-form"` to every type/click in the form. **Two proposals**, both small: (1) extend the `--by-label` resolver to walk `label[for]` → `#id` pairs (not just `aria-labelledby` / wrapped `<label>` siblings), since PyPI's pattern is the textbook HTML form layout; (2) once `--by-label` works there it's still ambiguous across the four forms — a `--first-visible` flag (or making visibility part of the default tie-break, since only one PyPI form is `display: block` at a time) would let scripts target "the visible form" without hand-rolling a `--within-css` scope per call.

- **2026-06-02 — viewport / device emulation gap.** Mobile responsive smoke test for marginalia. Needed a sub-720px viewport to verify `@media (max-width: 720px)` and `pointer: coarse` paths. `window.open(url, name, 'popup=yes,width=412,height=820')` was blocked by Chrome even when dispatched from a real user-gesture `.click()` on an injected button. Worked around by injecting a 412x820 `<iframe>` into an existing tab — fine for CSS layout verification, but the iframe inherits the host's pointer modality, so `pointer: coarse` stays false and touch-conditional JS paths (selection toolbar, audio recorder ergonomics) can't be exercised. **Proposed**: a `--width / --height` flag on `saidkick open` that uses CDP `Emulation.setDeviceMetricsOverride` (or `Emulation.setUserAgentOverride` + touch emulation), and/or a top-level `saidkick set-viewport --tab T --width W --height H [--touch]` command. Closest CDP primitive: `Emulation.setDeviceMetricsOverride` plus `Emulation.setTouchEmulationEnabled`. Would also let us smoke iOS/Android-shaped layouts without touching DevTools.

- **2026-06-24 — State-3 desync recovery is opaque; restarting the server makes it worse.** Smoke-testing a Peacock frontend at `localhost:8055`. `saidkick tabs` returned "No tabs. Is a browser connected?" while the extension UI showed connected; `curl /tabs` confirmed `[]` server-side (FastAPI up, zero registered browser sessions). Two "Reconnect" clicks didn't take. I then restarted the server (`kill` + `saidkick start`) — which *dropped* the extension's WS to the old process and left the new one with no browser, compounding the problem; it only recovered once a tab was actually opened/reconnected. **Proposals**, both small: (1) a `saidkick doctor` command that distinguishes the three states explicitly — server-down vs server-up-0-browsers vs connected — and prints the exact next action ("click the extension icon → Reconnect"; do NOT restart the server, which orphans the extension WS); (2) make `saidkick tabs` / errors disambiguate "server not running" from "server up, 0 browsers" instead of collapsing both into "No tabs. Is a browser connected?" — the former needs `start`, the latter needs a Reconnect click, and conflating them is what led to the counterproductive restart.
