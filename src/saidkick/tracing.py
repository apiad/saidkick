"""Playwright trace capture.

A trace is a step-by-step recording — DOM snapshots, screenshots, network,
console — viewable in Playwright's own trace viewer. It is the strongest
debugging artefact available and costs one call at each end, so it is worth
having even though nothing else depends on it.

Traces are files, not database rows: they are large and binary, and Playwright
already owns the format.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .engine import ManagedContext

log = logging.getLogger("saidkick.tracing")


class TraceManager:
    def __init__(self, trace_dir: Path):
        self.trace_dir = Path(trace_dir)
        self._active: set[str] = set()

    def path_for(self, ctx_id: str) -> Path:
        return self.trace_dir / f"{ctx_id}.zip"

    def is_tracing(self, ctx_id: str) -> bool:
        return ctx_id in self._active

    async def start(self, ctx: "ManagedContext") -> dict:
        if ctx.id in self._active:
            # A caller-state mistake, not a domain condition an agent branches on,
            # so it stays out of the closed error set and maps to 400.
            raise ValueError(f"a trace is already running on {ctx.id}")
        await ctx.pw_context.tracing.start(screenshots=True, snapshots=True, sources=True)
        self._active.add(ctx.id)
        return {"tracing": True, "context": ctx.id}

    async def stop(self, ctx: "ManagedContext") -> dict:
        if ctx.id not in self._active:
            raise ValueError(f"no trace is running on {ctx.id}")
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(ctx.id)
        await ctx.pw_context.tracing.stop(path=str(path))
        self._active.discard(ctx.id)
        return {
            "tracing": False,
            "context": ctx.id,
            "path": str(path),
            "bytes": path.stat().st_size if path.is_file() else 0,
            "view": f"npx playwright show-trace {path}",
        }
