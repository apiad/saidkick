import base64
from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


SAMPLE_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_screenshot_viewport():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["args"] = args
        seen["payload"] = kwargs.get("payload") or args[2]
        return {
            "success": True,
            "payload": {"png_base64": SAMPLE_PNG_B64, "width": 1920, "height": 1080},
        }
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/screenshot?tab=br-aaaa:1")
    assert r.status_code == 200
    body = r.json()
    assert body["png_base64"] == SAMPLE_PNG_B64
    assert body["width"] == 1920
    assert seen["args"][1] == "SCREENSHOT"
    assert seen["payload"]["full_page"] is False
    assert seen["payload"]["css"] is None


def test_screenshot_with_full_page_and_clip():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {
            "success": True,
            "payload": {"png_base64": SAMPLE_PNG_B64, "width": 400, "height": 300},
        }
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get(
            "/screenshot?tab=br-aaaa:1&by_text=Article&full_page=true"
        )
    assert r.status_code == 200
    assert seen["payload"]["full_page"] is True
    assert seen["payload"]["by_text"] == "Article"


def test_screenshot_two_locators_is_400():
    setup_single_browser()
    r = TestClient(app).get(
        "/screenshot?tab=br-aaaa:1&css=.a&by_text=b"
    )
    assert r.status_code == 400


def test_screenshot_element_not_found_is_404():
    setup_single_browser()
    async def fake_send(*args, **kwargs):
        return {"success": False, "payload": "element not found"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/screenshot?tab=br-aaaa:1&css=.nope")
    assert r.status_code == 404
