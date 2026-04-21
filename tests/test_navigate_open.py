from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_navigate_routes_with_defaults():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"url": "https://example.com/"}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/navigate",
            json={"tab": "br-aaaa:1", "url": "https://example.com/"},
        )
    assert r.status_code == 200
    assert r.json() == {"url": "https://example.com/"}
    assert seen["args"][1] == "NAVIGATE"
    assert seen["payload"]["tab_id"] == 1
    assert seen["payload"]["url"] == "https://example.com/"
    assert seen["payload"]["wait"] == "dom"
    assert seen["payload"]["timeout_ms"] == 15000


def test_navigate_custom_wait_and_timeout():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"url": "https://x/"}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/navigate",
            json={
                "tab": "br-aaaa:1", "url": "https://x/",
                "wait": "full", "timeout_ms": 30000,
            },
        )
    assert r.status_code == 200
    assert seen["payload"]["wait"] == "full"
    assert seen["payload"]["timeout_ms"] == 30000


def test_navigate_malformed_url_is_400():
    setup_single_browser()
    r = TestClient(app).post(
        "/navigate", json={"tab": "br-aaaa:1", "url": "javascript:alert(1)"}
    )
    assert r.status_code == 400


def test_navigate_bad_wait_mode_is_422():
    setup_single_browser()
    r = TestClient(app).post(
        "/navigate",
        json={"tab": "br-aaaa:1", "url": "https://x/", "wait": "sort-of"},
    )
    assert r.status_code == 422


def test_navigate_timeout_is_504():
    setup_single_browser()
    async def fake_send(*args, **kwargs):
        return {"success": False, "payload": "navigation timeout after 15000ms"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/navigate", json={"tab": "br-aaaa:1", "url": "https://x/"}
        )
    assert r.status_code == 504


def test_open_routes_with_defaults():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"tab_id": 77, "url": "https://x/"}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/open", json={"browser": "br-aaaa", "url": "https://x/"}
        )
    assert r.status_code == 200
    body = r.json()
    assert body == {"tab": "br-aaaa:77", "url": "https://x/"}
    assert seen["args"][0] == "br-aaaa"
    assert seen["args"][1] == "OPEN"
    assert seen["payload"]["url"] == "https://x/"
    assert seen["payload"]["wait"] == "dom"
    assert seen["payload"]["timeout_ms"] == 15000
    assert seen["payload"]["activate"] is False


def test_open_activate_flag():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"tab_id": 77, "url": "https://x/"}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/open",
            json={"browser": "br-aaaa", "url": "https://x/", "activate": True},
        )
    assert r.status_code == 200
    assert seen["payload"]["activate"] is True


def test_open_malformed_browser_is_400():
    r = TestClient(app).post(
        "/open", json={"browser": "bad-id", "url": "https://x/"}
    )
    assert r.status_code == 400


def test_open_unknown_browser_is_404():
    manager.connections.clear()
    r = TestClient(app).post(
        "/open", json={"browser": "br-ffff", "url": "https://x/"}
    )
    assert r.status_code == 404


def test_open_malformed_url_is_400():
    setup_single_browser()
    r = TestClient(app).post(
        "/open", json={"browser": "br-aaaa", "url": "javascript:alert(1)"}
    )
    assert r.status_code == 400
