"""Durable run log: what the agent did, and what happened.

Answers "why did the agent do that" after the fact, which the in-memory event
bus cannot once the daemon restarts.

**Redaction is a requirement, not a feature.** Actions carry the text an agent
typed, and that text is routinely a password, a 2FA code, or an API key. A run
log that stored it verbatim would be a credential store nobody asked for. So
text is replaced with a length and a short hash by default, which is enough to
tell two values apart and to confirm something was entered, and useless to
anyone who steals the file.

The sink is optional. ``RunLog(None)`` is a no-op, so the library path never
requires beaver.
"""

import hashlib
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("saidkick.runlog")

#: Fields whose values are secrets often enough that they are always redacted.
SENSITIVE = ("text", "value", "password", "token")


def redact_value(value: Any) -> dict:
    """Replace a value with something identifying but not reusable."""
    text = value if isinstance(value, str) else str(value)
    digest = hashlib.sha256(text.encode()).hexdigest()[:12]
    return {"len": len(text), "sha256": digest}


class RunLog:
    def __init__(self, db_path: Path | None = None, redact: bool = True):
        self.redact = redact
        self._log = None
        self._db = None
        # Writing to a closed BeaverDB BLOCKS rather than raising, so a
        # try/except around the write is not enough — an action logging during
        # shutdown would hang the browser call. The flag is the guard.
        self._closed = False
        if db_path is None:
            return
        try:
            from beaver import BeaverDB  # noqa: PLC0415 - optional dependency

            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._db = BeaverDB(str(db_path))
            self._log = self._db.log("runs")
        except Exception as exc:  # noqa: BLE001 - logging must never break the daemon
            log.warning("run log disabled: %s", exc)

    @property
    def enabled(self) -> bool:
        return self._log is not None and not self._closed

    def _clean(self, fields: dict) -> dict:
        if not self.redact:
            return fields
        out = {}
        for key, value in fields.items():
            out[key] = redact_value(value) if key in SENSITIVE and value is not None else value
        return out

    def record(self, kind: str, ctx: str | None = None, **fields: Any) -> None:
        if not self.enabled:
            return
        try:
            self._log.log({"kind": kind, "ctx": ctx, **self._clean(fields)})
        except Exception as exc:  # noqa: BLE001
            log.warning("run log write failed: %s", exc)

    def query(self, ctx: str | None = None, limit: int = 100) -> list[dict]:
        if not self.enabled:
            return []
        rows = self._log.range(limit=limit)
        out = [{"ts": r.timestamp, **r.data} for r in rows]
        if ctx:
            out = [r for r in out if r.get("ctx") == ctx]
        return out

    def count(self) -> int:
        return self._log.count() if self.enabled else 0

    def close(self) -> None:
        self._closed = True
        if self._db is not None:
            try:
                self._db.close()
            except Exception as exc:  # noqa: BLE001
                log.debug("run log close failed: %s", exc)


#: A shared no-op sink so callers never need a None check.
NULL_RUNLOG = RunLog(None)


def timed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
