# saidkick 2.0 — Production hardening (Tiers 1–3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Take saidkick from "works on my laptop" to something safe to run on a server and to
build products on: authenticated, resource-bounded, honest about dialogs, crash-tolerant, and
observable.

**Architecture:** All hardening lands in the **daemon** layer. `Engine`, `Controller`,
`PinRegistry` and `ProfileStore` stay plain in-process objects with no auth, no HTTP and no
lifecycle policy — saidkick is a library you can import as much as a service you can run, and
the embedding path must not get worse. Auth is middleware on the FastAPI app; the reaper is a
daemon task; the run log is an optional sink the engine *emits to*, never depends on.

**Tech Stack:** adds `beaver-db` 2.1 (verified: `db.log(name).log(data)` / `.range(limit=)` /
`.count()`, entries carry `timestamp` + `data`, durable across reopen).

**Spec:** `docs/specs/2026-07-23-saidkick-2-agent-native-browser-design.md`. This slice replaces
the spec's VS5 with a superset: VS5's observability is Tier 3 here, and Tiers 1–2 cover gaps the
spec did not name.

## Global Constraints

Inherits prior slices' (English only, no vendor coupling, closed error set, `@browser` marker,
commit per task, gate as separate steps). New:

- **The library path stays clean.** `Engine`/`Controller`/`PinRegistry`/`ProfileStore` must remain
  constructible and usable with no daemon, no auth, no token, no beaver. A test asserts this.
- **Auth is daemon-only.** No engine-layer code may reference a token.
- **No secret is ever written to the run log.** Typed text is redacted to a length + SHA-256
  prefix by default; full capture is opt-in via config and off by default.
- **New config lives in one place** — `saidkick.config.Settings`, read from env
  (`SAIDKICK_*`) with CLI overrides.

## File Structure

| File | Responsibility |
|---|---|
| `src/saidkick/config.py` | `Settings` dataclass: token, bind, limits, TTL, log opts. |
| `src/saidkick/auth.py` | Token load/generate/verify + FastAPI dependency & WS check. |
| `src/saidkick/dialogs.py` | Per-context dialog policy + handler + record. |
| `src/saidkick/reaper.py` | Idle-context reaping and the context cap. |
| `src/saidkick/capture.py` | Per-tab console + network ring buffers. |
| `src/saidkick/runlog.py` | Beaver-backed run log with redaction; no-op when disabled. |
| `src/saidkick/tracing.py` | Playwright trace start/stop/download. |

Modified: `engine.py` (activity stamp, crash detection, capture hookup, emit to sink),
`api.py`, `mcp_server.py`, `cli.py`, `client.py`, `profiles.py` (atomic write).

---

# Tier 1 — blockers

### Task 1: Config + token auth

**Files:** Create `src/saidkick/config.py`, `src/saidkick/auth.py`; modify `api.py`, `cli.py`,
`client.py`; Test `tests/test_auth.py`.

**Interfaces (Produces):**
- `Settings(token: str | None, require_auth: bool, max_contexts: int, idle_ttl_s: float,
  runlog: bool, redact: bool, trace_dir: Path)`; `Settings.from_env(**overrides)`.
- `auth.resolve_token(settings, home) -> str | None` — uses `SAIDKICK_TOKEN`, else
  `$SAIDKICK_HOME/token`, else generates one and writes it `0600`.
- `auth.install(app, token)` — middleware rejecting unauthenticated requests with **401**.
  Accepts `Authorization: Bearer <t>`, `X-Saidkick-Token: <t>`, `?token=<t>` (for the cockpit and
  WebSockets, which cannot set headers), or a `saidkick_token` cookie. A successful `?token=`
  on an HTML route sets the cookie so the cockpit works thereafter.
- `/health` is the only unauthenticated route (liveness probes).
- **Refuse to bind a non-loopback host with auth disabled** — `serve` exits with a clear error.

- [ ] **Step 1: failing tests**

