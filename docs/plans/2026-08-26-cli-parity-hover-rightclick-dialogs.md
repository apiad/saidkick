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
- [ ] **Triage the bare 500.** Two shapes produced an untyped
      `500 Internal Server Error` from the CLI rather than a saidkick error:
      (a) clicking a `visibility: hidden` element — should be a typed
      actionability/`ElementNotInteractable` error naming the locator and the
      reason; (b) `--css <sel> --nth <n>` — `locators.py:101` supports `nth`, so
      this needs an actual repro before assuming a cause. A 500 with no body is
      the least useful thing the CLI can say.
- [ ] **Update `SKILL.md`.** It documents "no `exec` command in 2.x" but says
      nothing about CLI-vs-API parity, which is what sends agents to the wrong
      conclusion. State plainly which capabilities are API/client-only, and that
      `uv run python -c "from saidkick.client import ..."` is the escape hatch
      until the CLI catches up.

## Verification

Each task lands with a CLI-level test against a real fixture page — the parity
gap is precisely that the non-CLI paths were already covered.
