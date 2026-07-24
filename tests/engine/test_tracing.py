import pytest

from saidkick import actions as A
from saidkick.locators import Locator
from saidkick.tracing import TraceManager

pytestmark = pytest.mark.browser


async def test_start_act_stop_writes_a_trace(engine, fixture_url, tmp_path):
    traces = TraceManager(tmp_path / "traces")
    ctx = await engine.open_context()
    tab = await ctx.open_tab(f"{fixture_url}/form.html")

    await traces.start(ctx)
    await A.type_text(tab, Locator(css="#u"), "traced")
    await A.click(tab, Locator(css="#go"))
    out = await traces.stop(ctx)

    assert out["bytes"] > 0
    assert out["path"].endswith(".zip")
    assert (tmp_path / "traces" / f"{ctx.id}.zip").is_file()
    assert "show-trace" in out["view"]


async def test_stopping_without_starting_is_a_clean_error(engine, tmp_path):
    traces = TraceManager(tmp_path / "traces")
    ctx = await engine.open_context()
    with pytest.raises(ValueError):
        await traces.stop(ctx)


async def test_starting_twice_is_a_clean_error(engine, tmp_path):
    traces = TraceManager(tmp_path / "traces")
    ctx = await engine.open_context()
    await traces.start(ctx)
    with pytest.raises(ValueError):
        await traces.start(ctx)
    await traces.stop(ctx)


async def test_is_tracing_reflects_state(engine, tmp_path):
    traces = TraceManager(tmp_path / "traces")
    ctx = await engine.open_context()
    assert traces.is_tracing(ctx.id) is False
    await traces.start(ctx)
    assert traces.is_tracing(ctx.id) is True
    await traces.stop(ctx)
    assert traces.is_tracing(ctx.id) is False
