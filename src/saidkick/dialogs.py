"""Native dialog handling.

**The bug this fixes.** Playwright auto-dismisses `alert`/`confirm`/`prompt`
with no signal. So an agent that clicks a button firing `confirm("Delete?")`
sees the click succeed while the page took the *cancel* branch. The action
reports success, the outcome is wrong, and nothing anywhere says so. That is
worse than an error.

So every dialog is **recorded and emitted**, in all three policies. The policy
only decides what answer the dialog gets:

- ``auto_dismiss`` (default) — cancel it, but say so.
- ``auto_accept`` — accept it, and say so.
- ``ask_human`` — leave it open, open a human request, and refuse agent actions
  on that context with ``DialogBlocked`` until a person decides.
"""

import logging
import time
from typing import TYPE_CHECKING

from . import errors as E

if TYPE_CHECKING:  # pragma: no cover
    from .engine import ManagedTab

log = logging.getLogger("saidkick.dialogs")

AUTO_DISMISS = "auto_dismiss"
AUTO_ACCEPT = "auto_accept"
ASK_HUMAN = "ask_human"
POLICIES = (AUTO_DISMISS, AUTO_ACCEPT, ASK_HUMAN)

MAX_RECORDS = 50


def install_dialog_handler(tab: "ManagedTab") -> None:
    """Attach the policy-aware handler to a page. Called for every new tab."""

    def _on_dialog(dialog) -> None:
        ctx = tab.context
        policy = ctx.dialog_policy
        record = {
            "type": dialog.type,
            "message": dialog.message,
            "default_value": dialog.default_value,
            "policy": policy,
            "ts": time.time(),
        }

        if policy == ASK_HUMAN:
            # Hold the dialog open. The agent is blocked until a human answers,
            # which is the whole point of this policy.
            record["action"] = "pending"
            tab._pending_dialog = dialog
            ctx.touch()
            if ctx.controller is not None:
                ctx.controller.open_request(
                    ctx.id, f"a page dialog needs an answer: {dialog.message}"
                )
        else:
            accept = policy == AUTO_ACCEPT
            record["action"] = "accepted" if accept else "dismissed"
            tab.schedule(dialog.accept() if accept else dialog.dismiss())

        tab.record_dialog(record)
        log.info("dialog on %s: %s (%s)", tab.id, dialog.message, record["action"])

    tab.page.on("dialog", _on_dialog)


def assert_no_pending_dialog(tab: "ManagedTab") -> None:
    """Refuse to act while a dialog is waiting on a human."""
    if getattr(tab, "_pending_dialog", None) is not None:
        raise E.DialogBlocked(
            f"a dialog is waiting for a human on {tab.id}: "
            f"{tab._pending_dialog.message!r}"
        )


async def resolve_dialog(tab: "ManagedTab", accept: bool, text: str | None = None) -> dict:
    """Answer a dialog held open by the ask_human policy."""
    dialog = getattr(tab, "_pending_dialog", None)
    if dialog is None:
        raise E.DialogBlocked(f"no dialog is pending on {tab.id}")
    tab._pending_dialog = None
    if accept:
        await dialog.accept(text) if text is not None else await dialog.accept()
    else:
        await dialog.dismiss()
    action = "accepted" if accept else "dismissed"
    if tab.dialogs:
        tab.dialogs[-1]["action"] = action
    ctx = tab.context
    if ctx.controller is not None and ctx.controller.pending(ctx.id) is not None:
        ctx.controller.release(ctx.id, note=f"dialog {action}")
    return {"ok": True, "action": action}
