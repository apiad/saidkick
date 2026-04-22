from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from saidkick.server import app, manager

client = TestClient(app)


def test_console_filtering():
    manager.logs.clear()
    manager.logs.append({"level": "info", "data": "Hello World", "browser_id": "br-aaaa"})
    manager.logs.append({"level": "error", "data": "Something failed", "browser_id": "br-aaaa"})
    manager.logs.append({"level": "info", "data": "Goodbye World", "browser_id": "br-bbbb"})

    response = client.get("/console?limit=1")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["data"] == "Goodbye World"

    response = client.get("/console?grep=World")
    assert response.status_code == 200
    assert len(response.json()) == 2

    response = client.get("/console?grep=World&limit=1")
    assert len(response.json()) == 1
    assert response.json()[0]["data"] == "Goodbye World"


def test_dom_anchoring_params():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    with patch.object(manager, "send_command", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"payload": "<div>Match</div>", "success": True}
        response = client.get("/dom?tab=br-aaaa:3&css=.test&all=true")
    assert response.status_code == 200
    assert response.json() == "<div>Match</div>"
    mock_send.assert_called_with(
        "br-aaaa", "GET_DOM",
        payload={
            "tab_id": 3, "wait_ms": 0, "all": True,
            "css": ".test", "xpath": None,
            "by_text": None, "by_label": None, "by_placeholder": None,
            "by_role": None, "within_css": None, "nth": None, "exact": False, "regex": False, "pierce_shadow": False,
        },
        timeout=10.0,
    )


def test_interaction_endpoints():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]

    with patch.object(manager, "send_command", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"success": True, "payload": "OK"}

        response = client.post("/click", json={"tab": "br-aaaa:1", "css": "#btn"})
        assert response.status_code == 200
        mock_send.assert_called_with(
            "br-aaaa", "CLICK",
            payload={
                "tab_id": 1, "wait_ms": 0,
                "css": "#btn", "xpath": None,
                "by_text": None, "by_label": None, "by_placeholder": None,
                "by_role": None, "within_css": None, "nth": None, "exact": False, "regex": False, "pierce_shadow": False,
            },
            timeout=10.0,
        )

        response = client.post("/type", json={
            "tab": "br-aaaa:2", "css": "#input", "text": "hello", "clear": True,
        })
        assert response.status_code == 200
        mock_send.assert_called_with(
            "br-aaaa", "TYPE",
            payload={
                "tab_id": 2, "wait_ms": 0,
                "text": "hello", "clear": True,
                "css": "#input", "xpath": None,
                "by_text": None, "by_label": None, "by_placeholder": None,
                "by_role": None, "within_css": None, "nth": None, "exact": False, "regex": False, "pierce_shadow": False,
            },
            timeout=10.0,
        )

        response = client.post("/select", json={
            "tab": "br-aaaa:3", "xpath": "//select", "value": "opt1",
        })
        assert response.status_code == 200
        mock_send.assert_called_with(
            "br-aaaa", "SELECT",
            payload={
                "tab_id": 3, "wait_ms": 0, "value": "opt1",
                "css": None, "xpath": "//select",
                "by_text": None, "by_label": None, "by_placeholder": None,
                "by_role": None, "within_css": None, "nth": None, "exact": False, "regex": False, "pierce_shadow": False,
            },
            timeout=10.0,
        )
