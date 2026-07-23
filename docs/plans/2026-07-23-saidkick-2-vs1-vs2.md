# saidkick 2.0 — VS1 + VS2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace saidkick's FastAPI-hub + Chrome-MV3-extension architecture with a single Python
daemon that owns Chromium through Playwright, exposes an MCP agent surface, and lets a human
watch any context live and take the wheel when the agent asks for help.

**Architecture:** One process. `saidkick.engine` drives Playwright and knows nothing about
humans; `saidkick.control` owns the arbitration state machine and the human-request registry;
`saidkick.api` serves REST + WebSocket + a mounted MCP app; `saidkick.cockpit` is the human UI.
The engine↔control seam is what lets browser semantics be tested without simulating a human and
arbitration be tested without a browser.

**Tech Stack:** Python 3.12+, `uv`, Playwright 1.61, FastAPI, `mcp` 1.28 (FastMCP), Typer, Rich,
Pydantic v2, pytest + pytest-asyncio.

**Spec:** `docs/specs/2026-07-23-saidkick-2-agent-native-browser-design.md`. Section references
below (§N) point at it.

---

## Global Constraints

- **Python** `>= 3.12`. Package manager is `uv`. Run everything as `uv run …`.
- **Playwright** `>=1.61`. `page.accessibility` does not exist; all snapshots go through
  `locator.aria_snapshot()`. `page._snapshot_for_ai()` does not exist.
- **English only** in identifiers, comments, docstrings, error strings, log messages, tool
  descriptions and commit messages. This is an open-source repo.
- **No personal or vendor coupling.** No Telegram, no named chat/mail/push provider, no
  hardcoded personal paths. Notification is terminal + tool descriptions + in-page overlay, plus
  one generic `notify.webhook_url` (§5.1).
- **Errors come from the closed set in §9 only.** `ProfileLocked`, `NoSuchContext`, `NoSuchTab`,
  `StaleHandle`, `LocatorNotFound`, `LocatorAmbiguous`, `NavigationFailed`, `HumanHoldsControl`,
  `HumanTimeout`, `DialogBlocked`, `EngineCrashed`. Anything else is a daemon bug (500).
- **Tab addressing is `ctx_<hex>:<n>`.** The `br-XXXX` scheme and every trace of the extension
  are deleted, not deprecated.
- **All state is in memory for VS1+VS2.** No `beaver`, no on-disk profiles, no run log — those
  are VS3 and VS5. Contexts are ephemeral and unnamed; `open_context()` takes no profile.
- **Browser tests carry `@pytest.mark.browser`.** `uv run pytest -m "not browser"` must stay
  green and fast at every commit. The old `e2e` marker is removed.
- **Commit after every task**, conventional commits, scope `engine`/`control`/`api`/`mcp`/
  `cockpit`/`cli`.

---

## File Structure

Flat module layout, matching saidkick 1.x's existing style (`server.py`, `client.py`, `cli.py` at
package root) rather than imposing a package-per-layer tree on a small codebase.

**Created:**

| File | Responsibility |
|---|---|
| `src/saidkick/errors.py` | The closed error set + HTTP status mapping. No logic. |
| `src/saidkick/locators.py` | `Locator` model + `resolve(page, loc) -> playwright.Locator`. |
| `src/saidkick/engine.py` | `Engine`, `ManagedContext`, `ManagedTab`. Owns Playwright. |
| `src/saidkick/snapshot.py` | `snapshot(tab, mode, within_css)` for aria/text/html. |
| `src/saidkick/screencast.py` | `ScreencastPump` — CDP frames with mandatory ack. |
| `src/saidkick/overlay.py` | Attention overlay inject/remove; agent-invisible. |
| `src/saidkick/control.py` | Controller state machine + `HumanRequest` registry. |
| `src/saidkick/events.py` | In-memory event bus with monotonic `seq`. |
| `src/saidkick/api.py` | FastAPI app factory: REST + WS + mounted MCP. |
| `src/saidkick/mcp_server.py` | FastMCP tools and their descriptions. |
| `src/saidkick/dashboard.py` | Rich terminal dashboard for `saidkick serve`. |
| `src/saidkick/cockpit/` | Jinja templates + static JS/CSS for the human UI. |
| `tests/fixtures/site/` | Static fixture site served locally by `conftest.py`. |
| `tests/conftest.py` | Fixture server, engine fixture, marker registration. |

**Deleted:** `src/saidkick/extension/` (entire tree), `src/saidkick/server.py`,
`tests/test_error_taxonomy.py`, `tests/test_saidkick_e2e.py`, `tests/test_saidkick.py`,
`tests/test_saidkick_enhanced.py`, `tests/test_doctor.py`, `tests/test_tabs.py`,
`tests/test_execute_args.py`, `tests/assets/`. These test the extension protocol, which no longer
exists.

**Rewritten:** `src/saidkick/client.py`, `src/saidkick/cli.py`.

**Preserved by porting, not copying:** the locator *vocabulary* (`css`, `xpath`, `by_text`,
`by_label`, `by_placeholder`, `by_role`, `within_css`, `nth`, `exact`, `regex`, `pierce_shadow`)
and its validation rules from `server.py:70-112`. Note `pierce_shadow` exists in 1.x and is not
mentioned in the spec — it carries over because Playwright pierces shadow DOM by default, so the
field becomes a no-op that is accepted for compatibility and documented as such.

---

## A note on code in steps

This plan gives **complete test code** for every task — under TDD the test *is* the contract, so
it must be exact. For implementation steps it gives **exact signatures, types, and the algorithm
where it is non-obvious** (the screencast ack loop, the arbitration transitions, the overlay
invisibility technique) rather than transcribing every line, because the implementation is being
written in the same session that wrote this plan and duplicating it verbatim would be pure waste.
Any task whose implementation approach is *not* obvious from its tests carries the code.

---

# VS1 — agent drives, human watches

## Task 1: Test scaffolding and the fixture site

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/fixtures/site/{index,form,delayed,dialog,frame}.html`
- Create: `tests/conftest.py`
- Delete: `tests/assets/`, `tests/test_saidkick.py`, `tests/test_saidkick_enhanced.py`,
  `tests/test_saidkick_e2e.py`, `tests/test_doctor.py`, `tests/test_tabs.py`,
  `tests/test_execute_args.py`, `tests/test_error_taxonomy.py`

**Interfaces:**
- Produces: `fixture_url` (session-scoped `str`, e.g. `http://127.0.0.1:PORT`), `engine`
  (function-scoped `Engine`, started and stopped), `ctx` (function-scoped `ManagedContext`),
  `tab` (function-scoped `ManagedTab` on `index.html`).

- [ ] **Step 1: Update dependencies and markers**

In `pyproject.toml`, add to `dependencies`: `playwright>=1.61`, `mcp>=1.28`, `jinja2>=3.1`.
Replace the `e2e` marker with `browser`:

```toml
markers = [
    "browser: requires a real Chromium (run with -m browser)",
]
```

Run `uv sync --all-groups && uv run playwright install chromium`.

- [ ] **Step 2: Write the fixture site**

`tests/fixtures/site/index.html` — links to the other pages, an `<h1>Fixture Home</h1>`.

`tests/fixtures/site/form.html` — the workhorse:

```html
<!doctype html><html><head><title>Form</title></head><body>
<h1>Form</h1>
<form id="f" onsubmit="event.preventDefault();document.getElementById('out').textContent='submitted:'+document.getElementById('u').value;">
  <label for="u">Username</label>
  <input id="u" name="u" placeholder="your name">
  <label for="c">Country</label>
  <select id="c"><option value="cu">Cuba</option><option value="es">Spain</option></select>
  <button type="submit" id="go">Send</button>
</form>
<div contenteditable="true" aria-label="Message" id="editor">edit me</div>
<div id="out"></div>
<div id="nested"><span>Outer <b>Send</b></span></div>
</body></html>
```

`delayed.html` — injects `<div id="late">ready</div>` after 800ms via `setTimeout`.
`dialog.html` — a button that calls `confirm("proceed?")` and writes the result to `#result`.
`frame.html` — an `<iframe src="form.html">`.

