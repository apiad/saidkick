from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_text_routes_with_defaults():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": "Hello world"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/text?tab=br-aaaa:1")
    assert r.status_code == 200
    assert r.json() == "Hello world"
    assert seen["args"][1] == "GET_TEXT"
    assert seen["payload"]["tab_id"] == 1
    assert seen["payload"]["css"] is None
    assert seen["payload"]["wait_ms"] == 0


def test_text_with_css_scope_and_wait():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": "scoped"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/text?tab=br-aaaa:1&css=main&wait_ms=500")
    assert r.status_code == 200
    assert seen["payload"]["css"] == "main"
    assert seen["payload"]["wait_ms"] == 500


def test_text_element_not_found_is_404():
    setup_single_browser()
    async def fake_send(*args, **kwargs):
        return {"success": False, "payload": "element not found"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/text?tab=br-aaaa:1&css=.nope")
    assert r.status_code == 404


def test_text_malformed_tab_is_400():
    r = TestClient(app).get("/text?tab=not-a-tab")
    assert r.status_code == 400