```python
import pytest
from httpx import ASGITransport, AsyncClient
from saidkick.api import create_app
from saidkick.config import Settings


def _app(engine, token):
    return create_app(engine, settings=Settings(token=token, require_auth=True))


async def test_health_is_open(engine):
    app = _app(engine, "t0k")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/health")).status_code == 200


async def test_unauthenticated_request_is_401(engine):
    app = _app(engine, "t0k")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/contexts")).status_code == 401


@pytest.mark.parametrize("send", [
    lambda: {"headers": {"Authorization": "Bearer t0k"}},
    lambda: {"headers": {"X-Saidkick-Token": "t0k"}},
    lambda: {"params": {"token": "t0k"}},
])
async def test_every_accepted_credential_form(engine, send):
    app = _app(engine, "t0k")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/contexts", **send())).status_code == 200


async def test_wrong_token_is_401(engine):
    app = _app(engine, "t0k")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/contexts", headers={"Authorization": "Bearer nope"})).status_code == 401


async def test_auth_disabled_allows_everything(engine):
    app = create_app(engine, settings=Settings(token=None, require_auth=False))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/contexts")).status_code == 200


def test_token_file_is_generated_0600(tmp_path, monkeypatch):
    monkeypatch.setenv("SAIDKICK_HOME", str(tmp_path))
    monkeypatch.delenv("SAIDKICK_TOKEN", raising=False)
    from saidkick.auth import resolve_token
    from saidkick.config import Settings as S
    t1 = resolve_token(S(require_auth=True))
    assert t1 and (tmp_path / "token").exists()
    assert oct((tmp_path / "token").stat().st_mode)[-3:] == "600"
    assert resolve_token(S(require_auth=True)) == t1   # stable across calls


def test_env_token_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("SAIDKICK_HOME", str(tmp_path))
    monkeypatch.setenv("SAIDKICK_TOKEN", "from-env")
    from saidkick.auth import resolve_token
    from saidkick.config import Settings as S
    assert resolve_token(S(require_auth=True)) == "from-env"


def test_token_comparison_is_constant_time():
    """Guard against a `==` regression that would leak the token by timing."""
    import inspect
    from saidkick import auth
    assert "compare_digest" in inspect.getsource(auth)
```

- [ ] **Step 2:** run → fail. **Step 3:** implement. **Step 4:** run → pass.
- [ ] **Step 5: WS auth + non-loopback guard.** WebSocket routes check `?token=`; on failure
  `await ws.close(code=4401)` before accept. In `cli.serve`, if `host` is not a loopback address
  and auth is off, print an error and `raise typer.Exit(1)`. Test both.
- [ ] **Step 6:** `SaidkickClient(token=...)` sends the bearer header; `_client()` in the CLI
  reads `SAIDKICK_TOKEN`/token file. `saidkick token` prints the current one.
- [ ] **Step 7:** commit `feat(auth)!: bearer-token auth on every surface`.

---

### Task 2: Dialog policy — no more silent dismissal

**Files:** Create `src/saidkick/dialogs.py`; modify `engine.py`, `api.py`, `mcp_server.py`;
Test `tests/engine/test_dialogs.py`.

**The bug being fixed:** Playwright auto-dismisses dialogs. An agent that clicks something firing
`confirm("Delete?")` sees the click succeed while the page took the *cancel* branch, with no
signal anywhere. Even the auto modes must therefore **record and emit** every dialog.

**Interfaces (Produces):**
- `DialogPolicy = "auto_dismiss" | "auto_accept" | "ask_human"`; context-level, default
  `auto_dismiss`, settable at `open_context` and via `set_dialog_policy`.
- `ManagedTab.dialogs: list[dict]` — a bounded record of `{type, message, action, ts}`.
- `install_dialog_handler(tab)` — a `page.on("dialog")` handler honouring the policy, appending
  to the record and emitting a `dialog` event.
- `ask_human`: the dialog is left open, a human request opens with the dialog text, and mutating
  actions on that context raise **`DialogBlocked`** until a human accepts/dismisses it from the
  cockpit (`POST /tabs/{tid}/dialog {"accept": bool, "text": str|None}`).
