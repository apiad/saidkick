# saidkick 2.0 — VS3 (persistent profiles) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Logins stop evaporating. A named profile persists to disk; an ephemeral context can be
seeded from it, an attached context reads and writes it, and `save_profile` captures a context's
authenticated state so the next context starts logged in.

**Architecture:** A `ProfileStore` owns `~/.saidkick/profiles/<name>/` — a `userdata/` dir for
attached mode and a `storage_state.json` seed for ephemeral mode. `Engine.open_context` grows a
`profile` and `mode`; `save_profile(context, name)` writes the context's `storage_state()` to
disk. `ProfileLocked` is enforced by the daemon (one live attached context per profile), not by
Chromium — verified: Chromium does not lock a headless user-data-dir.

**Tech Stack:** unchanged. `SAIDKICK_HOME` overrides the profile root (default `~/.saidkick`).


**Status:** COMPLETE — 2026-07-23. All 4 tasks on `main`; gate green (ruff clean,
115 non-browser + 122 browser). Released 2.2.0. Driven live headful: set a real
cookie on profile 'demo', save_profile captured it, a fresh seeded context read
`session=alex-42` back from httpbin while an anonymous context was empty;
attached ProfileLocked returned 409 on the second open.

**Deviations:**
1. `ProfileLocked` is daemon-enforced, not OS-enforced — the probe found Chromium
   does not lock a headless user-data-dir (spec §3 corrected before the plan).
2. Fixed a CLI display bug surfaced by the live demo: `[ephemeral:github]` in the
   contexts listing was swallowed by Rich as console markup; switched to parens.

**Spec:** §3 (Profile / Context), §7 (`save_profile`), §12 (bootstrapping). Every mechanism
probe-verified on Playwright 1.61.

## Global Constraints

Inherits prior slices'. New:

- **`storage_state()` carries cookies + localStorage only.** sessionStorage, IndexedDB and
  service workers do not survive ephemeral seeding (§3 table). Attached mode preserves everything
  via the user-data-dir.
- **`ProfileLocked` is daemon-enforced.** The engine tracks live attached contexts per profile;
  a second attached open on the same profile raises it.
- **Attached mode requires a profile.** `open_context(mode="attached")` with no profile is a
  `ValueError`.
- **Profile root is `SAIDKICK_HOME`/profiles** (default `~/.saidkick`). Tests set `SAIDKICK_HOME`
  to a tmp dir — never touch the real one.

## File Structure

- Create `src/saidkick/profiles.py` — `ProfileStore`.
- Modify `src/saidkick/engine.py` — `open_context(profile, mode)`, `save_profile`, attach lock,
  persistent-context lifecycle.
- Modify `src/saidkick/api.py`, `mcp_server.py`, `client.py`, `cli.py` — surface it.
- Tests: `tests/test_profiles.py` (no browser), `tests/engine/test_profile_contexts.py` (browser).

---

### Task 1: ProfileStore

**Files:** Create `src/saidkick/profiles.py`; Test `tests/test_profiles.py`.

**Interfaces (Produces):**
- `class ProfileStore(root: Path | None = None)` — root defaults to
  `Path(os.environ.get("SAIDKICK_HOME", "~/.saidkick")).expanduser() / "profiles"`.
  - `path(name) -> Path`, `userdata(name) -> Path`, `state_file(name) -> Path`.
  - `save_state(name, state: dict) -> None` (creates the dir, writes `storage_state.json`).
  - `load_state(name) -> dict | None`.
  - `exists(name) -> bool` (has a dir).
  - `list() -> list[dict]` — `{name, has_state, has_userdata, updated}` sorted by name.
  - `delete(name) -> None`.
  - Name validation: `[a-zA-Z0-9_-]+`, else `ValueError` (no path traversal).

- [x] **Step 1: failing tests** — validation rejects `../x` and `a/b`; `save_state`+`load_state`
  round-trips; `load_state` of an unknown profile is `None`; `list` reports `has_state` after a
  save; `delete` removes it; the root honours `SAIDKICK_HOME` (monkeypatched to tmp).
- [x] **Step 2–4:** run/implement/run.
- [x] **Step 5:** commit `feat(profiles): on-disk profile store`.

---

### Task 2: Engine — profile-backed contexts + save_profile

**Files:** Modify `src/saidkick/engine.py`; Test `tests/engine/test_profile_contexts.py`.

