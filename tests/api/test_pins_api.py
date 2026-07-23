import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from saidkick.api import create_app
from saidkick.pins import PinRegistry

pytestmark = pytest.mark.browser


@pytest_asyncio.fixture
async def setup(engine, controller, fixture_url):
    """Client, registry, and a tab on the form — engine used directly so pins
    can be minted on precise geometry without digging through the transport."""
    pins = PinRegistry()
    app = create_app(engine, controller, pins=pins)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        ctx = await engine.open_context()
        tab = await ctx.open_tab(f"{fixture_url}/form.html")
        yield client, pins, engine, tab


async def _pin_the_button(pins, tab):
    b = await tab.page.locator("#go").bounding_box()
    return await pins.mint_point(tab, b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)


async def test_create_pin_via_rest_and_read_bundle(setup):
    client, _, _, tab = setup
    pin = (await client.post(f"/tabs/{tab.id}/pin", json={"x": 0, "y": 0, "w": 400, "h": 300})).json()
    assert pin["handle"].startswith("el_")
    bundle = (await client.get(f"/pins/{pin['handle']}", params={"screenshot": True})).json()
    assert bundle["css"] and bundle["xpath"]
    assert "screenshot" in bundle


async def test_click_via_handle_submits_the_form(setup):
    client, pins, _, tab = setup
    pin = await _pin_the_button(pins, tab)
    await client.post(f"/tabs/{tab.id}/type", json={"css": "#u", "text": "pinned"})
    r = await client.post(f"/tabs/{tab.id}/click", json={"handle": pin.id})
    assert r.status_code == 200
    text = (await client.get(f"/tabs/{tab.id}/snapshot", params={"mode": "text"})).json()
    assert "submitted:pinned" in text["snapshot"]


async def test_stale_handle_is_410(setup, fixture_url):
    client, pins, _, tab = setup
    pin = await _pin_the_button(pins, tab)
    await client.post(f"/tabs/{tab.id}/navigate", json={"url": f"{fixture_url}/index.html"})
    r = await client.post(f"/tabs/{tab.id}/click", json={"handle": pin.id})
    assert r.status_code == 410 and r.json()["error"] == "StaleHandle"


async def test_list_pins_scoped_to_context(setup):
    client, pins, _, tab = setup
    await _pin_the_button(pins, tab)
    cid = tab.id.split(":", 1)[0]
    out = (await client.get(f"/contexts/{cid}/pins")).json()
    assert len(out) == 1 and out[0]["descriptor"]["tag"] == "BUTTON"


async def test_read_pin_omits_screenshot_unless_asked(setup):
    client, pins, _, tab = setup
    pin = await _pin_the_button(pins, tab)
    assert "screenshot" not in (await client.get(f"/pins/{pin.id}")).json()