- [ ] **Step 3: Write conftest.py**

```python
import asyncio, functools, http.server, socket, threading
from pathlib import Path
import pytest, pytest_asyncio
from saidkick.engine import Engine

SITE = Path(__file__).parent / "fixtures" / "site"


@pytest.fixture(scope="session")
def fixture_url():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest_asyncio.fixture
async def engine():
    eng = Engine()
    await eng.start()
    yield eng
    await eng.stop()


@pytest_asyncio.fixture
async def ctx(engine):
    return await engine.open_context()


@pytest_asyncio.fixture
async def tab(ctx, fixture_url):
    t = await ctx.open_tab(f"{fixture_url}/form.html")
    return t
```

- [ ] **Step 4: Delete the extension-era tests and assets**

```bash
git rm -r tests/assets src/saidkick/extension
git rm tests/test_saidkick.py tests/test_saidkick_enhanced.py tests/test_saidkick_e2e.py \
       tests/test_doctor.py tests/test_tabs.py tests/test_execute_args.py tests/test_error_taxonomy.py
```

- [ ] **Step 5: Verify collection**

Run: `uv run pytest -m "not browser" --collect-only -q`
Expected: collects the remaining locator tests without import errors. (`test_locators.py`,
`test_find.py` etc. still import `saidkick.server` and will be dealt with in Task 3 — if they
error here, that is expected and they are deleted or ported there.)

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "test: fixture site and engine fixtures; drop extension-era tests"
```

---

## Task 2: The error set

**Files:**
- Create: `src/saidkick/errors.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Produces: `SaidkickError(Exception)` with class attributes `status: int` and `code: str`, plus
  the eleven subclasses named in §9. `http_detail(exc) -> dict` returning
  `{"error": code, "detail": str(exc), **exc.extra}`.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from saidkick import errors as E


@pytest.mark.parametrize("cls,status,code", [
    (E.ProfileLocked, 409, "ProfileLocked"),
    (E.NoSuchContext, 404, "NoSuchContext"),
    (E.NoSuchTab, 404, "NoSuchTab"),
    (E.StaleHandle, 410, "StaleHandle"),
    (E.LocatorNotFound, 404, "LocatorNotFound"),
    (E.LocatorAmbiguous, 400, "LocatorAmbiguous"),
    (E.NavigationFailed, 502, "NavigationFailed"),
    (E.HumanHoldsControl, 409, "HumanHoldsControl"),
    (E.HumanTimeout, 504, "HumanTimeout"),
    (E.DialogBlocked, 409, "DialogBlocked"),
    (E.EngineCrashed, 502, "EngineCrashed"),
])
def test_error_has_status_and_code(cls, status, code):
    exc = cls("boom")
    assert exc.status == status and exc.code == code
    assert isinstance(exc, E.SaidkickError)


def test_ambiguous_carries_candidates():
    exc = E.LocatorAmbiguous("found 3", candidates=[{"tag": "b"}, {"tag": "span"}])
    d = E.http_detail(exc)
    assert d["error"] == "LocatorAmbiguous"
    assert d["detail"] == "found 3"
    assert len(d["candidates"]) == 2


def test_extra_is_never_shared_between_instances():
    a = E.LocatorAmbiguous("a", candidates=[1])
    b = E.LocatorAmbiguous("b")
    assert E.http_detail(b).get("candidates", []) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_errors.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'saidkick.errors'`

- [ ] **Step 3: Implement**

`SaidkickError(Exception)` with `status = 500`, `code = "SaidkickError"`, and
`__init__(self, detail: str = "", **extra)` storing `self.extra = dict(extra)` — a fresh dict per
instance, which is what the third test pins down. Each subclass sets `status` and `code`.
`code` is derived as `cls.__name__` via `__init_subclass__` so the two can never drift.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_errors.py -q` → 13 passed.

- [ ] **Step 5: Commit**

```bash
git add src/saidkick/errors.py tests/test_errors.py
git commit -m "feat(errors): closed error set with HTTP mapping"
```

---

## Task 3: Locator model and resolution

**Files:**
- Create: `src/saidkick/locators.py`
- Test: `tests/test_locators.py` (replace the 1.x file wholesale)
- Delete: `tests/test_find.py`, `tests/test_wait_ms.py`, `tests/test_text.py` (extension-protocol
  tests superseded by `tests/engine/` coverage in Tasks 4–6)

**Interfaces:**
- Consumes: `saidkick.errors`.
- Produces:
  - `class Locator(BaseModel)` with fields `css, xpath, by_text, by_label, by_placeholder,
    by_role, within_css, nth, exact, regex, pierce_shadow, wait_ms` (`wait_ms: int = 0`).
  - `validate_locator(loc: Locator, required: bool) -> None` raising `LocatorAmbiguous` for
    >1 primary or `exact`+`regex`, and `LocatorNotFound` when `required` and none given.
  - `resolve(page: playwright.async_api.Page, loc: Locator) -> playwright.async_api.Locator`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from saidkick.locators import Locator, validate_locator, resolve
from saidkick import errors as E

PRIMARIES = ["css", "xpath", "by_text", "by_label", "by_placeholder", "by_role"]


@pytest.mark.parametrize("field", PRIMARIES)
def test_single_primary_is_valid(field):
    validate_locator(Locator(**{field: "x"}), required=True)


def test_two_primaries_rejected():
    with pytest.raises(E.LocatorAmbiguous):
        validate_locator(Locator(css="a", by_text="b"), required=True)


def test_exact_and_regex_mutually_exclusive():
    with pytest.raises(E.LocatorAmbiguous):
        validate_locator(Locator(by_text="a", exact=True, regex=True), required=True)


def test_no_locator_when_required():
    with pytest.raises(E.LocatorNotFound):
        validate_locator(Locator(), required=True)


def test_no_locator_allowed_when_optional():
    validate_locator(Locator(), required=False)


@pytest.mark.browser
@pytest.mark.parametrize("loc,expected_id", [
    (Locator(css="#go"), "go"),
    (Locator(by_label="Username"), "u"),
    (Locator(by_placeholder="your name"), "u"),
    (Locator(by_role="button", by_text="Send"), "go"),
    (Locator(xpath="//button[@id='go']"), "go"),
])
async def test_resolve_finds_element(tab, loc, expected_id):
    assert await resolve(tab.page, loc).get_attribute("id") == expected_id


@pytest.mark.browser
async def test_by_text_returns_leafmost(tab):
    # #nested contains <span>Outer <b>Send</b></span>; the <b> is leaf-most.
    el = resolve(tab.page, Locator(by_text="Send", within_css="#nested"))
    assert (await el.evaluate("e => e.tagName")) == "B"


@pytest.mark.browser
async def test_within_css_scopes(tab):
    el = resolve(tab.page, Locator(by_text="Send", within_css="#f"))
    assert await el.get_attribute("id") == "go"


