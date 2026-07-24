"""Actions an agent performs on a tab.

Every action funnels through :func:`_one` so that "not found" and "ambiguous"
are raised identically everywhere, and so that arbitration is checked in one
place rather than remembered at each call site.

Mutating actions are gated on the controller. Read-only ones (``find``,
``screenshot``) deliberately are not: an agent must still be able to observe
the rescue it asked for, and blinding it exactly when a human is fixing
something would be the wrong trade.
"""

import functools
import inspect
import time
from typing import TYPE_CHECKING, Any

from playwright.async_api import Error as PWError
from playwright.async_api import Locator as PWLocator
from playwright.async_api import TimeoutError as PWTimeout

from . import errors as E
from .dialogs import assert_no_pending_dialog
from .locators import Locator, resolve
from .runlog import timed

if TYPE_CHECKING:  # pragma: no cover
    from .engine import ManagedTab

DEFAULT_TIMEOUT_MS = 5000
MAX_CANDIDATES = 10

_DESCRIBE_JS = """
e => ({
  tag: e.tagName,
  text: (e.innerText || e.value || '').trim().slice(0, 120),
  id: e.id || null,
  role: e.getAttribute('role'),
  label: e.getAttribute('aria-label'),
  placeholder: e.getAttribute('placeholder'),
})
"""


def _gate(tab: "ManagedTab") -> None:
    """Refuse a mutating action while a human holds the wheel.

    Also stamps activity: a context being acted on is not idle, and the reaper
    must not close it out from under a working agent.
    """
    tab.context.touch()
    controller = tab.context.controller
    if controller is not None:
        controller.assert_agent_may_act(tab.context.id)
    # A dialog held open under the ask_human policy blocks the page entirely;
    # acting into it would hang rather than fail.
    assert_no_pending_dialog(tab)


def _sink(tab: "ManagedTab"):
    """The run log for this tab's engine, or the shared no-op."""
    from .runlog import NULL_RUNLOG

    return getattr(tab.context.engine, "runlog", None) or NULL_RUNLOG


def recorded(kind: str):
    """Wrap an action so its outcome and duration land in the run log.

    Records failures too: 'the agent tried and it did not resolve' is the more
    useful half of the history.
    """

    def decorate(fn):
        signature = inspect.signature(fn)

        def _detail(args, kwargs) -> dict:
            """Pull loggable fields out however the caller passed them.

            Binding the signature matters: `type_text(tab, loc, "secret")` puts
            the text in *args, so reading kwargs alone would record nothing and
            make the redaction guarantee vacuous.
            """
            try:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
            except TypeError:  # pragma: no cover - the call is about to fail anyway
                return {}
            values = bound.arguments
            loc = values.get("loc")
            return {
                "text": values.get("text"),
                "locator": loc.describe() if isinstance(loc, Locator) else None,
            }

        @functools.wraps(fn)
        async def wrapper(tab, *args, **kwargs):
            started = time.monotonic()
            sink = _sink(tab)
            detail = _detail((tab, *args), kwargs)
            try:
                result = await fn(tab, *args, **kwargs)
            except E.SaidkickError as exc:
                sink.record(
                    kind, ctx=tab.context.id, tab=tab.id, ok=False,
                    error=exc.code, ms=timed(started), **detail,
                )
                raise
            sink.record(
                kind, ctx=tab.context.id, tab=tab.id, ok=True, ms=timed(started), **detail
            )
            return result

        return wrapper

    return decorate


async def _describe_all(found: PWLocator, limit: int = MAX_CANDIDATES) -> list[dict]:
    n = min(await found.count(), limit)
    return [await found.nth(i).evaluate(_DESCRIBE_JS) for i in range(n)]


