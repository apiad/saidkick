from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from saidkick.server import app, manager

client = TestClient(app)


def test_get_console_empty():
    manager.logs.clear()
    response = client.get("/console")
    assert response.status_code == 200
    assert response.json() == []


def test_get_console_with_logs():
    manager.logs.clear()
    manager.logs.append({
        "level": "log", "data": "test message",
        "timestamp": "2024-01-01", "url": "test",
        "browser_id": "br-aaaa",
    })
    response = client.get("/console")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["data"] == "test message"


def test_post_execute_routes_through_send_command():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    with patch.object(manager, "send_command", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"success": True, "payload": "result"}
        response = client.post(
            "/execute", json={"tab": "br-aaaa:1", "code": "console.log(1)"}
        )
    assert response.status_code == 200
    assert response.json() == "result"
    mock_send.assert_called_with(
        "br-aaaa", "EXECUTE", payload={"tab_id": 1, "code": "console.log(1)"}
    )
