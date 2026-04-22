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