@pytest.mark.browser
async def test_nth_disambiguates(tab):
    el = resolve(tab.page, Locator(css="label", nth=1))
    assert (await el.text_content()) == "Country"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_locators.py -q -m "not browser"` → FAIL, module not found.

- [ ] **Step 3: Implement**

`resolve` maps each primary onto Playwright:

| Field | Playwright call |
|---|---|
| `css` | `root.locator(css)` |
| `xpath` | `root.locator(f"xpath={xpath}")` |
| `by_text` | `root.get_by_text(value, exact=exact)` |
| `by_label` | `root.get_by_label(value, exact=exact)` |
| `by_placeholder` | `root.get_by_placeholder(value, exact=exact)` |
| `by_role` | `root.get_by_role(value, name=by_text)` when `by_text` is also set, else `get_by_role(value)` |

where `root = page.locator(within_css)` if `within_css` else `page`. `regex=True` wraps the value
in `re.compile`. `nth` applies `.nth(n)`. `by_role` combined with `by_text` is the one case where
two primaries are legal — `validate_locator` must special-case it, and the test above pins that.

Leaf-most: Playwright's `get_by_text` already returns the innermost matching element, which the
`test_by_text_returns_leafmost` test verifies rather than assumes. `pierce_shadow` is accepted and
ignored (Playwright pierces open shadow roots by default); document it in the field description.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_locators.py -q` (with `-m browser` too) → all pass.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(locators): port 1.x locator vocabulary onto Playwright"
```

---

## Task 4: Engine — lifecycle, contexts, tabs, navigation

**Files:**
- Create: `src/saidkick/engine.py`
- Test: `tests/engine/test_lifecycle.py`

**Interfaces:**
- Produces:
  - `class Engine`: `async start()`, `async stop()`, `async open_context(viewport=None) ->
    ManagedContext`, `async close_context(ctx_id)`, `get_context(ctx_id) -> ManagedContext`
    (raises `NoSuchContext`), `list_contexts() -> list[dict]`.
  - `class ManagedContext`: `.id` (`"ctx_" + 4 hex`), `.pw_context`, `async open_tab(url=None) ->
    ManagedTab`, `async close_tab(tab_id)`, `get_tab(tab_id) -> ManagedTab` (raises `NoSuchTab`),
    `list_tabs() -> list[dict]`, `async cdp(tab) -> CDPSession` (cached per tab).
  - `class ManagedTab`: `.id` (`"ctx_a1b2:3"`), `.page`, `.context`, `async navigate(url, wait)`.
- Consumes: `saidkick.errors`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from saidkick import errors as E

pytestmark = pytest.mark.browser


async def test_open_and_list_context(engine):
    ctx = await engine.open_context()
    assert ctx.id.startswith("ctx_")
    assert [c["id"] for c in engine.list_contexts()] == [ctx.id]


async def test_contexts_are_isolated(engine, fixture_url):
    a, b = await engine.open_context(), await engine.open_context()
    ta = await a.open_tab(f"{fixture_url}/form.html")
    await ta.page.evaluate("localStorage.setItem('k','from-a')")
    tb = await b.open_tab(f"{fixture_url}/form.html")
    assert await tb.page.evaluate("localStorage.getItem('k')") is None


async def test_tab_id_is_context_scoped(engine, fixture_url):
    ctx = await engine.open_context()
    t = await ctx.open_tab(f"{fixture_url}/index.html")
    assert t.id.startswith(ctx.id + ":")


async def test_unknown_context_raises(engine):
    with pytest.raises(E.NoSuchContext):
        engine.get_context("ctx_zzzz")


async def test_unknown_tab_raises(ctx):
    with pytest.raises(E.NoSuchTab):
        ctx.get_tab("ctx_zzzz:99")


async def test_closed_tab_is_gone(ctx, fixture_url):
    t = await ctx.open_tab(f"{fixture_url}/index.html")
    await ctx.close_tab(t.id)
    with pytest.raises(E.NoSuchTab):
        ctx.get_tab(t.id)


async def test_navigate_bad_url_raises_navigation_failed(tab):
    with pytest.raises(E.NavigationFailed):
        await tab.navigate("http://127.0.0.1:1/nope", wait="load")


async def test_close_context_closes_its_tabs(engine, fixture_url):
    ctx = await engine.open_context()
    await ctx.open_tab(f"{fixture_url}/index.html")
    await engine.close_context(ctx.id)
    assert engine.list_contexts() == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/engine/test_lifecycle.py -q -m browser` → FAIL, no module.

- [ ] **Step 3: Implement**

