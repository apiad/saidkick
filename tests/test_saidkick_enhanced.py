import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from saidkick.server import app, manager

client = TestClient(app)

def test_console_filtering():
    # Clear logs for predictability
    manager.logs.clear()
    manager.logs.append({"level": "info", "data": "Hello World"})
    manager.logs.append({"level": "error", "data": "Something failed"})
    manager.logs.append({"level": "info", "data": "Goodbye World"})

    # Test limit
    response = client.get("/console?limit=1")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["data"] == "Goodbye World"

    # Test grep
    response = client.get("/console?grep=World")
    assert response.status_code == 200
    assert len(response.json()) == 2

    # Test combined
    response = client.get("/console?grep=World&limit=1")
    assert len(response.json()) == 1
    assert response.json()[0]["data"] == "Goodbye World"

@patch("saidkick.server.manager.send_command", new_callable=AsyncMock)
def test_dom_anchoring_params(mock_send_command):
    mock_send_command.return_value = {"payload": "<div>Match</div>", "success": True}
    
    response = client.get("/dom?css=.test&all=true")
    assert response.status_code == 200
    assert response.json() == "<div>Match</div>"
    
    # Verify parameters were passed
    mock_send_command.assert_called_with("GET_DOM", payload={"css": ".test", "xpath": None, "all": True})

@patch("saidkick.server.manager.send_command", new_callable=AsyncMock)
def test_interaction_endpoints(mock_send_command):
    mock_send_command.return_value = {"success": True, "payload": "OK"}
    
    # Test Click
    response = client.post("/click", json={"css": "#btn"})
    assert response.status_code == 200
    mock_send_command.assert_called_with("CLICK", {"css": "#btn", "xpath": None})
    
    # Test Type
    response = client.post("/type", json={"css": "#input", "text": "hello", "clear": True})
    assert response.status_code == 200
    mock_send_command.assert_called_with("TYPE", {"css": "#input", "xpath": None, "text": "hello", "clear": True})
    
    # Test Select
    response = client.post("/select", json={"xpath": "//select", "value": "opt1"})
    assert response.status_code == 200
    mock_send_command.assert_called_with("SELECT", {"css": None, "xpath": "//select", "value": "opt1"})
