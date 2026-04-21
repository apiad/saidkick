# Semantic find, keyboard press, screenshot, rich-type, exec hygiene — Design

**Status:** approved, ready for implementation plan
**Date:** 2026-04-21
**Version target:** 0.4.0 (additive except one `exec` breaking change)

## Problem

Driving a real app (WhatsApp Web) end-to-end exposed the gaps 0.3.0 doesn't cover:

- Finding a chat by name required `span[title*="Alice"]` + an ancestor walk. The agent's intent is "the row named Alice," not a CSS query.
- Sending the message required hand-built `KeyboardEvent` dispatch to press Enter.
- Typing into WhatsApp's Lexical-based contenteditable ignored naive `value = ...` / `innerText = ...`; had to fall back to `document.execCommand("insertText", ...)`.
- Verifying the send landed needed another `exec` DOM read. A PNG would have answered instantly and also fed a vision model.
- `exec` calls leaked `const`/`let` into each other because every invocation shares the page's global scope — silent footgun.

## Goals

Add three primitives (`find`, `press`, `screenshot`), fix two pre-existing weaknesses (rich-input `type`, `exec` scope isolation). Make the common driving patterns expressible without `exec` acrobatics.

## Non-goals

Deferred: `by_role` locator + `a11y` accessibility-tree dump (both need CDP `Accessibility.getFullAXTree` — one coherent chunk for 0.5.0), `wait-gone` inverse wait, `fill-form` composite, network-request inspection, file upload, record/replay macros.

## New / changed surface

### 1. `find` — semantic locator, inline on every selector-using command

Every existing selector-taking endpoint (`dom`, `text`, `click`, `type`, `select`) gains three new locator fields alongside the existing `css` / `xpath`:

- `by_text: Optional[str]` — match on `textContent` / `innerText` (substring, case-insensitive).
- `by_label: Optional[str]` — match on `aria-label`, `aria-labelledby` resolved text, `<label for>` target. Same substring-ci rules.
- `by_placeholder: Optional[str]` — match on `placeholder` attribute directly.

Plus:

- `nth: Optional[int]` — when multiple matches exist, pick the Nth (0-indexed). Omit to error on ambiguity.
- `within_css: Optional[str]` — scope the search to descendants of a CSS-matched root. Empty = document root.
- Text-match escape hatches: `exact: bool = False` makes `by_text` / `by_label` / `by_placeholder` require full-string equality; `regex: bool = False` treats them as regex patterns. `exact` and `regex` are mutually exclusive; setting both is 400.

**Exactly one** of `css` / `xpath` / `by_text` / `by_label` / `by_placeholder` must be set on any command that uses locators; setting zero or more than one is 400.

Ambiguity behavior: when the resolved locator matches ≥2 elements and `nth` is unset, return 400 `"Ambiguous locator: found N matches"`. Consistent with today's CSS-ambiguity rule in 0.3.0. `nth` is the disambiguation knob; there is no "first wins by default."

### 2. `find` as a standalone command — debugging/inspection

`GET /find?tab=br-XXXX:N&by_text=Alice&...` returns up to 50 matches as JSON:

```json
[
  {
    "selector": "#chat-list > div:nth-child(4) div[role=\"listitem\"]",
    "tag": "DIV",
    "role": "listitem",
    "name": "Alice Chen",
    "text": "Alice Chen Sounds good 10:21 am",
    "rect": {"x": 0, "y": 180, "w": 420, "h": 72},
    "visible": true
  }
]
```

CLI: `saidkick find --tab TAB --by-text "Alice" [--role listitem] [--within-css ...]`.

The `selector` field is a unique CSS path back to that element, useful as a stable handle for follow-up commands in scripts. `visible` is `el.offsetParent !== null && rect has nonzero area` — cheap filter to exclude hidden matches. `role` is read from the element's IDL `role` attribute (where supported) or inferred from tag; it's informational output only — `by_role` matching is deferred to the 0.5.0 accessibility chunk.

### 3. `press` — keyboard events

`POST /press` body:

```json
{
  "tab": "br-XXXX:N",
  "key": "Enter",
  "modifiers": ["ctrl", "shift"],
  "css": null,
  "by_text": null,
  "by_label": null,
  "by_placeholder": null,
  "within_css": null,
  "nth": null,
  "wait_ms": 0
}
```

`key` is a JS `KeyboardEvent.key` value (`Enter`, `Escape`, `Tab`, `ArrowDown`, `Backspace`, `a`, `A`, `F5`, …). Translated to CDP `Input.dispatchKeyEvent` parameters in the extension: synthesises proper `keyDown` + `keyUp` (plus `char` for printable keys), with `modifiers` bitmask.

`modifiers` is optional; values from `{"ctrl", "shift", "alt", "meta"}`.

