import pytest
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
    manager.logs.append({"level": "log", "data": "test message", "timestamp": "2024-01-01", "url": "test"})
    response = client.get("/console")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["data"] == "test message"

@patch("saidkick.server.manager.send_command", new_callable=AsyncMock)
def test_post_execute(mock_send_command):
    mock_send_command.return_value = {"success": True, "payload": "result"}
    response = client.post("/execute", json={"code": "console.log(1)"})
    assert response.status_code == 200
    assert response.json() == "result"
    mock_send_command.assert_called_with("EXECUTE", "console.log(1)")
