import pytest

from saidkick import errors as E

pytestmark = pytest.mark.browser


async def test_open_and_list_context(engine):
    ctx = await engine.open_context()
    assert ctx.id.startswith("ctx_")
    assert [c["id"] for c in engine.list_contexts()] == [ctx.id]


async def test_contexts_are_isolated(engine, fixture_url):
    a, b = await engine.open_context(), await engine.open_context()
    ta = await a.open_tab(f"{fixture_url}/form.html")
    await ta.page.evaluate("localStorage.setItem('k','from-a')")
    tb = await b.open_tab(f"{fixture_url}/form.html")
    assert await tb.page.evaluate("localStorage.getItem('k')") is None


async def test_tab_id_is_context_scoped(engine, fixture_url):
    ctx = await engine.open_context()
    t = await ctx.open_tab(f"{fixture_url}/index.html")
    assert t.id.startswith(ctx.id + ":")


async def test_unknown_context_raises(engine):
    with pytest.raises(E.NoSuchContext):
        engine.get_context("ctx_zzzz")


async def test_unknown_tab_raises(ctx):
    with pytest.raises(E.NoSuchTab):
        ctx.get_tab("ctx_zzzz:99")


async def test_closed_tab_is_gone(ctx, fixture_url):
    t = await ctx.open_tab(f"{fixture_url}/index.html")
    await ctx.close_tab(t.id)
    with pytest.raises(E.NoSuchTab):
        ctx.get_tab(t.id)


async def test_tab_ids_are_never_reused(ctx, fixture_url):
    """A closed tab's id must stay permanently invalid, not silently rebind."""
    first = await ctx.open_tab(f"{fixture_url}/index.html")
    await ctx.close_tab(first.id)
    second = await ctx.open_tab(f"{fixture_url}/index.html")
    assert second.id != first.id


async def test_navigate_bad_url_raises_navigation_failed(tab):
    with pytest.raises(E.NavigationFailed):
        await tab.navigate("http://127.0.0.1:1/nope", wait="load")


async def test_close_context_closes_its_tabs(engine, fixture_url):
    ctx = await engine.open_context()
    await ctx.open_tab(f"{fixture_url}/index.html")
    await engine.close_context(ctx.id)
    assert engine.list_contexts() == []


async def test_stop_is_idempotent(engine):
    await engine.stop()
    await engine.stop()
