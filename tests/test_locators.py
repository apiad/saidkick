import pytest
from fastapi import HTTPException
from saidkick.server import Locator, _validate_locator, _validate_required_locator


def _loc(**kw):
    return Locator(**kw)


def test_required_locator_zero_set_raises_400():
    with pytest.raises(HTTPException) as exc:
        _validate_required_locator(_loc())
    assert exc.value.status_code == 400
    assert "No locator" in exc.value.detail


def test_required_locator_two_set_raises_400():
    with pytest.raises(HTTPException) as exc:
        _validate_required_locator(_loc(css=".a", by_text="b"))
    assert exc.value.status_code == 400
    assert "Ambiguous locator options" in exc.value.detail


@pytest.mark.parametrize("kw", [
    {"css": ".a"},
    {"xpath": "//div"},
    {"by_text": "hi"},
    {"by_label": "hi"},
    {"by_placeholder": "hi"},
])
def test_required_locator_exactly_one_passes(kw):
    _validate_required_locator(_loc(**kw))


def test_optional_locator_zero_set_is_fine():
    _validate_locator(_loc())


def test_exact_and_regex_mutex():
    with pytest.raises(HTTPException) as exc:
        _validate_locator(_loc(by_text="x", exact=True, regex=True))
    assert exc.value.status_code == 400
    assert "mutually exclusive" in exc.value.detail


from fastapi.testclient import TestClient
from unittest.mock import patch
from saidkick.server import app, manager


def setup_single_browser():
    manager.connections.clear()
    manager.connections["br-aaaa"] = object()  # type: ignore[assignment]


def test_click_by_text_propagates_to_extension():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": "Clicked"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).post(
            "/click", json={"tab": "br-aaaa:1", "by_text": "Send"}
        )
    assert r.status_code == 200
    assert seen["payload"]["by_text"] == "Send"
    assert seen["payload"]["css"] is None
    assert seen["payload"]["exact"] is False


def test_click_no_locator_is_400():
    setup_single_browser()
    r = TestClient(app).post("/click", json={"tab": "br-aaaa:1"})
    assert r.status_code == 400
    assert "No locator" in r.json()["detail"]


def test_click_two_locators_is_400():
    setup_single_browser()
    r = TestClient(app).post(
        "/click", json={"tab": "br-aaaa:1", "css": ".a", "by_text": "b"},
    )
    assert r.status_code == 400
    assert "Ambiguous locator options" in r.json()["detail"]


def test_dom_by_label_query_string():
    setup_single_browser()
    seen = {}
    async def fake_send(*args, **kwargs):
        seen["payload"] = kwargs.get("payload") or args[2]
        return {"success": True, "payload": "<div/>"}
    with patch.object(manager, "send_command", side_effect=fake_send):
        r = TestClient(app).get("/dom?tab=br-aaaa:1&by_label=Username")
    assert r.status_code == 200
    assert seen["payload"]["by_label"] == "Username"
