from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def test_close_routes_tab_id_to_extension():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]
    seen = {}

    async def fake_send(bid, cmd, *a, payload=None, **k):
        seen["bid"] = bid
        seen["cmd"] = cmd
        seen["payload"] = payload if payload is not None else (a[0] if a else None)
        return {"success": True, "payload": {"closed": 1}}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post("/close", json={"tab": "br-aaaa:1"})
    assert r.status_code == 200
    assert r.json() == {"closed": 1}
    assert seen["bid"] == "br-aaaa"
    assert seen["cmd"] == "CLOSE"
    assert seen["payload"] == {"tab_id": 1}
