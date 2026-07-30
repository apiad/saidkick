"""`click` must be able to send a right button.

`actions.click` already forwards **kwargs to Playwright, which takes
`button="right"` — but the REST ACTIONS table dropped every body key except the
locator, so the capability existed in the library and was unreachable over the
wire. Anything behind a context menu was undriveable, which is where magpie
keeps its cross-app handoffs ("Analyze in Superbot", "Visualize in Peacock").
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from saidkick.api import create_app


@pytest_asyncio.fixture
async def client(engine, controller):
    app = create_app(engine, controller)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


@pytest.mark.browser
async def test_right_click_over_the_action_dispatch(ctx, fixture_url, client):
    """Drives the REST surface, not actions.click directly — the gap was in the
    dispatch table, so calling the library function would test the wrong layer.
    """
    tab = await ctx.open_tab(f"{fixture_url}/contextmenu.html")
    r = await client.post(f"/tabs/{tab.id}/click",
                          json={"css": "#target", "button": "right"})
    assert r.status_code == 200, r.text
    assert await tab.page.locator("#menu").is_visible()


@pytest.mark.browser
async def test_click_still_defaults_to_left(ctx, fixture_url, client):
    tab = await ctx.open_tab(f"{fixture_url}/contextmenu.html")
    r = await client.post(f"/tabs/{tab.id}/click", json={"css": "#target"})
    assert r.status_code == 200, r.text
    assert await tab.page.locator("#menu").is_hidden(), \
        "a plain click opened the context menu — the default is not left"