`Engine.start()` does `self._pw = await async_playwright().start()` then
`self._browser = await self._pw.chromium.launch(headless=self.headless)`. `open_context()` calls
`self._browser.new_context(viewport=viewport or {"width":1280,"height":800})` and wraps it. IDs
are `"ctx_" + secrets.token_hex(2)`; tab ids are `f"{ctx.id}:{n}"` with `n` a per-context counter
(never reused, so a closed tab's id stays permanently invalid rather than silently rebinding).

`navigate` wraps `page.goto(url, wait_until=wait)` and re-raises any Playwright error as
`NavigationFailed`. `wait` accepts `load | domcontentloaded | networkidle | commit`.

`stop()` closes browser then playwright, and is idempotent.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/engine/test_lifecycle.py -q -m browser` → 8 passed.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(engine): Playwright-backed contexts, tabs and navigation"
```

---

## Task 5: Snapshots

**Files:**
- Create: `src/saidkick/snapshot.py`
- Test: `tests/engine/test_snapshot.py`

**Interfaces:**
- Produces: `async snapshot(tab: ManagedTab, mode: str = "aria", within_css: str | None = None)
  -> str`. Modes `aria | text | html`. Unknown mode raises `ValueError`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from saidkick.snapshot import snapshot

pytestmark = pytest.mark.browser


async def test_aria_snapshot_names_controls(tab):
    s = await snapshot(tab, mode="aria")
    assert 'button "Send"' in s
    assert 'textbox "Username"' in s


async def test_aria_snapshot_is_much_smaller_than_html(tab):
    assert len(await snapshot(tab, mode="aria")) < len(await snapshot(tab, mode="html"))


async def test_within_css_scopes_snapshot(tab):
    s = await snapshot(tab, mode="aria", within_css="#f")
    assert 'button "Send"' in s
    assert "edit me" not in s


async def test_text_mode(tab):
    assert "edit me" in await snapshot(tab, mode="text")


async def test_bad_mode_raises(tab):
    with pytest.raises(ValueError):
        await snapshot(tab, mode="dom")
```

- [ ] **Step 2: Run to verify failure.** Run: `uv run pytest tests/engine/test_snapshot.py -q -m browser`

- [ ] **Step 3: Implement**

```python
async def snapshot(tab, mode="aria", within_css=None):
    root = tab.page.locator(within_css) if within_css else tab.page.locator("body")
    if mode == "aria":
        return await root.aria_snapshot()
    if mode == "text":
        return await root.inner_text()
    if mode == "html":
        return await root.inner_html()
    raise ValueError(f"unknown snapshot mode: {mode!r}")
```

`aria_snapshot()` returns a YAML-shaped string (verified §7). There is no `page.accessibility`.

- [ ] **Step 4: Verify.** → 5 passed.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(engine): aria/text/html snapshots"
```

---

## Task 6: Actions

**Files:**
- Create: `src/saidkick/actions.py`
- Test: `tests/engine/test_actions.py`

**Interfaces:**
- Produces, all `async (tab: ManagedTab, loc: Locator, ...) -> dict`:
  `click`, `type_text(tab, loc, text, submit=False)`, `press(tab, key, loc=None, modifiers=None)`,
  `select(tab, loc, values)`, `hover`, `scroll(tab, loc, block="center")`,
  `highlight(tab, loc, color="#ef4444", duration_ms=2000)`, `upload(tab, loc, paths)`,
  `find(tab, loc) -> list[dict]`, `screenshot(tab, loc=None, full_page=False) -> bytes`.
- Consumes: `saidkick.locators.resolve`, `saidkick.errors`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from saidkick.locators import Locator
from saidkick import actions as A
from saidkick import errors as E

pytestmark = pytest.mark.browser


async def test_click_submits_form(tab):
    await A.type_text(tab, Locator(by_label="Username"), "alice")
    await A.click(tab, Locator(by_text="Send"))
    assert "submitted:alice" == await tab.page.locator("#out").text_content()


async def test_type_into_contenteditable(tab):
    await A.type_text(tab, Locator(by_label="Message"), "hello")
    assert "hello" in await tab.page.locator("#editor").inner_text()


async def test_type_with_submit(tab):
    await A.type_text(tab, Locator(by_label="Username"), "bob", submit=True)
    assert "submitted:bob" == await tab.page.locator("#out").text_content()


async def test_select_option(tab):
    await A.select(tab, Locator(css="#c"), ["es"])
    assert await tab.page.locator("#c").input_value() == "es"


async def test_not_found_raises(tab):
    with pytest.raises(E.LocatorNotFound):
        await A.click(tab, Locator(by_text="NoSuchButton"), timeout_ms=500)


async def test_ambiguous_raises_with_candidates(tab):
    with pytest.raises(E.LocatorAmbiguous) as exc:
        await A.click(tab, Locator(css="label"), timeout_ms=500)
    assert len(exc.value.extra["candidates"]) == 2


async def test_find_returns_descriptors(tab):
    out = await A.find(tab, Locator(css="#go"))
    assert out[0]["tag"] == "BUTTON" and out[0]["text"] == "Send"


async def test_wait_ms_waits_for_late_element(engine, fixture_url):
    ctx = await engine.open_context()
    t = await ctx.open_tab(f"{fixture_url}/delayed.html")
    out = await A.find(t, Locator(css="#late", wait_ms=3000))
    assert out[0]["text"] == "ready"


async def test_screenshot_returns_png_bytes(tab):
    png = await A.screenshot(tab)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

Every action funnels through one helper so ambiguity and not-found are handled identically:

```python
async def _one(tab, loc, timeout_ms=5000):
    """Resolve to exactly one element or raise from the closed error set."""
    l = resolve(tab.page, loc)
    timeout = loc.wait_ms or timeout_ms
    if loc.nth is None:
        try:
            await l.first.wait_for(state="attached", timeout=timeout)
        except PWTimeout as e:
            raise E.LocatorNotFound(f"no element matched {loc.describe()}") from e
        n = await l.count()
        if n > 1:
            cands = await _describe_all(l, limit=10)
            raise E.LocatorAmbiguous(f"found {n} matches", candidates=cands)
    return l.first if loc.nth is None else l
```

`type_text` uses `fill()` for `<input>`/`<textarea>` and falls back to `click()` + `type()` for
`contenteditable` (detected via `evaluate("e => e.isContentEditable")`), because `fill()` raises
on non-form elements. `submit=True` appends `press("Enter")`. `highlight` injects a temporary
outline via `evaluate` and removes it after `duration_ms` (`0` = persist).

- [ ] **Step 4: Verify.** → 9 passed.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(engine): click/type/select/find/screenshot actions"
```

---

## Task 7: Event bus

**Files:**
- Create: `src/saidkick/events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Produces: `class EventBus`: `emit(ctx_id, kind, **data) -> int` (returns the assigned `seq`),
  `since(ctx_id, seq) -> list[dict]`, `async wait(ctx_id, seq, timeout_s) -> list[dict]`,
  `subscribe(ctx_id) -> AsyncIterator[dict]`. Events are `{seq, ts, ctx, kind, **data}`.
  Per-context ring buffer capped at 500.

- [ ] **Step 1: Write the failing tests**

```python
import asyncio, pytest
from saidkick.events import EventBus


def test_seq_is_monotonic_across_contexts():
    b = EventBus()
    assert b.emit("ctx_a", "nav", url="x") == 1
    assert b.emit("ctx_b", "nav", url="y") == 2


def test_since_filters_by_context_and_seq():
    b = EventBus()
    b.emit("ctx_a", "one"); s = b.emit("ctx_a", "two"); b.emit("ctx_b", "other")
    assert [e["kind"] for e in b.since("ctx_a", 0)] == ["one", "two"]
    assert [e["kind"] for e in b.since("ctx_a", s - 1)] == ["two"]


def test_ring_buffer_caps_at_500():
    b = EventBus()
    for i in range(600):
        b.emit("ctx_a", "e", i=i)
    kept = b.since("ctx_a", 0)
    assert len(kept) == 500 and kept[0]["i"] == 100


async def test_wait_returns_immediately_when_events_pending():
    b = EventBus(); b.emit("ctx_a", "one")
    assert len(await b.wait("ctx_a", 0, timeout_s=5)) == 1


async def test_wait_blocks_then_wakes_on_emit():
    b = EventBus()
    task = asyncio.create_task(b.wait("ctx_a", 0, timeout_s=5))
    await asyncio.sleep(0.05)
    assert not task.done()
    b.emit("ctx_a", "late")
    assert (await task)[0]["kind"] == "late"


async def test_wait_returns_empty_on_timeout():
    assert await EventBus().wait("ctx_a", 0, timeout_s=0.1) == []
```

- [ ] **Step 2–4:** run (fail), implement with a global counter, `collections.deque(maxlen=500)`
  per context, and an `asyncio.Event` per context woken on `emit`; run (pass).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(events): in-memory event bus with monotonic seq"
```

---

## Task 8: REST API

**Files:**
- Create: `src/saidkick/api.py`
- Test: `tests/api/test_rest.py`

**Interfaces:**
- Produces: `create_app(engine: Engine, control: Controller | None = None) -> FastAPI`.
  Routes: `GET /health`, `GET /contexts`, `POST /contexts`, `DELETE /contexts/{cid}`,
  `GET /contexts/{cid}/tabs`, `POST /contexts/{cid}/tabs`, `DELETE /tabs/{tid}`,
  `POST /tabs/{tid}/navigate`, `GET /tabs/{tid}/snapshot`, `POST /tabs/{tid}/{action}`,
  `GET /tabs/{tid}/screenshot`, `GET /contexts/{cid}/events`.
  A single exception handler maps `SaidkickError -> JSONResponse(status=exc.status,
  content=http_detail(exc))`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest, pytest_asyncio
from httpx import AsyncClient, ASGITransport
from saidkick.api import create_app


@pytest_asyncio.fixture
async def client(engine):
    app = create_app(engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_health(client):
    assert (await client.get("/health")).json()["ok"] is True


@pytest.mark.browser
async def test_context_lifecycle(client):
    cid = (await client.post("/contexts")).json()["id"]
    assert cid in [c["id"] for c in (await client.get("/contexts")).json()]
    assert (await client.delete(f"/contexts/{cid}")).status_code == 200
    assert (await client.get("/contexts")).json() == []


async def test_unknown_context_is_404_with_code(client):
    r = await client.get("/contexts/ctx_zzzz/tabs")
    assert r.status_code == 404 and r.json()["error"] == "NoSuchContext"


@pytest.mark.browser
async def test_ambiguous_locator_is_400_with_candidates(client, fixture_url):
    cid = (await client.post("/contexts")).json()["id"]
    tid = (await client.post(f"/contexts/{cid}/tabs",
           json={"url": f"{fixture_url}/form.html"})).json()["id"]
    r = await client.post(f"/tabs/{tid}/click", json={"css": "label", "wait_ms": 300})
    assert r.status_code == 400
    assert r.json()["error"] == "LocatorAmbiguous" and len(r.json()["candidates"]) == 2
```

- [ ] **Step 2–4:** run (fail), implement, run (pass). The exception handler is the only place
  status codes appear — routes raise domain errors and never build HTTP responses themselves.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(api): REST surface over the engine"
```

---

## Task 9: MCP server

**Files:**
- Create: `src/saidkick/mcp_server.py`
- Modify: `src/saidkick/api.py` (mount + lifespan)
- Test: `tests/api/test_mcp.py`

**Interfaces:**
- Produces: `build_mcp(engine, control) -> FastMCP` exposing the VS1 tools: `list_contexts`,
  `open_context`, `close_context`, `list_tabs`, `open_tab`, `close_tab`, `navigate`, `snapshot`,
  `find`, `click`, `type`, `press`, `select`, `screenshot`, `events`.

**Known gotcha:** `mcp.streamable_http_app()` returns a Starlette app **with its own lifespan**
(the session manager). Mounting it on FastAPI without running that lifespan yields requests that
hang. The FastAPI lifespan must wrap `mcp.session_manager.run()`:

```python
@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield

app = FastAPI(lifespan=lifespan)
app.mount("/mcp", mcp.streamable_http_app())
```

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from saidkick.mcp_server import build_mcp

VS1_TOOLS = {"list_contexts","open_context","close_context","list_tabs","open_tab","close_tab",
             "navigate","snapshot","find","click","type","press","select","screenshot","events"}


async def test_all_vs1_tools_registered(engine):
    names = {t.name for t in await build_mcp(engine, None).list_tools()}
    assert VS1_TOOLS <= names


async def test_every_tool_has_a_real_description(engine):
    """Descriptions are a deliverable (spec §7), not autogenerated stubs."""
    for t in await build_mcp(engine, None).list_tools():
        assert t.description and len(t.description) > 60, t.name


async def test_snapshot_description_states_aria_preference(engine):
    d = {t.name: t.description for t in await build_mcp(engine, None).list_tools()}
    assert "aria" in d["snapshot"].lower() and "last resort" in d["snapshot"].lower()


async def test_open_context_description_warns_about_attached(engine):
    d = {t.name: t.description for t in await build_mcp(engine, None).list_tools()}
    assert "ephemeral" in d["open_context"].lower()
```

The description tests are deliberate: §7 makes tool prose a reviewed artifact, and a test is the
only thing that stops it from decaying into `"""Click an element."""`.

- [ ] **Step 2–4:** run (fail), implement each tool as a thin wrapper over `actions`/`snapshot`
  with an explicit `description=`, run (pass).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(mcp): VS1 tool surface with reviewed descriptions"
```

---

## Task 10: Screencast pump

**Files:**
- Create: `src/saidkick/screencast.py`
- Test: `tests/engine/test_screencast.py`

**Interfaces:**
- Produces: `class ScreencastPump`: `async start(quality, max_width)`, `async stop()`,
  `add_viewer(q: asyncio.Queue) -> None`, `remove_viewer(q)`, `viewer_count -> int`,
  `async set_quality(quality, max_width)`. Frames pushed to viewers as
  `{"data": b64, "metadata": {...}}`.

- [ ] **Step 1: Write the failing tests**

```python
import asyncio, pytest
from saidkick.screencast import ScreencastPump

pytestmark = pytest.mark.browser


async def test_pump_delivers_more_than_one_frame(tab):
    """Without Page.screencastFrameAck Chromium sends exactly ONE frame, forever.
    This test is the regression guard for the ack loop (spec §6)."""
    pump = ScreencastPump(tab)
    q = asyncio.Queue(); pump.add_viewer(q)
    await pump.start(quality=60, max_width=800)
    for i in range(4):
        await tab.page.evaluate(f"document.body.style.background='#{i}{i}{i}'")
        await asyncio.sleep(0.15)
    await pump.stop()
    assert q.qsize() > 1, "only one frame: the ack loop is broken"


async def test_frame_carries_metadata_for_coordinate_mapping(tab):
    pump = ScreencastPump(tab)
    q = asyncio.Queue(); pump.add_viewer(q)
    await pump.start(quality=60, max_width=800)
    frame = await asyncio.wait_for(q.get(), timeout=5)
    await pump.stop()
    for key in ("deviceWidth", "deviceHeight", "pageScaleFactor", "scrollOffsetX", "scrollOffsetY"):
        assert key in frame["metadata"]


async def test_stops_when_last_viewer_leaves(tab):
    pump = ScreencastPump(tab)
    q = asyncio.Queue(); pump.add_viewer(q)
    await pump.start(quality=60, max_width=800)
    assert pump.running is True
    pump.remove_viewer(q)
    await asyncio.sleep(0.1)
    assert pump.running is False
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement — the ack is the transport**

```python
async def start(self, quality=60, max_width=1280):
    self._cdp = await self.tab.context.cdp(self.tab)
    self._cdp.on("Page.screencastFrame", self._on_frame)
    await self._cdp.send("Page.startScreencast", {
        "format": "jpeg", "quality": quality,
        "maxWidth": max_width, "maxHeight": max_width,
    })
    self.running = True

def _on_frame(self, ev):
    # Ack FIRST and unconditionally: Chromium sends no further frames until
    # this lands, so any early return above it flatlines the stream.
    asyncio.create_task(self._ack(ev["sessionId"]))
    for q in list(self._viewers):
        if q.full():
            q.get_nowait()          # drop oldest; a slow viewer must not stall the pump
        q.put_nowait({"data": ev["data"], "metadata": ev["metadata"]})

async def _ack(self, session_id):
    try:
        await self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
    except Exception:
        pass    # tab closed mid-stream
```

- [ ] **Step 4: Verify.** → 3 passed.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(screencast): CDP frame pump with mandatory ack"
```

---

## Task 11: Cockpit — session list and live view

**Files:**
- Create: `src/saidkick/cockpit/templates/{base,index,session}.html`,
  `src/saidkick/cockpit/static/{cockpit.css,cockpit.js}`
- Modify: `src/saidkick/api.py` (routes `GET /`, `GET /session/{cid}`, `WS /ws/view/{tid}`)
- Test: `tests/api/test_cockpit.py`

**Interfaces:**
- Produces: `WS /ws/view/{tid}` — server pushes `{type:"frame", data, metadata}`; client sends
  `{type:"quality", quality, max_width}`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from fastapi.testclient import TestClient
from saidkick.api import create_app


@pytest.mark.browser
def test_index_lists_contexts(engine_sync, fixture_url):
    with TestClient(create_app(engine_sync)) as c:
        cid = c.post("/contexts").json()["id"]
        assert cid in c.get("/").text


@pytest.mark.browser
def test_view_socket_streams_frames(engine_sync, fixture_url):
    with TestClient(create_app(engine_sync)) as c:
        cid = c.post("/contexts").json()["id"]
        tid = c.post(f"/contexts/{cid}/tabs", json={"url": f"{fixture_url}/form.html"}).json()["id"]
        with c.websocket_connect(f"/ws/view/{tid}") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "frame" and msg["data"]
```

(`engine_sync` is a session-scoped sync-wrapper fixture added to `conftest.py` for `TestClient`,
which drives its own event loop.)

- [ ] **Step 2–4:** run (fail), implement, run (pass).

The client renders frames with `createImageBitmap` + `drawImage` onto a `<canvas>` (spec §6: not
WebGL). The canvas is sized from `metadata.deviceWidth/deviceHeight` so the coordinate transform
needed by Task 16 is established here rather than retrofitted.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(cockpit): session list and live screencast view"
```

---

## Task 12: CLI and client rewrite; delete the old server

**Files:**
- Rewrite: `src/saidkick/cli.py`, `src/saidkick/client.py`
- Delete: `src/saidkick/server.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `saidkick serve [--host --port --headless/--headful --quiet]`,
  `saidkick contexts`, `saidkick tabs`, `saidkick quick <url>`, plus the ported verbs
  `click type press select find snapshot screenshot navigate open close scroll highlight`.
  `SaidkickClient` gains `open_context/close_context/list_contexts/quick/snapshot`; `doctor`,
  `get_logs`, `set_mirror`, `get_mirror`, `execute` are removed.

- [ ] **Step 1: Write the failing tests**

```python
from typer.testing import CliRunner
from saidkick.cli import app

runner = CliRunner()


def test_start_command_is_gone():
    assert runner.invoke(app, ["start", "--help"]).exit_code != 0


def test_serve_command_exists():
    assert runner.invoke(app, ["serve", "--help"]).exit_code == 0


def test_quick_command_exists():
    assert "quick" in runner.invoke(app, ["--help"]).output


def test_mirror_command_is_gone():
    assert runner.invoke(app, ["mirror", "--help"]).exit_code != 0
```

- [ ] **Step 2–4:** run (fail), implement, run (pass). `quick` posts a context, then a tab, and
  prints the tab id and nothing else so `TAB=$(saidkick quick URL)` works.

- [ ] **Step 5: Commit**

```bash
git rm src/saidkick/server.py
git add -A && git commit -m "feat(cli)!: serve/quick/contexts; remove extension-era commands"
```

- [ ] **Step 6: VS1 gate — full suite, then tag**

```bash
uv run ruff check src tests
uv run pytest -m "not browser" -q
uv run pytest -m browser -q
```

Each as its own step; check the exit code of each before continuing. Do not chain them with `&&`
into one command — a non-zero code in the middle of a chain is easy to miss.

---

# VS2 — human takes the wheel

## Task 13: Controller state machine

**Files:**
- Create: `src/saidkick/control.py`
- Test: `tests/test_control.py`

**Interfaces:**
- Produces:
  - `class Controller`: `state(ctx_id) -> str` (`"agent" | "human" | "none"`),
    `take(ctx_id, who="human")`, `release(ctx_id, note: str | None = None)`,
    `assert_agent_may_act(ctx_id)` raising `HumanHoldsControl`.
  - `async request_human(ctx_id, reason, deadline_s=600, poll_s=120) -> dict` returning
    `{"status": "resolved"|"still_waiting"|"timeout", "note": str|None, "request_id": str,
      "human_message": str, "cockpit_url": str}`.
  - `pending(ctx_id) -> HumanRequest | None`, `list_pending() -> list[HumanRequest]`.

**No browser.** This whole task is a pure state machine and every test runs in `-m "not browser"`.

- [ ] **Step 1: Write the failing tests**

```python
import asyncio, pytest
from saidkick.control import Controller
from saidkick import errors as E


def test_default_state_is_agent():
    assert Controller().state("ctx_a") == "agent"


def test_agent_blocked_while_human_holds():
    c = Controller(); c.take("ctx_a")
    with pytest.raises(E.HumanHoldsControl):
        c.assert_agent_may_act("ctx_a")


def test_other_contexts_unaffected():
    c = Controller(); c.take("ctx_a")
    c.assert_agent_may_act("ctx_b")


def test_release_restores_agent():
    c = Controller(); c.take("ctx_a"); c.release("ctx_a")
    c.assert_agent_may_act("ctx_a")


async def test_request_resolved_by_release_carries_note():
    c = Controller()
    task = asyncio.create_task(c.request_human("ctx_a", "2FA", deadline_s=5, poll_s=5))
    await asyncio.sleep(0.05)
    c.take("ctx_a"); c.release("ctx_a", note="logged in")
    out = await task
    assert out["status"] == "resolved" and out["note"] == "logged in"


async def test_poll_returns_still_waiting_without_closing_request():
    c = Controller()
    out = await c.request_human("ctx_a", "2FA", deadline_s=5, poll_s=0.1)
    assert out["status"] == "still_waiting"
    assert c.pending("ctx_a") is not None


async def test_deadline_closes_request_as_timeout():
    c = Controller()
    out = await c.request_human("ctx_a", "2FA", deadline_s=0.1, poll_s=5)
    assert out["status"] == "timeout"
    assert c.pending("ctx_a") is None


async def test_second_request_rejoins_the_first():
    """Spec §5: re-calling must not open a duplicate card in the cockpit."""
    c = Controller()
    t1 = asyncio.create_task(c.request_human("ctx_a", "2FA", deadline_s=5, poll_s=5))
    await asyncio.sleep(0.05)
    t2 = asyncio.create_task(c.request_human("ctx_a", "2FA again", deadline_s=5, poll_s=5))
    await asyncio.sleep(0.05)
    assert len(c.list_pending()) == 1
    c.take("ctx_a"); c.release("ctx_a", note="done")
    assert (await t1)["status"] == (await t2)["status"] == "resolved"
    assert (await t1)["request_id"] == (await t2)["request_id"]


async def test_timeout_does_not_change_control_state():
    """Spec §5: a timed-out request must not touch the browser or the controller."""
    c = Controller()
    await c.request_human("ctx_a", "2FA", deadline_s=0.1, poll_s=5)
    assert c.state("ctx_a") == "agent"


async def test_request_carries_a_relayable_human_message():
    c = Controller(cockpit_base="http://localhost:6992")
    out = await c.request_human("ctx_a", "enter the 2FA code", deadline_s=0.1, poll_s=0.05)
    assert "enter the 2FA code" in out["human_message"]
    assert "http://localhost:6992" in out["cockpit_url"]
```

- [ ] **Step 2: Run to verify they fail.** Run: `uv run pytest tests/test_control.py -q`

- [ ] **Step 3: Implement**

`HumanRequest` is a dataclass holding `id`, `ctx`, `reason`, `opened_at`, `deadline_at`, and an
`asyncio.Event` plus a `note` slot. `request_human` creates-or-rejoins, then does:

```python
remaining = req.deadline_at - loop.time()
try:
    await asyncio.wait_for(req.done.wait(), timeout=min(poll_s, remaining))
    return {"status": "resolved", "note": req.note, ...}
except asyncio.TimeoutError:
    if loop.time() >= req.deadline_at:
        self._close(req)               # drop from pending; control state untouched
        return {"status": "timeout", ...}
    return {"status": "still_waiting", ...}
```

`release(ctx, note)` sets `req.note` and fires `req.done`. Two waiters on one request both wake
and both see the same `request_id`, which is what the rejoin test pins down.

- [ ] **Step 4: Verify.** → 11 passed, no browser.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(control): arbitration state machine and human-request registry"
```

---

## Task 14: Arbitration wired into the action path

**Files:**
- Modify: `src/saidkick/actions.py`, `src/saidkick/api.py`, `src/saidkick/mcp_server.py`
- Test: `tests/engine/test_arbitration.py`

**Interfaces:**
- Consumes: `Controller.assert_agent_may_act`.
- Produces: every mutating action (`click`, `type_text`, `press`, `select`, `hover`, `scroll`,
  `upload`, `navigate`) calls `assert_agent_may_act(tab.context.id)` first. Read-only operations
  (`snapshot`, `find`, `screenshot`, `events`) are **not** gated — an agent must still be able to
  observe what the human is doing.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from saidkick.locators import Locator
from saidkick import actions as A, errors as E

pytestmark = pytest.mark.browser


async def test_mutating_action_blocked_while_human_holds(tab, controller):
    controller.take(tab.context.id)
    with pytest.raises(E.HumanHoldsControl):
        await A.click(tab, Locator(css="#go"))


async def test_read_only_still_allowed_while_human_holds(tab, controller):
    """The agent must be able to watch the rescue it asked for."""
    controller.take(tab.context.id)
    assert await A.find(tab, Locator(css="#go"))
    assert (await A.screenshot(tab))[:8] == b"\x89PNG\r\n\x1a\n"


async def test_fails_fast_rather_than_queueing(tab, controller):
    """Spec §5: the call must return immediately, not block until release."""
    import asyncio
    controller.take(tab.context.id)
    with pytest.raises(E.HumanHoldsControl):
        await asyncio.wait_for(A.click(tab, Locator(css="#go")), timeout=0.5)


async def test_action_resumes_after_release(tab, controller):
    controller.take(tab.context.id); controller.release(tab.context.id)
    await A.click(tab, Locator(css="#go"))
```

- [ ] **Step 2–4:** run (fail), thread a `controller` reference onto `ManagedContext`, run (pass).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(control): gate mutating actions on the controller"
```

---

## Task 15: Takeover input forwarding

**Files:**
- Create: `src/saidkick/input_bridge.py`
- Modify: `src/saidkick/api.py` (`WS /ws/control/{tid}`)
- Test: `tests/test_input_bridge.py`, `tests/engine/test_takeover.py`

**Interfaces:**
- Produces:
  - `to_cdp(msg: dict, metadata: dict) -> tuple[str, dict]` — pure translation of a cockpit
    message to a CDP method and params, with canvas→viewport scaling. **No I/O, unit-tested.**
  - `async forward(tab, msg, metadata)` — calls `to_cdp` and sends over CDP, refusing unless the
    controller state is `human`.

- [ ] **Step 1: Write the failing tests (pure, no browser)**

```python
import pytest
from saidkick.input_bridge import to_cdp

# canvas is 640 wide for a 1280-wide device: everything scales by 2.
META = {"deviceWidth": 1280, "deviceHeight": 800, "pageScaleFactor": 1,
        "scrollOffsetX": 0, "scrollOffsetY": 0}
CANVAS = {"width": 640, "height": 400}


def test_mouse_coordinates_scale_from_canvas_to_viewport():
    method, params = to_cdp({"type": "mousedown", "x": 100, "y": 50,
                             "button": "left", "canvas": CANVAS}, META)
    assert method == "Input.dispatchMouseEvent"
    assert params["x"] == 200 and params["y"] == 100


def test_key_event_maps_to_dispatch_key_event():
    method, params = to_cdp({"type": "keydown", "key": "Enter", "code": "Enter",
                             "modifiers": []}, META)
    assert method == "Input.dispatchKeyEvent"
    assert params["key"] == "Enter"


def test_modifier_bitmask():
    _, params = to_cdp({"type": "keydown", "key": "a", "code": "KeyA",
                        "modifiers": ["ctrl", "shift"]}, META)
    assert params["modifiers"] == 2 | 8   # ctrl=2, shift=8 per CDP


def test_paste_uses_insert_text_not_synthesised_keystrokes():
    """Spec §6: a 2FA code is inserted, not typed thirty times."""
    method, params = to_cdp({"type": "paste", "text": "123456"}, META)
    assert method == "Input.insertText" and params["text"] == "123456"


def test_unknown_message_type_rejected():
    with pytest.raises(ValueError):
        to_cdp({"type": "levitate"}, META)


def test_scroll_offset_is_not_added_twice():
    """CDP viewport coords are already scroll-relative; adding scrollOffset double-counts."""
    meta = dict(META, scrollOffsetY=300)
    _, params = to_cdp({"type": "mousedown", "x": 100, "y": 50,
                        "button": "left", "canvas": CANVAS}, meta)
    assert params["y"] == 100
```

Then the browser-level test that it really drives the page:

```python
@pytest.mark.browser
async def test_forwarded_click_actually_clicks(tab, controller):
    from saidkick.input_bridge import forward
    controller.take(tab.context.id)
    box = await tab.page.locator("#go").bounding_box()
    meta = {"deviceWidth": 1280, "deviceHeight": 800, "pageScaleFactor": 1,
            "scrollOffsetX": 0, "scrollOffsetY": 0}
    pt = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2,
          "canvas": {"width": 1280, "height": 800}, "button": "left"}
    await forward(tab, {"type": "mousemove", **pt}, meta)
    await forward(tab, {"type": "mousedown", "clickCount": 1, **pt}, meta)
    await forward(tab, {"type": "mouseup", "clickCount": 1, **pt}, meta)
    assert "submitted:" in await tab.page.locator("#out").text_content()


