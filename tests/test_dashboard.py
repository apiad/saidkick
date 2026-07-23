import pytest

from saidkick.control import Controller
from saidkick.dashboard import snapshot_text


class FakeEngine:
    """The dashboard must render without a browser, so it takes data, not a driver."""

    def __init__(self, contexts=None):
        self._contexts = contexts or []

    def list_contexts(self):
        return self._contexts


@pytest.fixture
def fake_engine():
    return FakeEngine(
        [{"id": "ctx_a", "controller": "agent", "tabs": [{"id": "ctx_a:1", "url": "http://x/"}]}]
    )


def test_pending_request_is_shown_with_reason(fake_engine):
    c = Controller()
    c.open_request("ctx_a", "enter the 2FA code", deadline_s=600)
    out = snapshot_text(fake_engine, c)
    assert "enter the 2FA code" in out
    assert "ctx_a" in out
    assert "NEEDS YOU" in out


def test_pending_section_absent_when_nothing_pending(fake_engine):
    assert "NEEDS YOU" not in snapshot_text(fake_engine, Controller())


def test_controller_state_is_visible(fake_engine):
    c = Controller()
    c.take("ctx_a")
    fake_engine._contexts[0]["controller"] = c.state("ctx_a")
    assert "human" in snapshot_text(fake_engine, c)


def test_cockpit_url_shown_for_pending_request(fake_engine):
    c = Controller()
    c.open_request("ctx_a", "help", deadline_s=600)
    assert "/session/ctx_a" in snapshot_text(fake_engine, c)


def test_renders_with_no_contexts_at_all():
    assert "no contexts" in snapshot_text(FakeEngine([]), Controller())


def test_multiple_pending_requests_all_shown(fake_engine):
    c = Controller()
    c.open_request("ctx_a", "first reason", deadline_s=600)
    c.open_request("ctx_b", "second reason", deadline_s=600)
    out = snapshot_text(fake_engine, c)
    assert "first reason" in out and "second reason" in out