- MCP: `dialogs(tab)` reads the record; `open_context` gains `dialog_policy`.

- [ ] **Step 1: failing tests** (using the unused `dialog.html` fixture):

```python
import pytest
from saidkick import actions as A, errors as E
from saidkick.locators import Locator

pytestmark = pytest.mark.browser


async def test_auto_dismiss_is_recorded_not_silent(engine, fixture_url):
    ctx = await engine.open_context()
    tab = await ctx.open_tab(f"{fixture_url}/dialog.html")
    await A.click(tab, Locator(css="#ask"))
    assert await tab.page.locator("#result").text_content() == "dismissed"
    assert tab.dialogs and tab.dialogs[-1]["message"] == "proceed?"
    assert tab.dialogs[-1]["action"] == "dismissed"


async def test_auto_accept_takes_the_other_branch(engine, fixture_url):
    ctx = await engine.open_context(dialog_policy="auto_accept")
    tab = await ctx.open_tab(f"{fixture_url}/dialog.html")
    await A.click(tab, Locator(css="#ask"))
    assert await tab.page.locator("#result").text_content() == "accepted"
    assert tab.dialogs[-1]["action"] == "accepted"


async def test_dialog_emits_an_event(engine, events, fixture_url):
    ctx = await engine.open_context()
    tab = await ctx.open_tab(f"{fixture_url}/dialog.html")
    await A.click(tab, Locator(css="#ask"))
    assert any(e["kind"] == "dialog" for e in events.since(ctx.id, 0))


async def test_ask_human_blocks_the_agent(engine, controller, fixture_url):
    ctx = await engine.open_context(dialog_policy="ask_human")
    tab = await ctx.open_tab(f"{fixture_url}/dialog.html")
    import asyncio
    asyncio.create_task(A.click(tab, Locator(css="#ask")))
    await asyncio.sleep(0.5)
    assert controller.pending(ctx.id) is not None
    with pytest.raises(E.DialogBlocked):
        await A.click(tab, Locator(css="#ask"))


async def test_resolving_an_asked_dialog_unblocks(engine, fixture_url):
    ctx = await engine.open_context(dialog_policy="ask_human")
    tab = await ctx.open_tab(f"{fixture_url}/dialog.html")
    import asyncio
    asyncio.create_task(A.click(tab, Locator(css="#ask")))
    await asyncio.sleep(0.5)
    await tab.resolve_dialog(accept=True)
    assert await tab.page.locator("#result").text_content() == "accepted"
    await A.click(tab, Locator(css="#ask"))   # no raise now
```

- [ ] **Step 2–4:** run/implement/run.
- [ ] **Step 5:** commit `fix(dialogs): record and honour dialogs instead of silently dismissing`.

---

### Task 3: Resource lifecycle — cap and reaper

**Files:** Create `src/saidkick/reaper.py`; modify `engine.py`, `api.py`, `cli.py`;
Test `tests/test_reaper.py` (no browser) + a browser case.

**Interfaces (Produces):**
- `ManagedContext.last_activity: float`, bumped by every engine action and tab operation;
  `ManagedContext.idle_s` property.
- `Engine.open_context` raises `TooManyContexts` when `len(self._contexts) >= max_contexts`.
  **New error, added to the closed set** (429) — the only addition, and it is genuinely distinct:
  the agent's recovery is "close something first".
- `reap_idle(engine, ttl_s) -> list[str]` — closes contexts idle beyond the TTL, returns their
  ids. Never reaps a context whose controller is `human` or that has a pending human request —
  a human mid-rescue must not have the page yanked away.
- `run_reaper(engine, settings, events)` — the periodic daemon task.

- [ ] **Step 1: failing tests** — cap raises `TooManyContexts`; reaping closes an idle context;
  activity bumps prevent reaping; a human-held context is never reaped; a context with a pending
  request is never reaped; reaping emits an event.
- [ ] **Step 2–4:** run/implement/run.
- [ ] **Step 5:** commit `feat(engine): context cap and idle reaping`.