@pytest.mark.browser
async def test_input_refused_when_agent_holds_control(tab, controller):
    from saidkick.input_bridge import forward
    with pytest.raises(E.HumanHoldsControl):
        await forward(tab, {"type": "paste", "text": "x"}, {})
```

The second one is inverted on purpose and needs a distinct error: forwarding human input while
the *agent* holds control is also a violation. Use `HumanHoldsControl` with a detail saying the
human does not currently hold control, rather than inventing a twelfth error outside §9's closed
set.

- [ ] **Step 2–4:** run (fail), implement, run (pass).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(control): takeover input forwarding over CDP"
```

---

## Task 16: Attention overlay

**Files:**
- Create: `src/saidkick/overlay.py`
- Test: `tests/engine/test_overlay.py`

**Interfaces:**
- Produces: `async show(tab, reason: str)`, `async hide(tab)`. Idempotent both ways.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from saidkick import overlay
from saidkick.snapshot import snapshot
from saidkick.locators import Locator
from saidkick import actions as A

pytestmark = pytest.mark.browser


async def test_overlay_is_invisible_to_the_agent(tab):
    """The single most important test in VS2. If the agent can see the banner,
    it will try to click saidkick's own UI instead of the page."""
    before = await snapshot(tab, mode="aria")
    await overlay.show(tab, "enter the 2FA code")
    after = await snapshot(tab, mode="aria")
    assert before == after


