import pytest
from saidkick.server import parse_tab_id


def test_parse_tab_id_valid():
    assert parse_tab_id("br-a1b2:42") == ("br-a1b2", 42)
    assert parse_tab_id("br-0000:1") == ("br-0000", 1)
    assert parse_tab_id("br-ffff:999999") == ("br-ffff", 999999)


@pytest.mark.parametrize("bad", [
    "",
    "br-a1b2",
    "br-a1b2:",
    "br-a1b2:abc",
    "br-XYZ1:42",         # non-hex chars
    "br-a1b:42",          # too few hex chars
    "br-a1b2c:42",        # too many hex chars
    "a1b2:42",            # missing br- prefix
    "br-a1b2:42:extra",   # extra segment
    "br-a1b2:-1",         # negative
])
def test_parse_tab_id_invalid_raises(bad):
    with pytest.raises(ValueError):
        parse_tab_id(bad)


import re
from saidkick.server import SaidkickManager


def test_generate_browser_id_format():
    m = SaidkickManager()
    for _ in range(100):
        bid = m.generate_browser_id()
        assert re.match(r"^br-[0-9a-f]{4}$", bid), f"bad format: {bid}"


def test_generate_browser_id_avoids_collision():
    m = SaidkickManager()
    m.connections = {"br-aaaa": object()}  # type: ignore[assignment]
    sequence = iter(["br-aaaa", "br-bbbb"])
    m._random_browser_id = lambda: next(sequence)  # type: ignore[attr-defined]
    bid = m.generate_browser_id()
    assert bid == "br-bbbb"


def test_manager_connections_is_dict():
    m = SaidkickManager()
    assert isinstance(m.connections, dict)
    assert m.connections == {}


from fastapi.testclient import TestClient
from saidkick.server import app, manager


def test_ws_handshake_sends_hello():
    client = TestClient(app)
    manager.connections.clear()
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "HELLO"
        assert re.match(r"^br-[0-9a-f]{4}$", hello["browser_id"])
        assert hello["browser_id"] in manager.connections


def test_ws_disconnect_removes_connection():
    client = TestClient(app)
    manager.connections.clear()
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        bid = hello["browser_id"]
        assert bid in manager.connections
    import time; time.sleep(0.1)
    assert bid not in manager.connections


from unittest.mock import AsyncMock, patch


def test_get_tabs_empty_when_no_browsers():
    manager.connections.clear()
    c = TestClient(app)
    r = c.get("/tabs")
    assert r.status_code == 200
    assert r.json() == []


def test_get_tabs_aggregates_across_browsers():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]
    manager.connections["br-bbbb"] = object()  # type: ignore[assignment]

    async def fake_send(browser_id, command_type, payload=None):
        assert command_type == "LIST_TABS"
        return {
            "success": True,
            "payload": [
                {"id": 1, "url": "https://a.com/", "title": "A",
                 "active": True, "windowId": 10},
                {"id": 2, "url": "https://b.com/", "title": "B",
                 "active": False, "windowId": 10},
            ],
        } if browser_id == "br-aaaa" else {
            "success": True,
            "payload": [
                {"id": 5, "url": "https://c.com/", "title": "C",
                 "active": True, "windowId": 20},
            ],
        }

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/tabs")
    assert r.status_code == 200
    data = r.json()
    tabs = {entry["tab"]: entry for entry in data}
    assert "br-aaaa:1" in tabs
    assert "br-aaaa:2" in tabs
    assert "br-bbbb:5" in tabs
    assert tabs["br-aaaa:1"]["browser_id"] == "br-aaaa"
    assert tabs["br-aaaa:1"]["tab_id"] == 1
    assert tabs["br-aaaa:1"]["url"] == "https://a.com/"
    assert tabs["br-aaaa:1"]["active"] is True


def test_get_tabs_active_filter():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    async def fake_send(browser_id, command_type, payload=None):
        return {"success": True, "payload": [
            {"id": 1, "url": "a", "title": "A", "active": True,  "windowId": 10},
            {"id": 2, "url": "b", "title": "B", "active": False, "windowId": 10},
        ]}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/tabs?active=true")
    assert r.status_code == 200
    tabs = r.json()
    assert len(tabs) == 1
    assert tabs[0]["tab"] == "br-aaaa:1"


