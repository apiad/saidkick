from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def test_doctor_no_browsers():
    manager.connections.clear()
    r = TestClient(app).get("/doctor")
    assert r.status_code == 200
    body = r.json()
    assert body["server"] == "up"
    assert body["state"] == "no-browsers"
    assert body["browsers"] == []
    assert "start" in body["hint"].lower() or "reconnect" in body["hint"].lower()


def test_doctor_connected_surfaces_browser_even_with_zero_tabs():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    async def fake_send(bid, cmd, *a, **k):
        assert cmd == "LIST_TABS"
        return {"success": True, "payload": []}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/doctor")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "connected"
    assert body["browsers"] == [{"id": "br-aaaa", "tabs": 0}]