If any locator is set, focus that element first (via a `chrome.debugger` `Runtime.callFunctionOn` `el.focus()`), then dispatch. If no locator, dispatch goes to whatever `document.activeElement` is — same as a real human pressing keys without clicking anything first.

CLI: `saidkick press KEY --tab TAB [--mod ctrl,shift] [--mod alt] [--css ... | --by-text ...] [--wait-ms N]`. Comma-separated `--mod` values and repeated `--mod` flags both work.

Return (200): `{"pressed": "Enter"}` on success.

### 4. `screenshot` — PNG capture

`GET /screenshot?tab=br-XXXX:N[&css=SCOPE|&by_text=...|...][&within_css=...][&full_page=false]`

- Returns JSON: `{"png_base64": "iVBORw0KGgo...", "width": 1920, "height": 1080}` over REST.
- Backed by `chrome.debugger` `Page.captureScreenshot` (format `png`).
- If a locator is set, the element's `getBoundingClientRect()` becomes the clip rectangle, rounded to integer pixels. No locator: capture the viewport.
- `full_page: true` captures the full scrollable document via CDP's `captureBeyondViewport`. Default false (viewport only) — keeps payloads small by default.

CLI: `saidkick screenshot --tab TAB [--css SCOPE | --by-text ...] [--full-page] [--output PATH]`.

- Default: decode base64 and write raw bytes to stdout. `saidkick screenshot --tab X > shot.png` works.
- `--output PATH`: write to the file instead (overwrites silently).

### 5. `type` on contenteditable — behavior fix

In `content.js` `TYPE` handler, after resolving the target element:

```javascript
if (element.isContentEditable) {
    element.focus();
    if (payload.clear) {
        const range = document.createRange();
        range.selectNodeContents(element);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        // execCommand("delete") clears the selection through the editor's input pipeline
        document.execCommand("delete");
    }
    document.execCommand("insertText", false, payload.text);
    // fire input/change for any external listeners
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return { success: true, payload: "Typed" };
}
// else: existing value/innerText path for plain <input>/<textarea>
```

`document.execCommand("insertText", ...)` routes through the editor framework's input pipeline (Lexical, ProseMirror, Slate, Quill, Draft all handle this) rather than overwriting private state. No new endpoint; silent, non-breaking behavior improvement for every real rich-text target.

### 6. `exec` isolation — breaking behavior change

In `background.js` `EXECUTE` handler, wrap the user's code before passing to `Runtime.evaluate`:

```javascript
const wrappedCode = `(async () => {\n${payload.code}\n})()`;
chrome.debugger.sendCommand(debugTarget, "Runtime.evaluate", {
    expression: wrappedCode,
    awaitPromise: true,
    returnByValue: true,
});
```

Consequences:

- `const` / `let` / `function` declarations in user code no longer leak into the page's global scope between calls. Each invocation gets a fresh async function scope.
- Top-level `await` works naturally.
- **Breaking:** callers must `return` their result. A bare expression `document.title` no longer becomes the response payload; write `return document.title` or use `document.title` as the last statement... no, specifically: the IIFE's return value is what propagates, so `return` is required. This is the only breaking change in 0.4.0 — called out in CHANGELOG.

## Error taxonomy additions

Existing 0.3.0 policy stands (400 malformed, 404 not-found, 422 validation, 502 upstream, 504 timeout, 500 reserved). Additions:

| Situation | HTTP | Message |
|---|---|---|
| Zero locator fields set on a command requiring one | 400 | `"No locator: specify one of css/xpath/by-text/by-label/by-placeholder"` |
| Two or more locator fields set | 400 | `"Ambiguous locator options: specify exactly one"` |
| Ambiguous resolution (N≥2 matches, `nth` unset) | 400 | `"Ambiguous locator: found N matches"` |
| `regex` and `exact` both true | 400 | `"exact and regex are mutually exclusive"` |
| Invalid `regex` pattern | 400 | `"invalid regex: {reason}"` |
| Invalid `key` for `press` | 400 | `"unknown key: {key}"` — whitelist-driven |
| Unknown modifier | 400 | `"unknown modifier: {mod}"` |
| Element found but not focusable (for `press` target) | 400 | `"element not focusable: {selector}"` |

The existing `_raise_for_extension_error` classifier picks up the new strings via its existing keyword patterns (`ambiguous`, `no locator` → 400; `element not found` → 404).

## Server changes — summary

- `src/saidkick/server.py`:
  - New Pydantic mixin `Locator` (fields: `css`, `xpath`, `by_text`, `by_label`, `by_placeholder`, `nth`, `within_css`, `exact`, `regex`). Existing `SelectorRequest` and friends extend it.
  - `GET /dom`, `GET /text`, `POST /click`, `POST /type`, `POST /select` take the new fields in their payloads and pass through to the extension unchanged.
  - New endpoints: `GET /find`, `POST /press`, `GET /screenshot`.
  - Classifier gains the new message patterns listed above (mostly absorbed by existing keywords).
