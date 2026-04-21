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
