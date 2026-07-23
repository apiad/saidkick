"""Live terminal dashboard for ``saidkick serve``.

This is announcement channel one: the developer who started the daemon in a
terminal sees a pending request without having to open anything. Pending
requests sit at the top of the screen because they are the only thing on it
that needs a person.

:func:`render` takes data and returns a renderable, so it can be tested without
a browser or a running server.
"""

import asyncio
from typing import Any

from rich.console import Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

STATE_STYLE = {"agent": "cyan", "human": "bold yellow", "none": "dim"}


def _requests_panel(controller) -> RenderableType | None:
    pending = controller.list_pending()
    if not pending:
        return None

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    for req in pending:
        table.add_row("context", req.ctx)
        table.add_row("reason", Text(req.reason, style="bold white"))
        table.add_row(
            "waiting",
            f"{req.elapsed():.0f}s elapsed · {req.remaining():.0f}s left",
        )
        table.add_row("take over", controller.cockpit_url(req.ctx))
    return Panel(
        table,
        title="[bold red]NEEDS YOU[/bold red]",
        border_style="red",
    )


def _contexts_table(engine, controller) -> RenderableType:
    table = Table(expand=True, border_style="dim")
    table.add_column("context")
    table.add_column("control")
    table.add_column("tabs", justify="right")
    table.add_column("url", overflow="fold")

    contexts = engine.list_contexts()
    if not contexts:
        table.add_row("[dim]no contexts[/dim]", "", "", "")
        return table

    for ctx in contexts:
        state = ctx.get("controller", "agent")
        tabs = ctx.get("tabs", [])
        first_url = tabs[0]["url"] if tabs else ""
        table.add_row(
            ctx["id"],
            Text(state, style=STATE_STYLE.get(state, "")),
            str(len(tabs)),
            first_url,
        )
    return table


def render(engine, controller) -> RenderableType:
    parts: list[RenderableType] = []
    panel = _requests_panel(controller)
    if panel is not None:
        parts.append(panel)
    parts.append(_contexts_table(engine, controller))
    return Group(*parts)


async def run_dashboard(engine, controller, refresh_hz: float = 4.0) -> None:  # pragma: no cover
    """Drive the dashboard until cancelled."""
    with Live(render(engine, controller), refresh_per_second=refresh_hz) as live:
        while True:
            live.update(render(engine, controller))
            await asyncio.sleep(1.0 / refresh_hz)


def snapshot_text(engine, controller, width: int = 120) -> str:
    """Render to plain text. Used by the tests and by ``--quiet`` one-shots."""
    from rich.console import Console

    console = Console(width=width, record=True, file=_Null())
    console.print(render(engine, controller))
    return console.export_text()


class _Null:
    def write(self, *_: Any) -> None:
        pass

    def flush(self) -> None:
        pass
