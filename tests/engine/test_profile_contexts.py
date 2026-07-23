import pytest

from saidkick import errors as E
from saidkick.engine import Engine
from saidkick.profiles import ProfileStore

pytestmark = pytest.mark.browser


@pytest.fixture
async def eng(tmp_path):
    """An engine with an isolated profile root, so tests never touch ~/.saidkick."""
    engine = Engine(store=ProfileStore(root=tmp_path / "profiles"))
    await engine.start()
    yield engine
    await engine.stop()


async def test_save_profile_writes_state(eng, fixture_url):
    ctx = await eng.open_context()
    tab = await ctx.open_tab(f"{fixture_url}/form.html")
    await tab.page.evaluate("localStorage.setItem('token', 'secret')")
    out = await eng.save_profile(ctx.id, "acme")
    assert out["profile"] == "acme"
    assert eng.store.load_state("acme") is not None


async def test_ephemeral_context_is_seeded_from_a_saved_profile(eng, fixture_url):
    # 1. Authenticate in one context and save it.
    a = await eng.open_context()
    ta = await a.open_tab(f"{fixture_url}/form.html")
    await ta.page.evaluate("localStorage.setItem('token', 'from-profile')")
    await eng.save_profile(a.id, "acme")

    # 2. A fresh ephemeral context on that profile starts with the saved state.
    b = await eng.open_context(profile="acme")
    tb = await b.open_tab(f"{fixture_url}/form.html")
    assert await tb.page.evaluate("localStorage.getItem('token')") == "from-profile"


async def test_ephemeral_without_profile_is_empty(eng, fixture_url):
    ctx = await eng.open_context()
    tab = await ctx.open_tab(f"{fixture_url}/form.html")
    assert await tab.page.evaluate("localStorage.getItem('token')") is None


async def test_attached_context_persists_across_reopen(eng, fixture_url):
    a = await eng.open_context(profile="persistent", mode="attached")
    ta = await a.open_tab(f"{fixture_url}/form.html")
    await ta.page.evaluate("localStorage.setItem('kept', 'yes')")
    await eng.close_context(a.id)

    b = await eng.open_context(profile="persistent", mode="attached")
    tb = await b.open_tab(f"{fixture_url}/form.html")
    assert await tb.page.evaluate("localStorage.getItem('kept')") == "yes"


async def test_profile_locked_on_second_attached_open(eng):
    await eng.open_context(profile="solo", mode="attached")
    with pytest.raises(E.ProfileLocked):
        await eng.open_context(profile="solo", mode="attached")


async def test_lock_released_after_close(eng):
    a = await eng.open_context(profile="solo", mode="attached")
    await eng.close_context(a.id)
    b = await eng.open_context(profile="solo", mode="attached")  # no raise
    assert b.mode == "attached"


async def test_attached_requires_a_profile(eng):
    with pytest.raises(ValueError):
        await eng.open_context(mode="attached")


async def test_attached_initial_page_is_tracked_not_leaked(eng):
    ctx = await eng.open_context(profile="p1", mode="attached")
    # The persistent context's blank page is adopted, so list_tabs is truthful.
    assert len(ctx.list_tabs()) == len(ctx.pw_context.pages)


async def test_info_reports_profile_and_mode(eng, fixture_url):
    ctx = await eng.open_context(profile="acme", mode="ephemeral")
    info = ctx.info()
    assert info["profile"] == "acme" and info["mode"] == "ephemeral"