async def _one(
    tab: "ManagedTab", loc: Locator, timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> PWLocator:
    """Resolve to exactly one element, or raise from the closed error set."""
    # Read-only paths reach here without _gate, so stamp activity here too.
    tab.context.touch()
    found = resolve(tab.page, loc)
    timeout = loc.wait_ms or timeout_ms

    try:
        await found.first.wait_for(state="attached", timeout=timeout)
    except PWTimeout as exc:
        raise E.LocatorNotFound(f"no element matched {loc.describe()}") from exc

    if loc.nth is None:
        count = await found.count()
        if count > 1:
            raise E.LocatorAmbiguous(
                f"found {count} matches for {loc.describe()}; retry with nth or a narrower locator",
                candidates=await _describe_all(found),
            )
    return found.first


@recorded("click")
async def click(
    tab: "ManagedTab", loc: Locator, timeout_ms: int = DEFAULT_TIMEOUT_MS, **kwargs: Any
) -> dict:
    _gate(tab)
    el = await _one(tab, loc, timeout_ms)
    await el.click(timeout=timeout_ms, **kwargs)
    return {"ok": True}


@recorded("type_text")
async def type_text(
    tab: "ManagedTab",
    loc: Locator,
    text: str,
    submit: bool = False,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict:
    _gate(tab)
    el = await _one(tab, loc, timeout_ms)
    # fill() raises on anything that is not a form control, so contenteditable
    # (every Lexical/ProseMirror/Quill editor) takes the click-and-type path.
    if await el.evaluate("e => e.isContentEditable"):
        await el.click(timeout=timeout_ms)
        await el.type(text, delay=0)
    else:
        await el.fill(text, timeout=timeout_ms)
    if submit:
        await el.press("Enter")
    return {"ok": True}


@recorded("press")
async def press(
    tab: "ManagedTab",
    key: str,
    loc: Locator | None = None,
    modifiers: list[str] | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict:
    _gate(tab)
    combo = "+".join([*(modifiers or []), key])
    if loc is not None and loc.primaries():
        el = await _one(tab, loc, timeout_ms)
        await el.press(combo, timeout=timeout_ms)
    else:
        await tab.page.keyboard.press(combo)
    return {"ok": True}


@recorded("select")
async def select(
    tab: "ManagedTab", loc: Locator, values: list[str], timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> dict:
    _gate(tab)
    el = await _one(tab, loc, timeout_ms)
    try:
        chosen = await el.select_option(values, timeout=timeout_ms)
    except PWError as exc:
        raise E.LocatorNotFound(f"could not select {values} on {loc.describe()}") from exc
    return {"ok": True, "selected": chosen}


@recorded("hover")
async def hover(
    tab: "ManagedTab", loc: Locator, timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> dict:
    _gate(tab)
    el = await _one(tab, loc, timeout_ms)
    await el.hover(timeout=timeout_ms)
    return {"ok": True}


@recorded("scroll")
async def scroll(
    tab: "ManagedTab", loc: Locator, timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> dict:
    _gate(tab)
    el = await _one(tab, loc, timeout_ms)
    await el.scroll_into_view_if_needed(timeout=timeout_ms)
    return {"ok": True}


@recorded("upload")
async def upload(
    tab: "ManagedTab", loc: Locator, paths: list[str], timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> dict:
    _gate(tab)
    el = await _one(tab, loc, timeout_ms)
    await el.set_input_files(paths, timeout=timeout_ms)
    return {"ok": True, "files": paths}


async def highlight(
    tab: "ManagedTab",
    loc: Locator,
    color: str = "#ef4444",
    duration_ms: int = 2000,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict:
    """Ring an element so a human can see what the agent means.

    Not gated: highlighting is how an agent points at something *for* the
    human, which is most useful precisely when the human holds control.
    """
    el = await _one(tab, loc, timeout_ms)
    await el.evaluate(
        """
        (e, {color, duration}) => {
          const prev = e.style.outline;
          e.style.outline = '3px solid ' + color;
          if (duration > 0) setTimeout(() => { e.style.outline = prev; }, duration);
        }
        """,
        {"color": color, "duration": duration_ms},
    )
    return {"ok": True}


async def find(
    tab: "ManagedTab", loc: Locator, timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> list[dict]:
    """Describe every element matching the locator. Read-only, so not gated."""
    found = resolve(tab.page, loc)
    timeout = loc.wait_ms or timeout_ms
    try:
        await found.first.wait_for(state="attached", timeout=timeout)
    except PWTimeout as exc:
        raise E.LocatorNotFound(f"no element matched {loc.describe()}") from exc
    return await _describe_all(found)


async def screenshot(
    tab: "ManagedTab",
    loc: Locator | None = None,
    full_page: bool = False,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> bytes:
    """PNG bytes of the tab, or of one element. Read-only, so not gated."""
    if loc is not None and loc.primaries():
        el = await _one(tab, loc, timeout_ms)
        return await el.screenshot(timeout=timeout_ms)
    return await tab.page.screenshot(full_page=full_page, timeout=timeout_ms)
