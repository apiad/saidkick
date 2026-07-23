"""Control arbitration and the human-in-the-loop request registry.

Every context has exactly one controller: ``agent``, ``human``, or ``none``.
All input — the agent's and the human's — routes through this one point, so
there is no state in which both can act.

There is no browser here. This whole module is a state machine, which is why
its tests run without Chromium.
"""

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from . import errors as E
from . import notify

AGENT = "agent"
HUMAN = "human"

DEFAULT_DEADLINE_S = 600.0
DEFAULT_POLL_S = 120.0


@dataclass
class HumanRequest:
    """An open ask for human help on one context."""

    id: str
    ctx: str
    reason: str
    opened_at: float
    deadline_at: float
    done: asyncio.Event = field(default_factory=asyncio.Event)
    note: str | None = None

    def remaining(self) -> float:
        return max(0.0, self.deadline_at - time.monotonic())

    def elapsed(self) -> float:
        return time.monotonic() - self.opened_at

    def info(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "context": self.ctx,
            "reason": self.reason,
            "elapsed_s": round(self.elapsed(), 1),
            "remaining_s": round(self.remaining(), 1),
        }


class Controller:
    def __init__(
        self,
        cockpit_base: str = "http://localhost:6992",
        webhook_url: str | None = None,
    ):
        self.cockpit_base = cockpit_base.rstrip("/")
        self.webhook_url = webhook_url
        self._state: dict[str, str] = {}
        self._pending: dict[str, HumanRequest] = {}

    # -- control state --------------------------------------------------

    def state(self, ctx_id: str) -> str:
        return self._state.get(ctx_id, AGENT)

    def take(self, ctx_id: str, who: str = HUMAN) -> str:
        self._state[ctx_id] = who
        return self.state(ctx_id)

    def release(self, ctx_id: str, note: str | None = None) -> str:
        self._state[ctx_id] = AGENT
        req = self._pending.get(ctx_id)
        if req is not None:
            req.note = note
            self._close(req)
            req.done.set()
        return self.state(ctx_id)

    def assert_agent_may_act(self, ctx_id: str) -> None:
        if self.state(ctx_id) == HUMAN:
            raise E.HumanHoldsControl(
                f"a human holds control of {ctx_id}; retry after they release"
            )

    def assert_human_may_act(self, ctx_id: str) -> None:
        if self.state(ctx_id) != HUMAN:
            raise E.HumanHoldsControl(
                f"the human does not hold control of {ctx_id}; take control first"
            )

    # -- human requests -------------------------------------------------

    def cockpit_url(self, ctx_id: str) -> str:
        return f"{self.cockpit_base}/session/{ctx_id}"

    def pending(self, ctx_id: str) -> HumanRequest | None:
        return self._pending.get(ctx_id)

    def list_pending(self) -> list[HumanRequest]:
        return list(self._pending.values())

    def open_request(
        self, ctx_id: str, reason: str, deadline_s: float = DEFAULT_DEADLINE_S
    ) -> HumanRequest:
        """Create the request, or rejoin the one already open on this context.

        Rejoining matters: an agent that loops on ``still_waiting`` would
        otherwise open a fresh card in the cockpit on every poll.
        """
        existing = self._pending.get(ctx_id)
        if existing is not None:
            return existing

        now = time.monotonic()
        req = HumanRequest(
            id=f"req_{secrets.token_hex(3)}",
            ctx=ctx_id,
            reason=reason,
            opened_at=now,
            deadline_at=now + deadline_s,
        )
        self._pending[ctx_id] = req
        if self.webhook_url:
            notify.fire_and_forget(
                self.webhook_url,
                {
                    "context": ctx_id,
                    "reason": reason,
                    "url": self.cockpit_url(ctx_id),
                    "deadline": round(deadline_s, 1),
                },
            )
        return req

    def _close(self, req: HumanRequest) -> None:
        if self._pending.get(req.ctx) is req:
            del self._pending[req.ctx]

    def _result(self, req: HumanRequest, status: str) -> dict[str, Any]:
        return {
            "status": status,
            "note": req.note,
            "request_id": req.id,
            "context": req.ctx,
            "reason": req.reason,
            "cockpit_url": self.cockpit_url(req.ctx),
            "human_message": (
                f"saidkick needs you on context {req.ctx}: {req.reason}. "
                f"Take over at {self.cockpit_url(req.ctx)}"
            ),
        }

    async def request_human(
        self,
        ctx_id: str,
        reason: str,
        deadline_s: float = DEFAULT_DEADLINE_S,
        poll_s: float = DEFAULT_POLL_S,
    ) -> dict[str, Any]:
        """Ask for human help.

        ``deadline_s`` is how long the request stays open for a human to answer.
        ``poll_s`` is how long *this call* blocks before returning. They are
        independent: an agent that wants to wait the full deadline loops on
        ``still_waiting``. The poll bound exists because an unbounded blocking
        tool call would trip harness timeouts in every agent runtime.
        """
        req = self.open_request(ctx_id, reason, deadline_s=deadline_s)

        wait_for = min(poll_s, req.remaining())
        try:
            await asyncio.wait_for(req.done.wait(), timeout=wait_for)
            return self._result(req, "resolved")
        except asyncio.TimeoutError:
            if req.remaining() <= 0:
                # Deadline elapsed. Close the request but leave the control
                # state and the browser untouched: reaping a half-finished
                # login would destroy the state the human was about to rescue.
                self._close(req)
                return self._result(req, "timeout")
            return self._result(req, "still_waiting")
