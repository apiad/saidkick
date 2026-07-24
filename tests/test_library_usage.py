"""saidkick must be usable as a library, not only as a daemon.

These tests protect that promise. Hardening lands in the daemon layer — auth,
resource policy, durable logging — and none of it may become a requirement for
someone who just wants to drive a browser from their own Python.
"""

import inspect

import pytest


@pytest.mark.browser
async def test_engine_works_with_no_daemon_no_auth_no_settings(tmp_path, fixture_url):
    from saidkick import actions as A
    from saidkick.engine import Engine
    from saidkick.locators import Locator
    from saidkick.profiles import ProfileStore
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


@pytest.mark.browser
async def test_no_context_cap_without_settings(tmp_path):
    """An embedder that never configures limits must not hit one."""
    from saidkick.engine import Engine
    from saidkick.profiles import ProfileStore

    engine = Engine(store=ProfileStore(root=tmp_path / "p"))
    await engine.start()
    try:
        for _ in range(3):
            await engine.open_context()
        assert len(engine.list_contexts()) == 3
    finally:
        await engine.stop()


@pytest.mark.browser
async def test_actions_work_without_a_controller(tmp_path, fixture_url):
    """Arbitration is a daemon concern; a bare engine has no controller."""
    from saidkick import actions as A
    from saidkick.engine import Engine
    from saidkick.locators import Locator
    from saidkick.profiles import ProfileStore

    engine = Engine(store=ProfileStore(root=tmp_path / "p"))
    await engine.start()
    try:
        assert engine.controller is None
        ctx = await engine.open_context()
        tab = await ctx.open_tab(f"{fixture_url}/form.html")
        await A.click(tab, Locator(css="#go"))
    finally:
        await engine.stop()


def test_engine_layer_does_not_import_daemon_concerns():
    """A layering break here is what would make embedding painful later."""
    from saidkick import actions, capture, dialogs, engine, locators, pins, profiles, snapshot

    forbidden = ("from .auth", "from .api", "from .cli", "from fastapi")
    for mod in (engine, actions, locators, pins, profiles, snapshot, capture, dialogs):
        src = inspect.getsource(mod)
        for needle in forbidden:
            assert needle not in src, f"{mod.__name__} imports {needle}"


def test_runlog_is_optional_for_the_engine():
    """Engine must default to a no-op sink, never require beaver."""
    from saidkick.engine import Engine
    from saidkick.runlog import NULL_RUNLOG

    assert Engine().runlog is NULL_RUNLOG
    assert NULL_RUNLOG.enabled is False
