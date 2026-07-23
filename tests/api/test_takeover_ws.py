import pytest
from fastapi.testclient import TestClient

from saidkick.api import create_app
from saidkick.control import Controller
from saidkick.engine import Engine

pytestmark = pytest.mark.browser


@pytest.fixture
def live(fixture_url):
    """TestClient drives its own event loop, so it gets its own engine."""
    controller = Controller()
    engine = Engine(controller=controller)
    # create_app's lifespan starts the engine, so there is nothing to wire.
    with TestClient(create_app(engine, controller)) as client:
        cid = client.post("/contexts").json()["id"]
        tid = client.post(
            f"/contexts/{cid}/tabs", json={"url": f"{fixture_url}/form.html"}
        ).json()["id"]
        yield client, controller, cid, tid


def test_take_and_release_round_trip(live):
    client, controller, cid, tid = live
    with client.websocket_connect(f"/ws/control/{tid}") as ws:
        ws.send_json({"type": "take"})
        assert ws.receive_json()["state"] == "human"
        assert controller.state(cid) == "human"
        ws.send_json({"type": "release", "note": "done"})
        assert ws.receive_json()["state"] == "agent"
    assert controller.state(cid) == "agent"


def test_disconnect_releases_control(live):
    """A closed laptop lid must not leave an agent permanently locked out."""
    client, controller, cid, tid = live
    with client.websocket_connect(f"/ws/control/{tid}") as ws:
        ws.send_json({"type": "take"})
        ws.receive_json()
        assert controller.state(cid) == "human"
    assert controller.state(cid) == "agent"


def test_release_resolves_a_pending_request(live):
    client, controller, cid, tid = live
    controller.open_request(cid, "enter the 2FA code", deadline_s=60)
    with client.websocket_connect(f"/ws/control/{tid}") as ws:
        ws.send_json({"type": "take"})
        ws.receive_json()
        ws.send_json({"type": "release", "note": "code entered"})
        ws.receive_json()
    assert controller.pending(cid) is None


def test_view_socket_streams_frames(live):
    client, _, _, tid = live
    with client.websocket_connect(f"/ws/view/{tid}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "frame" and msg["data"]
        assert "deviceWidth" in msg["metadata"]


def test_pin_placed_over_the_control_socket_without_taking_control(live):
    """Pointing at something must not require a takeover.

    A rect over the top-left region encloses form content; the exact element
    does not matter here — that a pin is created without control does.
    """
    client, controller, cid, tid = live
    with client.websocket_connect(f"/ws/control/{tid}") as ws:
        ws.send_json({"type": "pin", "x": 0, "y": 0, "w": 400, "h": 300, "label": "the form"})
        reply = ws.receive_json()
    assert reply["pin"]["handle"].startswith("el_")
    assert reply["pin"]["label"] == "the form"
    assert reply["pin"]["descriptor"]["tag"]
    assert controller.state(cid) == "agent"  # never took control
    assert len(client.app.state.pins.list(tid)) == 1


def test_input_before_take_is_refused(live):
    """Forwarding input while the agent holds control is a violation."""
    client, controller, cid, tid = live
    with client.websocket_connect(f"/ws/control/{tid}") as ws:
        ws.send_json({"type": "paste", "text": "nope", "metadata": {}})
        # The socket closes rather than silently applying the input.
        with pytest.raises(Exception):
            ws.receive_json()
    assert controller.state(cid) == "agent"