async def test_overlay_host_not_findable(tab):
    from saidkick import errors as E
    await overlay.show(tab, "help")
    with pytest.raises(E.LocatorNotFound):
        await A.find(tab, Locator(by_text="enter the 2FA code", wait_ms=300))


async def test_overlay_is_actually_present_in_the_dom(tab):
    """Guard against 'invisible' being achieved by not rendering anything."""
    await overlay.show(tab, "help")
    assert await tab.page.evaluate(
        "() => !!document.querySelector('#saidkick-attention')") is True


async def test_title_and_favicon_restored_on_hide(tab):
    original = await tab.page.title()
    await overlay.show(tab, "help")
    assert await tab.page.title() != original
    await overlay.hide(tab)
    assert await tab.page.title() == original


async def test_show_is_idempotent(tab):
    await overlay.show(tab, "a"); await overlay.show(tab, "b")
    assert await tab.page.evaluate(
        "() => document.querySelectorAll('#saidkick-attention').length") == 1


async def test_hide_without_show_is_safe(tab):
    await overlay.hide(tab)
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

One host element, a closed shadow root, and the two ARIA attributes that make Playwright's
`aria_snapshot()` skip the subtree entirely:

```javascript
const host = document.createElement('div');
host.id = 'saidkick-attention';
host.setAttribute('aria-hidden', 'true');       // excluded from the a11y tree
host.setAttribute('role', 'presentation');
host.style.cssText = 'position:fixed;inset:0;z-index:2147483647;pointer-events:none';
const root = host.attachShadow({mode: 'closed'}); // invisible to page scripts and to querySelector
root.innerHTML = `<style>…pulsing border…</style><div class="banner">${reason}</div>`;
document.documentElement.appendChild(host);
```

