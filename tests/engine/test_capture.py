import asyncio

import pytest

pytestmark = pytest.mark.browser


@pytest.fixture
async def noisy(engine, fixture_url):
    ctx = await engine.open_context()
    tab = await ctx.open_tab(f"{fixture_url}/noisy.html")
    await asyncio.sleep(0.6)  # let the fetch settle
    return tab


async def test_console_messages_are_captured_with_levels(noisy):
    levels = {e["level"] for e in noisy.capture.read_console()}
    assert {"log", "warning", "error"} <= levels


async def test_console_grep_filters(noisy):
    found = noisy.capture.read_console(grep="boom")
    assert len(found) == 1 and "something broke" in found[0]["text"]


async def test_console_level_filters(noisy):
    errors = noisy.capture.read_console(level="error")
    assert errors and all(e["level"] == "error" for e in errors)


async def test_network_records_requests(noisy):
    urls = [e["url"] for e in noisy.capture.read_network()]
    assert any("noisy.html" in u for u in urls)


async def test_failed_request_is_visible(noisy):
    """A 404 on an XHR is usually the actual answer to 'why is the page wrong'."""
    failed = noisy.capture.read_network(failed_only=True)
    assert any("definitely-missing.json" in e["url"] for e in failed)


async def test_uncaught_page_error_is_captured(engine, fixture_url):
    """pageerror never reaches console, and it is the most important event."""
    ctx = await engine.open_context()
    tab = await ctx.open_tab(f"{fixture_url}/index.html")
    await tab.page.evaluate("setTimeout(() => { throw new Error('kaboom'); }, 0)")
    await asyncio.sleep(0.3)
    assert any(
        e["level"] == "pageerror" and "kaboom" in e["text"]
        for e in tab.capture.read_console()
    )


async def test_rings_are_bounded(engine, fixture_url):
    from saidkick.capture import TabCapture

    cap = TabCapture(size=10)
    for i in range(50):
        cap.console.append({"level": "log", "text": str(i), "ts": 0})
    assert len(cap.console) == 10
