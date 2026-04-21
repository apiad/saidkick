from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_press_enter_no_target():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"pressed": "Enter"}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/press", json={"tab": "br-aaaa:1", "key": "Enter"}
        )
    assert r.status_code == 200
    assert r.json() == {"pressed": "Enter"}
    assert seen["args"][1] == "PRESS"
    assert seen["payload"]["key"] == "Enter"
    assert seen["payload"]["modifiers"] == []
    assert seen["payload"]["css"] is None


def test_press_with_modifiers_and_locator():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": {"pressed": "k"}}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/press",
            json={
                "tab": "br-aaaa:1", "key": "k",
                "modifiers": ["ctrl", "shift"],
                "by_label": "Search",
            },
        )
    assert r.status_code == 200
    assert seen["payload"]["modifiers"] == ["ctrl", "shift"]
    assert seen["payload"]["by_label"] == "Search"


def test_press_bad_modifier_is_400():
    setup_single_browser()
    r = TestClient(app).post(
        "/press", json={"tab": "br-aaaa:1", "key": "a", "modifiers": ["hyper"]}
    )
    assert r.status_code == 400
    assert "unknown modifier" in r.json()["detail"].lower()


def test_press_missing_key_is_422():
    setup_single_browser()
    r = TestClient(app).post("/press", json={"tab": "br-aaaa:1"})
    assert r.status_code == 422


def test_press_two_locators_is_400():
    setup_single_browser()
    r = TestClient(app).post(
        "/press",
        json={"tab": "br-aaaa:1", "key": "a",
              "css": ".a", "by_text": "b"},
    )
    assert r.status_code == 400