**Interfaces (Produces):**
- `Engine.__init__` gains `store: ProfileStore | None = None` (defaults to `ProfileStore()`).
- `open_context(profile=None, mode="ephemeral", viewport=None) -> ManagedContext`:
  - `ephemeral` + no profile → empty `new_context` (today's behaviour).
  - `ephemeral` + profile → `new_context(storage_state=store.state_file(profile))` when that file
    exists, else empty.
  - `attached` + profile → `chromium.launch_persistent_context(store.userdata(profile), ...)`;
    raises `ProfileLocked` if a live attached context already holds that profile; raises
    `ValueError` if no profile.
- `ManagedContext` gains `.profile: str | None`, `.mode: str`; `.info()` reports both.
- `save_profile(context_id, name) -> dict` — `state = await ctx.pw_context.storage_state()`,
  `store.save_state(name, state)`; returns `{profile, cookies, origins}` counts.
- `close_context` releases the attach lock; `stop()` closes attached contexts (which own their
  browser — verified `pc.browser is not None`, and `ctx.close()` tears the browser down).

- [x] **Step 1: failing tests** (browser):
  - ephemeral+profile seeded: mint state via `save_profile`, open a fresh ephemeral context on
    that profile, assert a cookie/localStorage value is present.
  - `save_profile` from an ephemeral context writes a state file (`store.load_state` non-None).
  - attached mode persists: write localStorage in an attached context, close it, reopen attached,
    value survives.
  - `ProfileLocked`: two attached opens on one profile → the second raises.
  - releasing (close) the first attached context lets a subsequent attached open succeed.
  - `attached` with no profile → `ValueError`.
  - a closed attached context's initial blank page is not leaked into `list_tabs`.
- [x] **Step 2–4:** run/implement/run. For attached, adopt the persistent context's initial page
  as the first tab (or close it) so `list_tabs` stays truthful. Track
  `self._attached: dict[str, str]` (profile → ctx_id); set on open, clear on close.
- [x] **Step 5:** commit `feat(engine): profile-backed contexts and save_profile`.

---

### Task 3: Surface it — REST, MCP, CLI

**Files:** Modify `api.py`, `mcp_server.py`, `client.py`, `cli.py`; extend `tests/api/test_rest.py`,
`tests/api/test_mcp.py`.

**Interfaces (Produces):**
- REST: `POST /contexts` body accepts `profile`, `mode`; `POST /contexts/{cid}/save-profile`
  body `{name}`; `GET /profiles`; `DELETE /profiles/{name}`.
- MCP: `open_context` gains `profile` / `mode`; new tools `save_profile(context, name)`,
  `list_profiles()`. Descriptions carry the obligations: `attached` writes to real credentials and
  is one-at-a-time; `save_profile` is how a login becomes durable after a human solves it;
  ephemeral seeding omits IndexedDB.
- CLI: `saidkick profiles`, `saidkick save-profile --context C --name N`; `open`/`quick` unchanged;
  `contexts` shows the profile column. `SaidkickClient.list_profiles/save_profile`.

- [x] **Step 1: failing tests** — `list_profiles` + `save_profile` MCP tools registered with real
  descriptions stating attached-writes-back and ephemeral-omits-IndexedDB; a REST round-trip that
  saves a profile and lists it; `saidkick profiles --help` exits 0.
- [x] **Step 2–4:** run/implement/run.
- [x] **Step 5:** commit `feat(profiles): REST/MCP/CLI surface`.

---

### Task 4: Gate, docs, release 2.2.0

- [x] **Step 1: full gate**, each its own step, check each rc:
  `uvx ruff check src tests` · `uv run pytest -m "not browser" -q` · `uv run pytest -m browser -q`.
- [x] **Step 2:** README "Profiles" section (the bootstrapping loop: ephemeral → login wall →
  request_human → takeover → save_profile → durable); the attached-vs-ephemeral table; SKILL.md
  note on when to use `attached` vs seeded ephemeral. CHANGELOG 2.2.0; bump version.
- [x] **Step 3:** commit `feat(profiles): docs and 2.2.0`.

---

## Self-Review

Spec coverage: §3 Profile → T1; attached/ephemeral modes → T2; ProfileLocked (daemon-enforced,
per the corrected spec) → T2; §7 save_profile → T2/T3; §12 bootstrapping loop → T3 descriptions +
README. `storage_state` cookies+localStorage-only limitation is a Global Constraint and drives the
attached-vs-ephemeral choice. No new error types — `ProfileLocked` already exists from VS1.
