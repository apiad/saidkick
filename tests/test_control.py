import asyncio

import pytest

from saidkick import errors as E
from saidkick import notify
from saidkick.control import Controller


def test_default_state_is_agent():
    assert Controller().state("ctx_a") == "agent"


def test_agent_blocked_while_human_holds():
    c = Controller()
    c.take("ctx_a")
    with pytest.raises(E.HumanHoldsControl):
        c.assert_agent_may_act("ctx_a")


def test_other_contexts_unaffected():
    c = Controller()
    c.take("ctx_a")
    c.assert_agent_may_act("ctx_b")


def test_release_restores_agent():
    c = Controller()
    c.take("ctx_a")
    c.release("ctx_a")
    c.assert_agent_may_act("ctx_a")


def test_human_input_refused_when_agent_holds():
    c = Controller()
    with pytest.raises(E.HumanHoldsControl):
        c.assert_human_may_act("ctx_a")


async def test_request_resolved_by_release_carries_note():
    c = Controller()
    task = asyncio.create_task(c.request_human("ctx_a", "2FA", deadline_s=5, poll_s=5))
    await asyncio.sleep(0.05)
    c.take("ctx_a")
    c.release("ctx_a", note="logged in")
    out = await task
    assert out["status"] == "resolved" and out["note"] == "logged in"


async def test_poll_returns_still_waiting_without_closing_request():
    c = Controller()
    out = await c.request_human("ctx_a", "2FA", deadline_s=5, poll_s=0.1)
    assert out["status"] == "still_waiting"
    assert c.pending("ctx_a") is not None


async def test_deadline_closes_request_as_timeout():
    c = Controller()
    out = await c.request_human("ctx_a", "2FA", deadline_s=0.1, poll_s=5)
    assert out["status"] == "timeout"
    assert c.pending("ctx_a") is None


async def test_second_request_rejoins_the_first():
    """Re-calling must not open a duplicate card in the cockpit."""
    c = Controller()
    t1 = asyncio.create_task(c.request_human("ctx_a", "2FA", deadline_s=5, poll_s=5))
    await asyncio.sleep(0.05)
    t2 = asyncio.create_task(c.request_human("ctx_a", "2FA again", deadline_s=5, poll_s=5))
    await asyncio.sleep(0.05)
    assert len(c.list_pending()) == 1
    c.take("ctx_a")
    c.release("ctx_a", note="done")
    r1, r2 = await t1, await t2
    assert r1["status"] == r2["status"] == "resolved"
    assert r1["request_id"] == r2["request_id"]


async def test_timeout_does_not_change_control_state():
    """A timed-out request must not touch the browser or the controller."""
    c = Controller()
    await c.request_human("ctx_a", "2FA", deadline_s=0.1, poll_s=5)
    assert c.state("ctx_a") == "agent"


async def test_request_carries_a_relayable_human_message():
    c = Controller(cockpit_base="http://localhost:6992")
    out = await c.request_human("ctx_a", "enter the 2FA code", deadline_s=0.1, poll_s=0.05)
    assert "enter the 2FA code" in out["human_message"]
    assert "http://localhost:6992/session/ctx_a" == out["cockpit_url"]


async def test_webhook_is_off_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "fire_and_forget", lambda *a: calls.append(a))
    c = Controller(webhook_url=None)
    await c.request_human("ctx_a", "x", deadline_s=0.1, poll_s=0.05)
    assert calls == []


async def test_webhook_payload_is_unbranded(monkeypatch):
    """No vendor-specific fields; a user wires this to whatever they use."""
    seen = {}
    monkeypatch.setattr(notify, "fire_and_forget", lambda url, payload: seen.update(payload))
    c = Controller(webhook_url="http://example.invalid/hook")
    await c.request_human("ctx_a", "x", deadline_s=0.1, poll_s=0.05)
    assert set(seen) == {"context", "reason", "url", "deadline"}


async def test_webhook_failure_does_not_break_the_rescue():
    """A dead webhook must never stop a human request from working."""
    c = Controller(webhook_url="http://127.0.0.1:1/dead")
    out = await c.request_human("ctx_a", "x", deadline_s=0.2, poll_s=0.05)
    assert out["status"] == "still_waiting"
