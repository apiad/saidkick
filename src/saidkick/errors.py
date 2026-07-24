"""The closed set of errors saidkick raises, and their HTTP mapping.

Every error an agent can encounter is in this module. Anything else reaching
the API layer is a daemon bug and surfaces as a 500.

For an agent-facing API this matters more than for a human-facing one: the
agent's recovery behaviour is entirely determined by whether the error tells it
what to do next. So each class carries a stable ``code`` (what the agent
branches on) alongside a human-readable detail, and optional structured extras
such as the candidate list on an ambiguous locator.
"""

from typing import Any

ALL_ERRORS: list[type["SaidkickError"]] = []


class SaidkickError(Exception):
    """Base class. ``code`` is derived from the class name so the two cannot drift."""

    status: int = 500
    code: str = "SaidkickError"

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.code = cls.__name__
        ALL_ERRORS.append(cls)

    def __init__(self, detail: str = "", **extra: Any):
        super().__init__(detail)
        self.detail = detail
        # A fresh dict per instance. A shared class-level default would leak
        # one call's candidates into the next call's error.
        self.extra: dict[str, Any] = dict(extra)


class ProfileLocked(SaidkickError):
    """An attached context is already live on that profile.

    Chromium takes an exclusive lock on a user-data-dir, so this is a hard
    constraint of the engine rather than a policy choice.
    """

    status = 409


class NoSuchContext(SaidkickError):
    """Unknown or already-closed context."""

    status = 404


class NoSuchTab(SaidkickError):
    """Unknown or already-closed tab."""

    status = 404


class StaleHandle(SaidkickError):
    """An element handle was invalidated, usually by navigation.

    Recovery: fall back to the selectors carried alongside the handle.
    """

    status = 410


class LocatorNotFound(SaidkickError):
    """No element matched the locator."""

    status = 404


class LocatorAmbiguous(SaidkickError):
    """More than one element matched. Carries ``candidates`` so the agent can
    retry with ``nth`` or a narrower locator instead of guessing."""

    status = 400


class NavigationFailed(SaidkickError):
    """Navigation did not complete."""

    status = 502


class HumanHoldsControl(SaidkickError):
    """A human currently holds the wheel on this context.

    Raised rather than queued on purpose: an agent silently blocking in a queue
    has no model of what is happening to it, whereas an error it can read is
    something it can reason about.
    """

    status = 409


class HumanTimeout(SaidkickError):
    """A human request reached its deadline with no response."""

    status = 504


class DialogBlocked(SaidkickError):
    """A native dialog is open and the context policy is ``ask_human``."""

    status = 409


class EngineCrashed(SaidkickError):
    """Chromium died; the context is no longer usable."""

    status = 502


class TooManyContexts(SaidkickError):
    """The context cap is reached.

    Distinct from every other error because the agent's recovery is specific:
    close a context it is finished with, then retry.
    """

    status = 429


def http_detail(exc: SaidkickError) -> dict[str, Any]:
    """Serialise an error into the JSON body the API returns."""
    return {"error": exc.code, "detail": exc.detail, **exc.extra}