- No changes to `send_command` signature; all new endpoints use it with appropriate `_command_timeout(wait_ms=...)`.

## Extension changes — summary

- `content.js`:
  - Replace `collectMatches(css, xpath)` with `collectMatches(locator)` — takes the full locator object and returns matching elements. Resolution:
    1. Determine the search root: `within_css ? document.querySelector(within_css) : document`. If `within_css` is set but matches nothing, throw `"within-css matched no element"`.
    2. If `css` set: `Array.from(root.querySelectorAll(css))`.
    3. If `xpath` set: existing `document.evaluate` logic, with `root` as the context node.
    4. If `by_text` / `by_label` / `by_placeholder` set: scan `root.querySelectorAll("*")` with the appropriate predicate (substring-ci by default, or exact-match or regex per the escape-hatch flags).
    5. If `nth` set, return `[matches[nth]]` (or `[]` if OOB); if `nth` unset and `matches.length > 1`, throw `"Ambiguous locator: found N matches"`.
  - `waitForSelector` and `waitForAnyMatches` become `waitForLocator` and `waitForAnyLocator`, using `collectMatches` under the hood. Ambiguity-during-wait still only throws at deadline expiry (DOM may be settling).
  - `TYPE` handler: contenteditable branch per Section 5 above.
  - `GET_DOM` / `GET_TEXT` / `CLICK` / `SELECT` continue to use the locator helper; no logic change besides the new input shape.
  - New handler `FIND` — returns the JSON array described in Section 2.
- `background.js`:
  - New `PRESS` handler. If a locator is set, forwards a `RESOLVE_AND_FOCUS` message to content.js, which returns the CSS path of the focused element; then uses the CSS path with `Runtime.callFunctionOn` for focus (redundant but robust), then `Input.dispatchKeyEvent` via `chrome.debugger`. If no locator: straight to `Input.dispatchKeyEvent`. Uses existing `ensureDebuggerAttached`.
  - New `SCREENSHOT` handler. Attaches debugger; if a locator is set, forwards `RESOLVE_RECT` to content.js to get the element's bounding rect; calls `Page.captureScreenshot` with an optional `clip` parameter + `captureBeyondViewport` when `full_page` is true; returns `{png_base64, width, height}`.
  - `EXECUTE` handler: wrap `payload.code` per Section 6.

## Python client changes — summary

```python
class SaidkickClient:
    # All selector-using methods gain locator kwargs:
    # by_text, by_label, by_placeholder, within_css, nth, exact, regex

    def find(self, tab: str, **locator) -> List[Dict[str, Any]]: ...
    def press(self, tab: str, key: str, modifiers: Optional[List[str]] = None,
              wait_ms: int = 0, **locator) -> Dict[str, Any]: ...
    def screenshot(self, tab: str, full_page: bool = False,
                   **locator) -> Dict[str, Any]: ...
    # get_dom, text, click, type, select gain **locator
```

## CLI changes — summary

- Every selector-using command gains `--by-text`, `--by-label`, `--by-placeholder`, `--within-css`, `--nth`, `--exact`, `--regex`.
- New commands: `find`, `press`, `screenshot`.
- `screenshot` defaults to stdout raw bytes; `--output PATH` writes to file.
- `press`'s `--mod` option is a `List[str]` that also accepts comma-separated values (split server-side).

## Testing

- **Unit:** locator validation (exactly-one rule, regex/exact mutex); classifier mappings for new messages; screenshot endpoint assembles CDP call correctly; press endpoint routes through send_command.
- **Integration (TestClient + AsyncMock):** each new endpoint routes the right payload; locator fields propagate end-to-end; error cases hit expected status codes.
- **Extension:** no automated tests; covered by a manual smoke checklist in the plan.

## Breaking changes

Only one: `exec` requires an explicit `return` for the value to propagate. CHANGELOG entry at top of 0.4.0.

## Success criteria

1. Full pytest suite green.
2. The WhatsApp-send flow reproducible without any `exec` calls:
   ```
   saidkick click  --tab $TAB --by-text "Alice Chen"
   saidkick type   "Hello Alice" --tab $TAB --by-label "Type a message"
   saidkick press  Enter --tab $TAB
   saidkick screenshot --tab $TAB --output /tmp/shot.png
   ```
3. `exec` with `const x = 1; return x` works; rerunning the same snippet twice doesn't error on redeclaration.
4. Typing "hello" into a Slack or WhatsApp compose box via `saidkick type` — with no `exec` fallback — lands in the message input.
