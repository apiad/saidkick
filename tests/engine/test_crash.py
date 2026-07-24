import asyncio

import pytest

from saidkick import errors as E
from saidkick.engine import Engine
from saidkick.profiles import ProfileStore

pytestmark = pytest.mark.browser


@pytest.fixture
async def eng(tmp_path):
    engine = Engine(store=ProfileStore(root=tmp_path / "p"))
    await engine.start()
    yield engine
    await engine.stop()


async def test_browser_death_is_detected(eng, fixture_url):
    ctx = await eng.open_context()
    await ctx.open_tab(f"{fixture_url}/index.html")
    assert eng.crashed is False

    await eng._browser.close()  # simulate Chromium dying
    await asyncio.sleep(0.3)
    assert eng.crashed is True, "a dead browser went unnoticed"


async def test_open_context_recovers_after_a_crash(eng, fixture_url):
    ctx = await eng.open_context()
    await ctx.open_tab(f"{fixture_url}/index.html")
    await eng._browser.close()
    await asyncio.sleep(0.3)

    # The daemon must come back rather than handing out contexts on a corpse.
    fresh = await eng.open_context()
    tab = await fresh.open_tab(f"{fixture_url}/form.html")
    assert eng.crashed is False
    assert await tab.page.title() == "Form"


async def test_stale_contexts_are_dropped_after_a_crash(eng, fixture_url):
    ctx = await eng.open_context()
    await ctx.open_tab(f"{fixture_url}/index.html")
    await eng._browser.close()
    await asyncio.sleep(0.3)
    await eng.open_context()  # triggers the restart

    with pytest.raises(E.NoSuchContext):
        eng.get_context(ctx.id)


async def test_preflight_passes_when_the_browser_is_installed(eng):
    eng.preflight()  # no raise


async def test_preflight_names_the_fix_when_it_fails(eng, monkeypatch):
    """A missing browser must fail at startup with the command that fixes it,
    not at first use with a Playwright stack trace."""
    import playwright._impl._driver as driver

    def boom():
        raise RuntimeError("no driver")

    monkeypatch.setattr(driver, "compute_driver_executable", boom)
    with pytest.raises(E.EngineCrashed) as exc:
        eng.preflight()
    assert "playwright install chromium" in str(exc.value)