---

# Tier 2 — will bite

### Task 4: Atomic profile writes, crash detection, startup preflight

**Files:** Modify `profiles.py`, `engine.py`, `cli.py`; Test `tests/test_profiles.py`,
`tests/engine/test_crash.py`.

**Interfaces (Produces):**
- `ProfileStore.save_state` writes `<file>.tmp` then `os.replace` — atomic on POSIX.
- `Engine` listens for `browser.on("disconnected")`, sets `self._crashed = True`; `open_context`
  auto-restarts a crashed engine; operations on contexts of a dead browser raise `EngineCrashed`.
- `Engine.preflight()` — verifies the Chromium executable exists, raising `EngineCrashed` with
  the exact `uv run playwright install chromium` fix. Called by `serve` at startup, before bind.

- [ ] **Step 1: failing tests** — a save interrupted by a simulated crash leaves the previous
  state readable (write to a path, assert no `.tmp` remains and the file parses); killing the
  browser marks the engine crashed and the next `open_context` recovers; `preflight` on a bogus
  browser path raises with an actionable message.
- [ ] **Step 2–4:** run/implement/run.
- [ ] **Step 5:** commit `fix(engine,profiles): atomic saves, crash recovery, startup preflight`.

---

### Task 5: Console and network capture

**Files:** Create `src/saidkick/capture.py`; modify `engine.py`, `api.py`, `mcp_server.py`;
Test `tests/engine/test_capture.py`.

**Interfaces (Produces):**
- `TabCapture` — bounded deques (default 200 each) fed by `page.on("console")`,
  `page.on("response")`, `page.on("requestfailed")`.
- `console(tab, grep=None, level=None) -> list[dict]` — `{level, text, ts}`.
- `network(tab, since=None, failed_only=False) -> list[dict]` —
  `{method, url, status, ok, ts}`; failed requests carry `error`.
- REST `GET /tabs/{tid}/console`, `GET /tabs/{tid}/network`; MCP `console`, `network` with
  descriptions pointing agents at them for debugging a failing page.
- Fixture additions: a page that logs an error and fetches a 404.

- [ ] **Step 1: failing tests** — a console error is captured with its level; grep filters;
  a failed request appears in `network` with `ok=False`; the ring is bounded.
- [ ] **Step 2–4:** run/implement/run.
- [ ] **Step 5:** commit `feat(capture): console and network capture`.

---

# Tier 3 — observability

### Task 6: Beaver run log with redaction

**Files:** Create `src/saidkick/runlog.py`; modify `engine.py`, `actions.py`, `control.py`,
`api.py`, `cli.py`; Test `tests/test_runlog.py`.

**Interfaces (Produces):**
- `RunLog(db_path: Path | None, redact: bool = True)` — `None` path ⇒ a no-op sink, so the
  library path never requires beaver.
- `record(kind, ctx, **fields)` — appends to `db.log("runs")`.
- **Redaction:** any `text` field becomes `{"text_len": n, "text_sha256": "<first 12 hex>"}`
  unless `redact=False`. A test asserts a known password never appears in the DB bytes.
- Records: actions (kind, ctx, tab, locator, ok/error, duration_ms), control transitions,
  human requests + resolutions, dialogs, reaps.
- `query(ctx=None, limit=100) -> list[dict]`; REST `GET /runlog`; CLI `saidkick runlog`.

- [ ] **Step 1: failing tests** — a click is recorded with duration and ok; a failed action
  records the error code; `type` text is redacted by default and the plaintext is absent from the
  file on disk; `redact=False` keeps it; a `None` path no-ops without beaver installed being
  required; records survive a reopen.
- [ ] **Step 2–4:** run/implement/run.
- [ ] **Step 5:** commit `feat(runlog): beaver-backed run log with redaction`.

---

### Task 7: Tracing

**Files:** Create `src/saidkick/tracing.py`; modify `api.py`, `mcp_server.py`, `cli.py`;
Test `tests/engine/test_tracing.py`.

