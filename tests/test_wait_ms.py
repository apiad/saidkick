from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_dom_passes_wait_ms_to_extension():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return {"success": True, "payload": "<div/>"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/dom?tab=br-aaaa:1&css=.x&wait_ms=3000")
    assert r.status_code == 200
    payload = seen["kwargs"].get("payload") or (seen["args"][2] if len(seen["args"]) >= 3 else None)
    assert payload["wait_ms"] == 3000


def test_click_passes_wait_ms_to_extension():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return {"success": True, "payload": "Clicked"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/click", json={"tab": "br-aaaa:1", "css": ".btn", "wait_ms": 2500}
        )
    assert r.status_code == 200
    payload = seen["kwargs"].get("payload") or (seen["args"][2] if len(seen["args"]) >= 3 else None)
    assert payload["wait_ms"] == 2500


def test_wait_ms_defaults_to_zero():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return {"success": True, "payload": "Clicked"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/click", json={"tab": "br-aaaa:1", "css": ".btn"}
        )
    assert r.status_code == 200
    payload = seen["kwargs"].get("payload") or (seen["args"][2] if len(seen["args"]) >= 3 else None)
    assert payload["wait_ms"] == 0


from saidkick.server import _command_timeout


def test_command_timeout_defaults_to_ten():
    assert _command_timeout() == 10.0


def test_command_timeout_grows_with_wait_ms():
    assert _command_timeout(wait_ms=5000) >= 7.0


def test_command_timeout_grows_with_timeout_ms():
    assert _command_timeout(timeout_ms=20000) >= 22.0


def test_command_timeout_uses_max_of_inputs():
    assert _command_timeout(wait_ms=5000, timeout_ms=15000) >= 17.0
