import asyncio

import pytest

from saidkick import actions as A
from saidkick import errors as E
from saidkick.locators import Locator

pytestmark = pytest.mark.browser


async def test_auto_dismiss_is_recorded_not_silent(engine, fixture_url):
    """The bug: Playwright dismisses silently, so the agent believes the click
    did what it asked while the page took the cancel branch."""
    ctx = await engine.open_context()
    tab = await ctx.open_tab(f"{fixture_url}/dialog.html")
    await A.click(tab, Locator(css="#ask"))
    await asyncio.sleep(0.2)
    assert await tab.page.locator("#result").text_content() == "dismissed"
    assert tab.dialogs, "the dialog left no trace at all"
    assert tab.dialogs[-1]["message"] == "proceed?"
    assert tab.dialogs[-1]["action"] == "dismissed"
    assert tab.dialogs[-1]["type"] == "confirm"


async def test_auto_accept_takes_the_other_branch(engine, fixture_url):
    ctx = await engine.open_context(dialog_policy="auto_accept")
    tab = await ctx.open_tab(f"{fixture_url}/dialog.html")
    await A.click(tab, Locator(css="#ask"))
    await asyncio.sleep(0.2)
    assert await tab.page.locator("#result").text_content() == "accepted"
    assert tab.dialogs[-1]["action"] == "accepted"


async def _trigger_held_dialog(tab):
    """Fire a dialog under ask_human and return the (blocked) click task.

    The click never completes while the dialog is held, which is the point —
    the caller must cancel it so it does not leak into the next test.
    """
    task = asyncio.create_task(A.click(tab, Locator(css="#ask")))
    await asyncio.sleep(0.5)
    return task


async def test_ask_human_holds_the_dialog_and_blocks_the_agent(engine, controller, fixture_url):
    ctx = await engine.open_context(dialog_policy="ask_human")
    tab = await ctx.open_tab(f"{fixture_url}/dialog.html")
    task = await _trigger_held_dialog(tab)
    try:
        assert tab.dialogs[-1]["action"] == "pending"
        assert controller.pending(ctx.id) is not None, "no human was asked"
        with pytest.raises(E.DialogBlocked):
            await A.click(tab, Locator(css="#ask"))
    finally:
        task.cancel()


async def test_resolving_an_asked_dialog_unblocks_and_answers(engine, controller, fixture_url):
    ctx = await engine.open_context(dialog_policy="ask_human")
    tab = await ctx.open_tab(f"{fixture_url}/dialog.html")
    task = await _trigger_held_dialog(tab)
    try:
        await tab.resolve_dialog(accept=True)
        await asyncio.sleep(0.3)
        assert await tab.page.locator("#result").text_content() == "accepted"
        assert controller.pending(ctx.id) is None
        assert tab._pending_dialog is None
        # An action that does NOT open a new dialog now works. Clicking #ask
        # again would correctly block on a fresh dialog, so it is not the test.
        await A.hover(tab, Locator(css="#result"))
    finally:
        task.cancel()


async def test_resolve_without_a_pending_dialog_is_an_error(engine, fixture_url):
    ctx = await engine.open_context()
    tab = await ctx.open_tab(f"{fixture_url}/dialog.html")
    with pytest.raises(E.DialogBlocked):
        await tab.resolve_dialog(accept=True)


async def test_dialog_history_is_bounded(engine, fixture_url):
    ctx = await engine.open_context()
    tab = await ctx.open_tab(f"{fixture_url}/dialog.html")
    for _ in range(60):
        tab.record_dialog({"message": "x", "action": "dismissed"})
    assert len(tab.dialogs) == 50


async def test_default_policy_is_auto_dismiss(engine, fixture_url):
    ctx = await engine.open_context()
    assert ctx.dialog_policy == "auto_dismiss"