**Interfaces (Produces):**
- `start_trace(ctx)` / `stop_trace(ctx) -> Path` wrapping `context.tracing.start/stop`
  (screenshots + snapshots + sources), writing to `settings.trace_dir`.
- REST `POST /contexts/{cid}/trace/start`, `POST /contexts/{cid}/trace/stop` (returns the path),
  `GET /traces/{name}` (download). MCP `start_trace` / `stop_trace`. CLI `saidkick trace`.

- [ ] **Step 1: failing tests** — start→act→stop produces a non-empty `.zip`; stopping without
  starting is a clean error; the file lands under `trace_dir`.
- [ ] **Step 2–4:** run/implement/run.
- [ ] **Step 5:** commit `feat(tracing): Playwright trace capture`.

---

### Task 8: Library path, gate, docs, release 2.3.0

**Files:** `README.md`, `SKILL.md`, `CHANGELOG.md`, `pyproject.toml`; Test
`tests/test_library_usage.py`.

- [ ] **Step 1: the embedding test** — this is the one that protects the "library you can build
  on" promise:

```python
@pytest.mark.browser
async def test_engine_is_usable_with_no_daemon_no_auth_no_beaver(tmp_path, fixture_url):
    """saidkick must be importable as a library: no FastAPI, no token, no beaver."""
    from saidkick.engine import Engine
    from saidkick.profiles import ProfileStore
    from saidkick import actions as A
    from saidkick.locators import Locator
    from saidkick.snapshot import snapshot

    engine = Engine(store=ProfileStore(root=tmp_path / "p"))
    await engine.start()
    try:
        ctx = await engine.open_context()
        tab = await ctx.open_tab(f"{fixture_url}/form.html")
        assert 'button "Send"' in await snapshot(tab)
        await A.type_text(tab, Locator(css="#u"), "lib")
        await A.click(tab, Locator(css="#go"))
        assert await tab.page.locator("#out").text_content() == "submitted:lib"
    finally:
        await engine.stop()


def test_engine_layer_has_no_auth_imports():
    """Auth is daemon-only; an engine-layer import of it would be a layering break."""
    import inspect
    from saidkick import actions, engine, locators, pins, profiles, snapshot
    for mod in (engine, actions, locators, pins, profiles, snapshot):
        src = inspect.getsource(mod)
        assert "from .auth" not in src and "import auth" not in src, mod.__name__
```

- [ ] **Step 2: full gate**, each its own step, checking each rc:
  `uvx ruff check src tests` · `uv run pytest -m "not browser" -q` · `uv run pytest -m browser -q`.
- [ ] **Step 3:** README: a **Security** section (token, bind, what auth does and does not cover)
  and a **Use as a library** section with the embedding example. SKILL.md: console/network for
  debugging, dialogs. CHANGELOG 2.3.0; bump version.
- [ ] **Step 4:** commit `feat: hardening docs and 2.3.0`.

---

## Self-Review

**Coverage of the agreed tiers.** T1 auth → Task 1; T1 dialogs → Task 2; T1 lifecycle → Task 3.
T2 atomic writes / crash recovery / preflight → Task 4; T2 console+network → Task 5. T3 run log →
Task 6; T3 tracing → Task 7. Library promise + docs → Task 8.

**One new error type.** `TooManyContexts` (429) is added to §9's closed set. Justified: the
agent's recovery ("close a context") is distinct from every existing error. Every other need is
served by the existing set — `DialogBlocked` finally gets its first raiser, having been dead code
since VS1.

**Redaction is a requirement, not a feature.** Task 6 must not ship without the test asserting a
known password is absent from the on-disk bytes; a run log that records typed text is a
credential store nobody asked for.

**Risk.** Task 2's `ask_human` dialog path is the most intricate: the dialog must stay open while
a human decides, which means the `page.on("dialog")` handler cannot return until resolved. If
that proves to deadlock Playwright's event loop, the fallback is to capture the dialog text,
dismiss it, open the human request, and let the agent retry after the human answers — worse UX,
same safety, and it must be recorded as a deviation rather than silently substituted.
