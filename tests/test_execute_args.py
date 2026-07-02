from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def _single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_execute_forwards_args_to_extension():
    _single_browser()
    seen = {}

    async def fake_send(bid, cmd, *a, payload=None, **k):
        seen["cmd"] = cmd
        seen["payload"] = payload if payload is not None else (a[0] if a else None)
        return {"success": True, "payload": "ok"}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/execute",
            json={"tab": "br-aaaa:1", "code": "return arguments[0]", "args": ["hello", 42]},
        )
    assert r.status_code == 200
    assert seen["cmd"] == "EXECUTE"
    assert seen["payload"]["args"] == ["hello", 42]
    assert seen["payload"]["code"] == "return arguments[0]"


def test_execute_args_defaults_to_empty():
    _single_browser()
    seen = {}

    async def fake_send(bid, cmd, *a, payload=None, **k):
        seen["payload"] = payload if payload is not None else (a[0] if a else None)
        return {"success": True, "payload": None}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/execute",
            json={"tab": "br-aaaa:1", "code": "return 1"},
        )
    assert r.status_code == 200
    assert seen["payload"]["args"] == []
