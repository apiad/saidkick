import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from saidkick.api import create_app

pytestmark = pytest.mark.browser


@pytest_asyncio.fixture
async def client(engine, controller):
    app = create_app(engine, controller)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_index_renders_with_no_contexts(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "No contexts open" in r.text


async def test_index_lists_contexts(client):
    cid = (await client.post("/contexts")).json()["id"]
    assert cid in (await client.get("/")).text


async def test_index_surfaces_pending_requests(client, controller):
    controller.open_request("ctx_a", "solve the captcha", deadline_s=600)
    body = (await client.get("/")).text
    assert "Needs you" in body and "solve the captcha" in body


async def test_session_page_lists_tabs(client, fixture_url):
    cid = (await client.post("/contexts")).json()["id"]
    tid = (
        await client.post(f"/contexts/{cid}/tabs", json={"url": f"{fixture_url}/form.html"})
    ).json()["id"]
    body = (await client.get(f"/session/{cid}")).text
    assert tid in body
    assert "Take over" in body


async def test_session_page_for_unknown_context_is_404(client):
    r = await client.get("/session/ctx_zzzz")
    assert r.status_code == 404 and r.json()["error"] == "NoSuchContext"


async def test_static_assets_are_served(client):
    assert (await client.get("/static/cockpit.js")).status_code == 200
    assert (await client.get("/static/cockpit.css")).status_code == 200
