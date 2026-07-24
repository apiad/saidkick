"""Daemon settings.

Everything operational — auth, resource caps, logging — lives here rather than
being threaded through the engine. The engine layer never reads this: saidkick
is a library you can import as much as a service you can run, and the library
path must not require a config object.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("SAIDKICK_HOME", "~/.saidkick")).expanduser()


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _num(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw is not None else default
    except ValueError:
        return default


@dataclass
class Settings:
    #: Shared bearer token. None + require_auth ⇒ resolved from file/env at startup.
    token: str | None = None
    require_auth: bool = True

    #: Refuse to open more than this many contexts at once.
    max_contexts: int = 20
    #: Close contexts idle longer than this. 0 disables reaping.
    idle_ttl_s: float = 1800.0
    #: How often the reaper runs.
    reap_interval_s: float = 30.0

    #: Persist a run log to beaver. Off by default: it is a durable record of
    #: everything the agent did, which not every deployment wants.
    runlog: bool = False
    #: Redact typed text in the run log. Never default this to False.
    redact: bool = True

    trace_dir: Path = field(default_factory=lambda: home() / "traces")
    runlog_path: Path = field(default_factory=lambda: home() / "runlog.db")

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        base = cls(
            token=os.environ.get("SAIDKICK_TOKEN"),
            require_auth=_flag("SAIDKICK_REQUIRE_AUTH", True),
            max_contexts=int(_num("SAIDKICK_MAX_CONTEXTS", 20)),
            idle_ttl_s=_num("SAIDKICK_IDLE_TTL_S", 1800.0),
            reap_interval_s=_num("SAIDKICK_REAP_INTERVAL_S", 30.0),
            runlog=_flag("SAIDKICK_RUNLOG", False),
            redact=_flag("SAIDKICK_REDACT", True),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(base, key, value)
        return base
