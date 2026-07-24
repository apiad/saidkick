import time
from types import SimpleNamespace

import pytest

from saidkick.reaper import idle_candidates


class _Ctx:
    def __init__(self, ctx_id, idle_for):
        self.id = ctx_id
        self.last_activity = time.monotonic() - idle_for


def _engine(contexts, controller=None):
    return SimpleNamespace(_contexts={c.id: c for c in contexts}, controller=controller)


def test_idle_context_is_a_candidate():
    eng = _engine([_Ctx("ctx_a", idle_for=100)])
    assert idle_candidates(eng, ttl_s=60) == ["ctx_a"]


def test_recently_active_context_is_not():
    eng = _engine([_Ctx("ctx_a", idle_for=5)])
    assert idle_candidates(eng, ttl_s=60) == []


def test_ttl_zero_disables_reaping():
    eng = _engine([_Ctx("ctx_a", idle_for=99999)])
    assert idle_candidates(eng, ttl_s=0) == []


def test_human_held_context_is_never_reaped():
    """Yanking the page from a human mid-rescue would destroy their work."""
    from saidkick.control import Controller

    controller = Controller()
    controller.take("ctx_a")
    eng = _engine([_Ctx("ctx_a", idle_for=99999)], controller)
    assert idle_candidates(eng, ttl_s=60) == []


def test_context_with_a_pending_request_is_never_reaped():
    """The agent asked for help and is waiting; the human has not answered yet."""
    from saidkick.control import Controller

    controller = Controller()
    controller.open_request("ctx_a", "solve the captcha", deadline_s=600)
    eng = _engine([_Ctx("ctx_a", idle_for=99999)], controller)
    assert idle_candidates(eng, ttl_s=60) == []


def test_mixed_set_reaps_only_the_idle_ones():
    from saidkick.control import Controller

    controller = Controller()
    controller.take("ctx_held")
    eng = _engine(
        [_Ctx("ctx_idle", 500), _Ctx("ctx_busy", 1), _Ctx("ctx_held", 500)], controller
    )
    assert idle_candidates(eng, ttl_s=60) == ["ctx_idle"]


@pytest.mark.browser
async def test_context_cap_raises_too_many(tmp_path):
    from saidkick import errors as E
    from saidkick.config import Settings
    from saidkick.engine import Engine
    from saidkick.profiles import ProfileStore

    engine = Engine(
        store=ProfileStore(root=tmp_path / "p"), settings=Settings(max_contexts=2)
    )
    await engine.start()
    try:
        await engine.open_context()
        await engine.open_context()
        with pytest.raises(E.TooManyContexts):
            await engine.open_context()
    finally:
        await engine.stop()


@pytest.mark.browser
async def test_closing_a_context_frees_a_slot(tmp_path):
    from saidkick.config import Settings
    from saidkick.engine import Engine
    from saidkick.profiles import ProfileStore

    engine = Engine(
        store=ProfileStore(root=tmp_path / "p"), settings=Settings(max_contexts=1)
    )
    await engine.start()
    try:
        ctx = await engine.open_context()
        await engine.close_context(ctx.id)
        await engine.open_context()  # no raise
    finally:
        await engine.stop()


@pytest.mark.browser
async def test_reap_idle_actually_closes(tmp_path, fixture_url):
    from saidkick.engine import Engine
    from saidkick.events import EventBus
    from saidkick.profiles import ProfileStore
    from saidkick.reaper import reap_idle

    engine = Engine(store=ProfileStore(root=tmp_path / "p"))
    await engine.start()
    try:
        ctx = await engine.open_context()
        await ctx.open_tab(f"{fixture_url}/index.html")
        ctx.last_activity = time.monotonic() - 500
        events = EventBus()
        reaped = await reap_idle(engine, ttl_s=60, events=events)
        assert reaped == [ctx.id]
        assert engine.list_contexts() == []
        assert any(e["kind"] == "context_reaped" for e in events.since(ctx.id, 0))
    finally:
        await engine.stop()


@pytest.mark.browser
async def test_activity_stamp_prevents_reaping(tmp_path, fixture_url):
    """A working agent must not have its context closed underneath it."""
    from saidkick import actions as A
    from saidkick.engine import Engine
    from saidkick.locators import Locator
    from saidkick.profiles import ProfileStore
    from saidkick.reaper import idle_candidates

    engine = Engine(store=ProfileStore(root=tmp_path / "p"))
    await engine.start()
    try:
        ctx = await engine.open_context()
        tab = await ctx.open_tab(f"{fixture_url}/form.html")
        ctx.last_activity = time.monotonic() - 500
        await A.click(tab, Locator(css="#go"))  # any action refreshes it
        assert idle_candidates(engine, ttl_s=60) == []
    finally:
        await engine.stop()
