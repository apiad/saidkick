import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from saidkick.api import create_app


@pytest_asyncio.fixture
async def client(engine, controller):
    app = create_app(engine, controller)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_health(client):
    assert (await client.get("/health")).json()["ok"] is True


async def test_unknown_context_is_404_with_code(client):
    r = await client.get("/contexts/ctx_zzzz/tabs")
    assert r.status_code == 404 and r.json()["error"] == "NoSuchContext"


@pytest.mark.browser
async def test_context_lifecycle(client):
    cid = (await client.post("/contexts")).json()["id"]
    assert cid in [c["id"] for c in (await client.get("/contexts")).json()]
    assert (await client.delete(f"/contexts/{cid}")).status_code == 200
    assert (await client.get("/contexts")).json() == []


@pytest.mark.browser
async def test_open_tab_and_snapshot(client, fixture_url):
    cid = (await client.post("/contexts")).json()["id"]
    tid = (
        await client.post(f"/contexts/{cid}/tabs", json={"url": f"{fixture_url}/form.html"})
    ).json()["id"]
    snap = (await client.get(f"/tabs/{tid}/snapshot")).json()
    assert 'button "Send"' in snap["snapshot"]


@pytest.mark.browser
async def test_ambiguous_locator_is_400_with_candidates(client, fixture_url):
    cid = (await client.post("/contexts")).json()["id"]
    tid = (
        await client.post(f"/contexts/{cid}/tabs", json={"url": f"{fixture_url}/form.html"})
    ).json()["id"]
    r = await client.post(f"/tabs/{tid}/click", json={"css": "label", "wait_ms": 300})
    assert r.status_code == 400
    assert r.json()["error"] == "LocatorAmbiguous" and len(r.json()["candidates"]) == 2


@pytest.mark.browser
async def test_human_holds_control_is_409(client, engine, controller, fixture_url):
    cid = (await client.post("/contexts")).json()["id"]
    tid = (
        await client.post(f"/contexts/{cid}/tabs", json={"url": f"{fixture_url}/form.html"})
    ).json()["id"]
    controller.take(cid)
    r = await client.post(f"/tabs/{tid}/click", json={"css": "#go"})
    assert r.status_code == 409 and r.json()["error"] == "HumanHoldsControl"


@pytest.mark.browser
async def test_type_then_click_submits(client, fixture_url):
    cid = (await client.post("/contexts")).json()["id"]
    tid = (
        await client.post(f"/contexts/{cid}/tabs", json={"url": f"{fixture_url}/form.html"})
    ).json()["id"]
    await client.post(f"/tabs/{tid}/type", json={"css": "#u", "text": "dave"})
    await client.post(f"/tabs/{tid}/click", json={"css": "#go"})
    text = (await client.get(f"/tabs/{tid}/snapshot", params={"mode": "text"})).json()
    assert "submitted:dave" in text["snapshot"]


@pytest.mark.browser
async def test_screenshot_returns_png(client, fixture_url):
    cid = (await client.post("/contexts")).json()["id"]
    tid = (
        await client.post(f"/contexts/{cid}/tabs", json={"url": f"{fixture_url}/form.html"})
    ).json()["id"]
    r = await client.get(f"/tabs/{tid}/screenshot")
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.browser
async def test_events_accumulate_with_seq(client, fixture_url):
    cid = (await client.post("/contexts")).json()["id"]
    await client.post(f"/contexts/{cid}/tabs", json={"url": f"{fixture_url}/index.html"})
    evs = (await client.get(f"/contexts/{cid}/events", params={"since": 0})).json()
    kinds = [e["kind"] for e in evs]
    assert "context_opened" in kinds and "tab_opened" in kinds
    assert evs == sorted(evs, key=lambda e: e["seq"])


@pytest.mark.browser
async def test_pending_requests_are_listed(client, controller):
    controller.open_request("ctx_a", "solve the captcha", deadline_s=60)
    out = (await client.get("/requests")).json()
    assert out[0]["reason"] == "solve the captcha"


@pytest.mark.browser
async def test_profile_save_seed_and_list_round_trip(tmp_path, controller, fixture_url):
    """Save a login, then open a seeded ephemeral context that already has it.

    Uses an isolated profile root so the real ~/.saidkick is never touched.
    """
    from httpx import ASGITransport, AsyncClient

    from saidkick.api import create_app
    from saidkick.engine import Engine
    from saidkick.profiles import ProfileStore

    engine = Engine(controller=controller, store=ProfileStore(root=tmp_path / "profiles"))
    await engine.start()
    try:
        app = create_app(engine, controller)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            cid = (await client.post("/contexts")).json()["id"]
            tid = (
                await client.post(f"/contexts/{cid}/tabs", json={"url": f"{fixture_url}/form.html"})
            ).json()["id"]
            await client.post(f"/tabs/{tid}/type", json={"css": "#u", "text": "kept"})
            # Persist a localStorage value we can check after seeding.
            await client.post(
                f"/tabs/{tid}/find", json={"css": "#u"}
            )  # ensure page ready
            saved = (await client.post(f"/contexts/{cid}/save-profile", json={"name": "acme"})).json()
            assert saved["profile"] == "acme"

            listed = (await client.get("/profiles")).json()
            assert any(p["name"] == "acme" and p["has_state"] for p in listed)

            # A seeded context reuses the saved cookies/localStorage.
            seeded = (await client.post("/contexts", json={"profile": "acme"})).json()
            assert seeded["profile"] == "acme" and seeded["mode"] == "ephemeral"
    finally:
        await engine.stop()


@pytest.mark.browser
async def test_attached_without_profile_is_400(client):
    r = await client.post("/contexts", json={"mode": "attached"})
    assert r.status_code == 400 and r.json()["error"] == "BadMode"