def test_get_tabs_skips_browser_on_timeout():
    from fastapi import HTTPException
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]
    manager.connections["br-bbbb"] = object()  # type: ignore[assignment]

    async def fake_send(browser_id, command_type, payload=None):
        if browser_id == "br-aaaa":
            raise HTTPException(status_code=504, detail="Browser response timeout")
        return {"success": True, "payload": [
            {"id": 5, "url": "c", "title": "C", "active": True, "windowId": 20},
        ]}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/tabs")
    assert r.status_code == 200
    tabs = {entry["tab"]: entry for entry in r.json()}
    assert "br-bbbb:5" in tabs
    assert not any(t.startswith("br-aaaa:") for t in tabs)


def test_execute_missing_tab_is_400():
    r = TestClient(app).post("/execute", json={"code": "1+1"})
    assert r.status_code == 422  # pydantic rejects missing required field


def test_execute_malformed_tab_is_400():
    r = TestClient(app).post(
        "/execute", json={"tab": "not-a-tab", "code": "1+1"}
    )
    assert r.status_code == 400
    assert "invalid tab ID" in r.json()["detail"]


def test_execute_unknown_browser_is_404():
    manager.connections.clear()
    r = TestClient(app).post(
        "/execute", json={"tab": "br-zzzz:1", "code": "1+1"}
    )
    assert r.status_code == 400  # malformed since 'z' is not hex


def test_execute_unknown_valid_browser_is_404():
    manager.connections.clear()
    r = TestClient(app).post(
        "/execute", json={"tab": "br-abcd:1", "code": "1+1"}
    )
    assert r.status_code == 404
    assert "not connected" in r.json()["detail"]


def test_execute_routes_to_correct_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    seen = {}
    async def fake_send(browser_id, command_type, payload=None):
        seen["browser_id"] = browser_id
        seen["type"] = command_type
        seen["payload"] = payload
        return {"success": True, "payload": "ok"}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/execute", json={"tab": "br-aaaa:42", "code": "1+1"}
        )
    assert r.status_code == 200
    assert seen["browser_id"] == "br-aaaa"
    assert seen["type"] == "EXECUTE"
    assert seen["payload"]["tab_id"] == 42
    assert seen["payload"]["code"] == "1+1"


def test_dom_routes_to_correct_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    seen = {}
    async def fake_send(browser_id, command_type, payload=None):
        seen["browser_id"] = browser_id
        seen["payload"] = payload
        return {"success": True, "payload": "<div/>"}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/dom?tab=br-aaaa:7&css=.foo")
    assert r.status_code == 200
    assert seen["browser_id"] == "br-aaaa"
    assert seen["payload"]["tab_id"] == 7
    assert seen["payload"]["css"] == ".foo"


def test_console_browser_filter():
    manager.logs.clear()
    manager.logs.append({"level": "info", "data": "from A", "browser_id": "br-aaaa"})
    manager.logs.append({"level": "info", "data": "from B", "browser_id": "br-bbbb"})
    manager.logs.append({"level": "info", "data": "also A", "browser_id": "br-aaaa"})

    r = TestClient(app).get("/console?browser=br-aaaa")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert all(e["browser_id"] == "br-aaaa" for e in data)


def test_execute_element_not_found_is_404():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    async def fake_send(*args, **kwargs):
        return {"success": False, "payload": "Element not found"}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/execute", json={"tab": "br-aaaa:1", "code": "x"}
        )
    assert r.status_code == 404
    assert r.json()["detail"] == "Element not found"


def test_click_ambiguous_selector_is_400():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    async def fake_send(*args, **kwargs):
        return {"success": False, "payload": "Ambiguous selector: found 3 matches"}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/click", json={"tab": "br-aaaa:1", "css": ".btn"}
        )
    assert r.status_code == 400


def test_type_unknown_extension_error_is_502():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    async def fake_send(*args, **kwargs):
        return {"success": False, "payload": "weird chrome thing"}

    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/type", json={"tab": "br-aaaa:1", "css": "#x", "text": "y"}
        )
    assert r.status_code == 502
