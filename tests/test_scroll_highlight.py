from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_scroll_routes_with_defaults():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"scrolled_to": {"x": 0, "y": 800, "width": 100, "height": 40}}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/scroll", json={"tab": "br-aaaa:1", "by_text": "Chapter 3"}
        )
    assert r.status_code == 200
    assert seen["args"][1] == "SCROLL"
    assert seen["payload"]["by_text"] == "Chapter 3"
    assert seen["payload"]["block"] == "center"
    assert seen["payload"]["behavior"] == "auto"


def test_scroll_custom_block_and_behavior():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"scrolled_to": {"x": 0, "y": 0, "width": 100, "height": 40}}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/scroll",
            json={
                "tab": "br-aaaa:1", "css": "#target",
                "block": "start", "behavior": "smooth",
            },
        )
    assert r.status_code == 200
    assert seen["payload"]["block"] == "start"
    assert seen["payload"]["behavior"] == "smooth"


def test_scroll_invalid_block_is_422():
    setup_single_browser()
    r = TestClient(app).post(
        "/scroll", json={"tab": "br-aaaa:1", "css": "#x", "block": "middle"}
    )
    assert r.status_code == 422


def test_scroll_no_locator_is_400():
    setup_single_browser()
    r = TestClient(app).post("/scroll", json={"tab": "br-aaaa:1"})
    assert r.status_code == 400


def test_highlight_routes_with_defaults():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"highlighted": {"x": 100, "y": 200, "width": 80, "height": 30}}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/highlight", json={"tab": "br-aaaa:1", "by_text": "Send"}
        )
    assert r.status_code == 200
    assert seen["args"][1] == "HIGHLIGHT"
    assert seen["payload"]["by_text"] == "Send"
    assert seen["payload"]["color"] == "#ff3b30"
    assert seen["payload"]["duration_ms"] == 2000


def test_highlight_custom_color_and_duration():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"highlighted": {"x": 0, "y": 0, "width": 0, "height": 0}}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/highlight",
            json={
                "tab": "br-aaaa:1", "by_label": "Email",
                "color": "lime", "duration_ms": 0,
            },
        )
    assert r.status_code == 200
    assert seen["payload"]["color"] == "lime"
    assert seen["payload"]["duration_ms"] == 0


def test_highlight_no_locator_is_400():
    setup_single_browser()
    r = TestClient(app).post("/highlight", json={"tab": "br-aaaa:1"})
    assert r.status_code == 400


def test_highlight_element_not_found_is_404():
    setup_single_browser()
    async def fake_send(*args, **kwargs):
        return {"success": False, "payload": "element not found"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/highlight", json={"tab": "br-aaaa:1", "css": ".nope"}
        )
    assert r.status_code == 404
