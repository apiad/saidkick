# saidkick 2.x — CLI parity: hover, right-click, dialogs

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use `- [ ]`.

**Status:** DRAFT — filed 2026-08-26 from a session that hit all four items in one
afternoon driving the AInBox suite (delete-functionality audit across magpie /
peacock / superbot / pipelines).

## The finding

**The capabilities are not missing. The CLI is a strict subset of the HTTP API,
and the CLI is what `SKILL.md` tells agents to use.**

| Capability | HTTP API | `client.py` | CLI |
|---|---|---|---|
| `hover` | ✅ `POST /tabs/{id}/hover` | ✅ `client.hover()` | ❌ |
| right-click | ✅ `button` in body | ✅ `client.click(button="right")` | ❌ no `--button` |
| `dialog_policy` on a context | ✅ `POST /contexts` body | ❌ | ❌ |
| list / answer a pending dialog | ✅ `GET,POST /tabs/{id}/dialog(s)` | ❌ | ❌ |

`actions.py` has `@recorded("hover")`, and `client.click` even documents
`button="right"` as *"the only way to reach an affordance that has no left-click
equivalent"* — which is exactly right, and exactly what a CLI-driven agent cannot
do.

## Why it matters more than a missing flag

Each gap does not read as "unsupported". It reads as **the app being broken**,
which is the expensive failure mode for a browser an agent uses to verify someone
else's software:

- **Hover-gated controls.** superbot's `.conv-controls` is `visibility: hidden`
  until `:hover`. Playwright refuses to click it, and the CLI surfaced a bare
  `500 Internal Server Error` — no locator, no reason. The natural agent
  conclusion is "the delete button is broken", not "I cannot reach it".
- **Right-click-only affordances.** magpie's folder delete is reachable *only*
  via `contextmenu` (`label.addEventListener("contextmenu", …)`). With no
  `--button right`, that whole surface is unverifiable from the CLI.
- **Dialogs.** `auto_dismiss` is the documented, deliberate default and
  `dialogs.py` is explicitly right about why. But a CLI agent has no way to set
  `auto_accept` for one flow, and no way to answer a pending dialog — so any
  click behind `confirm()` silently takes the cancel branch. This one already
  cost real confusion twice: a prior AInBox session recorded *"saidkick 2.x has
  no dialog command"* in `repos/ainbox/tasks.md` and abandoned a click-through,
  and this session repeated the same wrong conclusion before reading the source.
  **That note in ainbox's tasks.md is wrong and should be corrected when someone
  is next in that file** — the machinery exists, it is just off-CLI.

## Tasks

- [ ] **Add `saidkick hover`** — mirror `click`'s locator options exactly
      (`--css/--xpath/--by-text/--by-label/--by-placeholder/--by-role`,
      `--within-css`, `--nth`, `--exact`, `--regex`, `--wait-ms`). One line in
      the `_ACTIONS` table already exists in `api.py`; this is CLI-only work.
- [ ] **Add `--button {left,right,middle}` to `saidkick click`.** Default `left`.
      Verify against a real `contextmenu`-only affordance — magpie's folder tree
      row is a good fixture.
- [ ] **Expose `dialog_policy`** on `saidkick quick` and `saidkick open`
      (`--dialog-policy auto_dismiss|auto_accept|ask_human`), threading through
      `client.open_context`, which does not carry it today either.
- [ ] **Add `saidkick dialogs` and `saidkick dialog --accept/--dismiss [--text]`**,
      plus `client` methods for both. `requests` already exists for the
      `ask_human` path; this is the missing half.
- [ ] **Map actionability failures to a typed error — it is the DAEMON layer,
      not the CLI.** Traced 2026-08-26 rather than assumed, and the answer is
      narrower than "the 500 is untyped everywhere":

      - `cli.py:handle_client_error` already reads `{error, detail}` off the
        response body and only falls back to `str(exc)` — httpx's
        `raise_for_status()` message — when the body is not that shape.
      - `api.py:127` already turns any `SaidkickError` into
        `JSONResponse(status=exc.status, content=http_detail(exc))`.
      - Both work: `LocatorAmbiguous: found 12 matches for css=…` and
        `LocatorNotFound` render perfectly through this path.

      So a bare `Server error '500 Internal Server Error' for url …` means the
      exception never became a `SaidkickError` and escaped to FastAPI's default
      handler with no body to read. Clicking a `visibility: hidden` element
      (Playwright refuses on actionability) is a confirmed producer. `--css
      <sel> --nth <n>` produced the same shape and is NOT yet explained —
      `locators.py:101` supports `nth`, so reproduce it before assuming a cause.

      Fix: catch Playwright's `TimeoutError`/actionability failures in
      `actions.py` and raise a typed `ElementNotInteractable` naming the locator
      AND the reason ("matched, but `visibility: hidden` — try `hover` first"),
      which is the sentence that would have saved this session an hour.

      > **Check both layers even when one looks obviously guilty.** Credit to
      > the agent who fixed AInBox's sandbox pin the same day: there, typing the
      > service's 500 would have changed *nothing*, because the consumer built
      > its message from `raise_for_status()` and never read the body — two
      > layers, both needing the fix. Here the consumer is already correct and
      > only the producer is wrong. The lesson is the check, not the count.
- [ ] **Make `find` report WHY a match is unpickable.** Driving superbot's
      conversation list, `find --by-role button` returned a column of bare
      `BUTTON` entries with nothing to tell them apart, and `--css … ` returned
      `LocatorAmbiguous: found 12 matches`. Both are accurate and neither is
      actionable: the agent cannot tell whether the elements are genuinely
      indistinguishable, hidden, or merely unnamed. Two agents independently
      stalled there the same day, and the safe move — refusing to pick a
      destructive control positionally — is the *correct* one, so the tool
      should make it unnecessary rather than punish it.

      Suggested: have `find` print, per match, the accessible name (or
      `<unnamed>`), whether it is in the a11y tree, and the computed visibility.
      `12 matches, all <unnamed>, all visibility:hidden` diagnoses the page in
      one line; `found 12 matches` does not.

      (The AInBox side of that particular case is fixed — those buttons now
      carry distinguishing `aria-label`s and reveal on `:focus-within`. The
      saidkick-side gap is what an agent sees when they *don't*.)

- [ ] **Update `SKILL.md`.** It documents "no `exec` command in 2.x" but says
      nothing about CLI-vs-API parity, which is what sends agents to the wrong
      conclusion. State plainly which capabilities are API/client-only, and that
      `uv run python -c "from saidkick.client import ..."` is the escape hatch
      until the CLI catches up.

## Verification

Each task lands with a CLI-level test against a real fixture page — the parity
gap is precisely that the non-CLI paths were already covered.
