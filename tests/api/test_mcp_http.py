"""The MCP surface over real HTTP.

test_mcp.py calls tools directly, which cannot catch mounting mistakes. This
file speaks JSON-RPC to the mounted endpoint, which is how an agent reaches it.
"""

import json

import pytest
from fastapi.testclient import TestClient

from saidkick.api import create_app
from saidkick.control import Controller
from saidkick.engine import Engine
from saidkick.mcp_server import build_mcp

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}


def _parse_sse(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError(f"no SSE data frame in: {text!r}")


@pytest.fixture
def client():
    controller = Controller()
    engine = Engine(controller=controller)
    mcp = build_mcp(engine, controller)
    # base_url matters. The MCP SDK enables DNS-rebinding protection by
    # default and allows 127.0.0.1:*, localhost:* and [::1]:* — all WITH a
    # port. TestClient's default "testserver" is rejected, and so is a bare
    # "localhost" with no port.
    with TestClient(
        create_app(engine, controller, mcp=mcp), base_url="http://localhost:6992"
    ) as c:
        yield c


def test_mcp_is_mounted_at_slash_mcp(client):
    """Guards the double-prefix bug: the endpoint must be /mcp, not /mcp/mcp."""
    r = client.post("/mcp/", headers=HEADERS, json=INIT)
    assert r.status_code == 200, r.text
    assert _parse_sse(r.text)["result"]["serverInfo"]["name"] == "saidkick"


def test_mcp_double_prefix_is_not_served(client):
    assert client.post("/mcp/mcp", headers=HEADERS, json=INIT).status_code == 404


def test_unexpected_host_header_is_rejected(client):
    """DNS-rebinding protection: a page on another origin cannot drive MCP.

    Consequence worth knowing: reaching saidkick through a reverse proxy on a
    real hostname needs allowed_hosts widened explicitly.
    """
    r = client.post("/mcp/", headers={**HEADERS, "Host": "evil.example"}, json=INIT)
    assert r.status_code == 421