Original `document.title` and the favicon `href` are stashed on `window.__saidkickPrev` so `hide`
restores them exactly — which is what `test_title_and_favicon_restored_on_hide` checks. Also call
`Page.bringToFront` via CDP in `show`, guarded so it is a no-op in headless.

- [ ] **Step 4: Verify.** → 6 passed.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(control): agent-invisible attention overlay"
```

---

## Task 17: Terminal dashboard

**Files:**
- Create: `src/saidkick/dashboard.py`
- Modify: `src/saidkick/cli.py` (`serve` renders it unless `--quiet`)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Produces: `render(engine, controller) -> rich.console.RenderableType`, and
  `async run_dashboard(engine, controller, refresh_hz=4)` driving `rich.live.Live`.
  Pure `render` is what gets tested; the `Live` loop is not.

- [ ] **Step 1: Write the failing tests**

```python
from rich.console import Console
from saidkick.dashboard import render


def _text(engine, controller):
    con = Console(width=120, record=True)
    con.print(render(engine, controller))
    return con.export_text()


def test_pending_request_is_shown_with_reason(fake_engine, controller):
    controller.open_request("ctx_a", "enter the 2FA code", deadline_s=600)
    out = _text(fake_engine, controller)
    assert "enter the 2FA code" in out and "ctx_a" in out


def test_pending_section_absent_when_nothing_pending(fake_engine, controller):
    assert "NEEDS YOU" not in _text(fake_engine, controller)


def test_controller_state_is_visible(fake_engine, controller):
    controller.take("ctx_a")
    assert "human" in _text(fake_engine, controller)


def test_cockpit_url_shown_for_pending_request(fake_engine, controller):
    controller.open_request("ctx_a", "help", deadline_s=600)
    assert "/session/ctx_a" in _text(fake_engine, controller)
```

`fake_engine` is a tiny stub in `conftest.py` exposing `list_contexts()` — the dashboard must not
need a real browser to render, which is why `render` takes data rather than reaching for it.

- [ ] **Step 2–4:** run (fail), implement, run (pass). Pending requests render in a panel above
  the context table, with reason, elapsed, remaining, and the cockpit URL.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(cli): live terminal dashboard for saidkick serve"
```

---

## Task 18: `request_human` MCP tool, description obligations, and the webhook

**Files:**
- Modify: `src/saidkick/mcp_server.py`, `src/saidkick/control.py`
- Create: `src/saidkick/notify.py`
- Test: `tests/api/test_request_human.py`, `tests/test_notify.py`

**Interfaces:**
- Produces: MCP tools `request_human`, `control_state`; `async post_webhook(url, payload)` in
  `notify.py`, called fire-and-forget when a request opens; config key `notify.webhook_url`
  (default `None`).

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from saidkick.mcp_server import build_mcp


