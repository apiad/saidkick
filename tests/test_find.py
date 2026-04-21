from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_find_by_text_routes_to_extension():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {
            "success": True,
            "payload": [
                {"selector": "div:nth-of-type(3)", "tag": "DIV",
                 "role": "listitem", "name": "Alice Chen",
                 "text": "Alice Chen", "rect": {"x": 0, "y": 0, "w": 100, "h": 40},
                 "visible": True},
            ],
        }
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/find?tab=br-aaaa:1&by_text=Alice")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["name"] == "Alice Chen"
    assert seen["args"][1] == "FIND"
    assert seen["payload"]["by_text"] == "Alice"


def test_find_no_locator_is_400():
    setup_single_browser()
    r = TestClient(app).get("/find?tab=br-aaaa:1")
    assert r.status_code == 400
    assert "No locator" in r.json()["detail"]


def test_find_two_locators_is_400():
    setup_single_browser()
    r = TestClient(app).get("/find?tab=br-aaaa:1&css=.a&by_text=b")
    assert r.status_code == 400


def test_find_with_within_and_nth():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": []}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get(
            "/find?tab=br-aaaa:1&by_text=send&within_css=.modal&nth=1&exact=true"
        )
    assert r.status_code == 200
    assert seen["payload"]["within_css"] == ".modal"
    assert seen["payload"]["nth"] == 1
    assert seen["payload"]["exact"] is True