async def test_request_human_description_obliges_the_agent_to_relay(engine, controller):
    """Spec §5.1 channel 2: the agent's own words are the most reliable notification."""
    d = {t.name: t.description for t in await build_mcp(engine, controller).list_tools()}
    text = d["request_human"].lower()
    assert "relay" in text or "tell" in text
    assert "still_waiting" in text
    assert "operator" in text or "user" in text or "human" in text


async def test_request_human_returns_a_relayable_message(engine, controller):
    out = await controller.request_human("ctx_a", "solve the captcha",
                                         deadline_s=0.1, poll_s=0.05)
    assert "solve the captcha" in out["human_message"]


async def test_webhook_is_off_by_default(monkeypatch):
    from saidkick import notify
    calls = []
    monkeypatch.setattr(notify, "post_webhook", lambda *a, **k: calls.append(a))
    from saidkick.control import Controller
    c = Controller(webhook_url=None)
    await c.request_human("ctx_a", "x", deadline_s=0.1, poll_s=0.05)
    assert calls == []


async def test_webhook_payload_is_unbranded(monkeypatch):
    """No vendor-specific fields; a user wires this to whatever they use."""
    from saidkick import notify
    seen = {}
    async def fake(url, payload): seen.update(payload)
    monkeypatch.setattr(notify, "post_webhook", fake)
    from saidkick.control import Controller
    c = Controller(webhook_url="http://example.invalid/hook")
    await c.request_human("ctx_a", "x", deadline_s=0.1, poll_s=0.05)
    assert set(seen) == {"context", "reason", "url", "deadline"}
```

- [ ] **Step 2–4:** run (fail), implement, run (pass). Webhook failures are swallowed and logged
  — a dead webhook must never break a rescue.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(mcp): request_human with relay obligations and generic webhook"
```

---

## Task 19: Cockpit takeover UI

**Files:**
- Modify: `src/saidkick/cockpit/templates/session.html`,
  `src/saidkick/cockpit/static/cockpit.js`, `src/saidkick/api.py`
- Test: `tests/api/test_takeover_ws.py`

**Interfaces:**
- Produces: `WS /ws/control/{tid}` accepting `{type:"take"}`, `{type:"release", note}`,
  and the input messages from Task 15. `GET /requests` lists pending requests for the UI.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from fastapi.testclient import TestClient
from saidkick.api import create_app


@pytest.mark.browser
def test_take_and_release_round_trip(engine_sync, controller, fixture_url):
    with TestClient(create_app(engine_sync, controller)) as c:
        cid = c.post("/contexts").json()["id"]
        tid = c.post(f"/contexts/{cid}/tabs",
                     json={"url": f"{fixture_url}/form.html"}).json()["id"]
        with c.websocket_connect(f"/ws/control/{tid}") as ws:
            ws.send_json({"type": "take"})
            assert ws.receive_json()["state"] == "human"
            ws.send_json({"type": "release", "note": "done"})
            assert ws.receive_json()["state"] == "agent"
        assert controller.state(cid) == "agent"


@pytest.mark.browser
def test_disconnect_releases_control(engine_sync, controller, fixture_url):
    """A closed laptop lid must not leave an agent permanently locked out."""
    with TestClient(create_app(engine_sync, controller)) as c:
        cid = c.post("/contexts").json()["id"]
        tid = c.post(f"/contexts/{cid}/tabs",
                     json={"url": f"{fixture_url}/form.html"}).json()["id"]
        with c.websocket_connect(f"/ws/control/{tid}") as ws:
            ws.send_json({"type": "take"}); ws.receive_json()
        assert controller.state(cid) == "agent"
```

The second test encodes a rule the spec does not state and should: **control is released when the
control socket closes.** Without it, a browser tab closed mid-takeover strands the context in
`human` forever and the agent never recovers. Add this to §5 as a spec amendment when the task
lands.

- [ ] **Step 2–4:** run (fail), implement, run (pass). The JS raises quality to 95/native on take
  and drops back to 60/1280 on release (§6 adaptive table).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(cockpit): takeover UI with release-on-disconnect"
```

---

## Task 20: VS2 gate, docs, and the end-to-end rescue

**Files:**
- Modify: `README.md`, `SKILL.md`, `CHANGELOG.md`
- Create: `tests/test_rescue_e2e.py`

- [ ] **Step 1: Write the end-to-end test that is the whole point**

```python
import asyncio, pytest

@pytest.mark.browser
async def test_agent_asks_for_help_and_is_rescued(engine, controller, fixture_url):
    """VS1+VS2 in one test: agent blocks, human takes over, types, releases, agent resumes."""
    from saidkick import actions as A
    from saidkick.locators import Locator
    from saidkick import overlay

    ctx = await engine.open_context()
    tab = await ctx.open_tab(f"{fixture_url}/form.html")

    async def agent():
        out = await controller.request_human(ctx.id, "enter the 2FA code",
                                             deadline_s=10, poll_s=10)
        assert out["status"] == "resolved" and out["note"] == "code entered"
        await A.click(tab, Locator(by_text="Send"))
        return await tab.page.locator("#out").text_content()

    task = asyncio.create_task(agent())
    await asyncio.sleep(0.2)

    assert controller.pending(ctx.id) is not None
    await overlay.show(tab, "enter the 2FA code")
    controller.take(ctx.id)
    with pytest.raises(Exception):
        await A.click(tab, Locator(css="#go"))          # agent locked out
    await tab.page.locator("#u").fill("123456")         # human drives
    await overlay.hide(tab)
    controller.release(ctx.id, note="code entered")

    assert await asyncio.wait_for(task, timeout=5) == "submitted:123456"
```

- [ ] **Step 2: Run the full gate, each as its own step**

```bash
uv run ruff check src tests
uv run pytest -m "not browser" -q
uv run pytest -m browser -q
```

Check each exit code separately. Never `cmd | tail` inside a chain — a pipe masks the real code.

- [ ] **Step 3: Rewrite README.md and SKILL.md**

README: new architecture diagram (daemon owns Chromium; no extension), quickstart
(`saidkick serve` → `saidkick quick URL`), the cockpit, the human-in-the-loop story, and a
"migrating from 1.x" section listing every removed command (`start`, `mirror`, `exec`, `logs`,
`doctor`) and the `br-XXXX` → `ctx_XXXX` change. SKILL.md: rewrite for the MCP surface, leading
with `snapshot(mode="aria")` and the obligation to relay `request_human` reasons.

- [ ] **Step 4: CHANGELOG entry for 2.0.0** with a prominent BREAKING section.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "docs: rewrite README and SKILL for the 2.0 architecture"
```

---

## Self-Review

**Spec coverage.** §3 object model → Tasks 4, 13 (Profile/attached deferred to VS3 by design).
§4 layering → the file structure. §5 arbitration → 13, 14. §5.1 three channels → 17 (terminal),
18 (tool descriptions), 16 (overlay), 18 (webhook). §6 screencast → 10; takeover → 15, 19; pins →
**VS4, not covered here, by design**. §7 MCP → 9, 18. §8 CLI → 12. §9 errors → 2. §10 persistence
→ **VS5, explicitly excluded** in Global Constraints. §11 testing → every task. §12 migration →
1, 12, 20.

**Two rules discovered while writing tests, which the spec should absorb:**

1. **Release control when the control socket closes** (Task 19). Otherwise a closed browser tab
   strands a context in `human` forever.
2. **Read-only operations are not gated by arbitration** (Task 14). The agent must be able to
   observe the rescue it requested; gating `snapshot` would blind it exactly when it most needs
   to see.

**Type consistency.** `ManagedContext.id` / `ManagedTab.id` are used identically in Tasks 4, 8,
10, 14, 15, 19. `Controller.state/take/release/assert_agent_may_act/request_human/pending/
list_pending` are named identically in 13, 14, 17, 18, 19. `Locator` fields are fixed in Task 3
and referenced unchanged after. `snapshot(tab, mode, within_css)` matches between 5, 9 and 16.

**Known risk.** Task 9's MCP mount depends on running `mcp.session_manager.run()` inside the
FastAPI lifespan; getting this wrong produces hanging requests rather than a clear error. The
gotcha and its fix are written into the task.
