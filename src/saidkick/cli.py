"""The saidkick CLI.

``saidkick serve`` runs the daemon and renders a live dashboard in the terminal
it was started from — that is the first of the three ways a pending human
request reaches a person. Everything else is a thin wrapper over the REST API.
"""

import asyncio
import os
import sys

import httpx
import typer
import uvicorn
from rich.console import Console

from saidkick.client import SaidkickClient
from saidkick.config import home as config_home

app = typer.Typer(help="saidkick — a browser for agents, supervised by humans.", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)


LOOPBACK = ("127.0.0.1", "localhost", "::1", "")


def _cli_token() -> str | None:
    """The token the CLI presents: env first, then the daemon's token file."""
    env = os.environ.get("SAIDKICK_TOKEN")
    if env:
        return env
    path = config_home() / "token"
    return path.read_text().strip() if path.is_file() else None


def _client() -> SaidkickClient:
    return SaidkickClient(token=_cli_token())


def _guard_bind(host: str, require_auth: bool) -> None:
    """Refuse to publish an unauthenticated browser to the network.

    The daemon drives a browser holding real logged-in profiles; reachable and
    unauthenticated, that is a credential-exfiltration surface.
    """
    if require_auth or host in LOOPBACK:
        return
    err_console.print(
        f"[red]refusing to bind {host} with authentication disabled.[/red]\n"
        "A non-loopback bind exposes a browser holding your real logins.\n"
        "Drop --no-auth, or bind 127.0.0.1."
    )
    raise typer.Exit(1)


def handle_client_error(exc: Exception) -> None:
    if isinstance(exc, httpx.ConnectError):
        err_console.print("[red]saidkick is not running.[/red] Start it with: saidkick serve")
    elif isinstance(exc, httpx.HTTPStatusError):
        try:
            body = exc.response.json()
            detail = f"{body.get('error', 'error')}: {body.get('detail', '')}"
        except Exception:
            detail = str(exc)
        err_console.print(f"[red]{detail}[/red]")
    else:
        err_console.print(f"[red]{exc}[/red]")
    raise typer.Exit(1)


def _locator(
    css: str | None = None,
    xpath: str | None = None,
    by_text: str | None = None,
    by_label: str | None = None,
    by_placeholder: str | None = None,
    by_role: str | None = None,
    within_css: str | None = None,
    nth: int | None = None,
    exact: bool = False,
    regex: bool = False,
    wait_ms: int = 0,
) -> dict:
    return {
        "css": css,
        "xpath": xpath,
        "by_text": by_text,
        "by_label": by_label,
        "by_placeholder": by_placeholder,
        "by_role": by_role,
        "within_css": within_css,
        "nth": nth,
        "exact": exact,
        "regex": regex,
        "wait_ms": wait_ms,
    }


# Shared locator options, declared once so every verb accepts the same vocabulary.
CSS = typer.Option(None, "--css")
XPATH = typer.Option(None, "--xpath")
BY_TEXT = typer.Option(None, "--by-text")
BY_LABEL = typer.Option(None, "--by-label")
BY_PLACEHOLDER = typer.Option(None, "--by-placeholder")
BY_ROLE = typer.Option(None, "--by-role")
WITHIN = typer.Option(None, "--within-css")
NTH = typer.Option(None, "--nth")
EXACT = typer.Option(False, "--exact")
REGEX = typer.Option(False, "--regex")
WAIT_MS = typer.Option(0, "--wait-ms")
TAB = typer.Option(..., "--tab")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(6992, "--port"),
    headless: bool = typer.Option(True, "--headless/--headful"),
    quiet: bool = typer.Option(False, "--quiet", help="Plain logs instead of the dashboard."),
    no_auth: bool = typer.Option(False, "--no-auth", help="Disable token auth (loopback only)."),
    max_contexts: int | None = typer.Option(None, "--max-contexts"),
    idle_ttl: float | None = typer.Option(None, "--idle-ttl", help="Seconds; 0 disables."),
    runlog: bool = typer.Option(False, "--runlog", help="Persist a run log to beaver."),
):
    """Run the daemon: browser engine, REST, MCP at /mcp, and the cockpit."""
    from saidkick.api import create_app
    from saidkick.config import Settings
    from saidkick.control import Controller
    from saidkick.dashboard import run_dashboard
    from saidkick.engine import Engine
    from saidkick.events import EventBus
    from saidkick.mcp_server import build_mcp
    from saidkick.pins import PinRegistry
    from saidkick.reaper import run_reaper
    from saidkick.runlog import RunLog
    from saidkick.tracing import TraceManager

    _guard_bind(host, require_auth=not no_auth)

    settings = Settings.from_env(
        require_auth=not no_auth if no_auth else None,
        max_contexts=max_contexts,
        idle_ttl_s=idle_ttl,
        runlog=runlog or None,
    )
    if no_auth:
        settings.require_auth = False

    controller = Controller(cockpit_base=f"http://{host}:{port}")
    sink = RunLog(settings.runlog_path if settings.runlog else None, redact=settings.redact)
    engine = Engine(
        headless=headless, controller=controller, settings=settings, runlog=sink
    )
    events = EventBus()
    pins = PinRegistry()
    traces = TraceManager(settings.trace_dir)
    mcp = build_mcp(engine, controller, events, pins, traces)
    api = create_app(engine, controller, events, mcp=mcp, pins=pins, settings=settings)

    async def main():
        engine.preflight()
        await engine.start()
        config = uvicorn.Config(api, host=host, port=port, log_level="warning" if not quiet else "info")
        server = uvicorn.Server(config)
        token = api.state.token
        console.print(f"[green]saidkick[/green] on http://{host}:{port}  ·  cockpit /  ·  MCP /mcp")
        if token:
            console.print(f"cockpit: http://{host}:{port}/?token={token}")
        else:
            console.print("[yellow]authentication disabled[/yellow] (loopback only)")
        if sink.enabled:
            console.print(f"run log: {settings.runlog_path}")
        tasks = [asyncio.create_task(server.serve())]
        tasks.append(asyncio.create_task(run_reaper(engine, settings, events)))
        if not quiet:
            tasks.append(asyncio.create_task(run_dashboard(engine, controller)))
        try:
            await tasks[0]
        finally:
            for task in tasks[1:]:
                task.cancel()
            await engine.stop()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:  # pragma: no cover
        pass


@app.command()
def contexts():
    """List live browsing contexts."""
    try:
        for ctx in _client().list_contexts():
            # Parens, not brackets: Rich reads [..] as console markup and drops it.
            tag = f"{ctx['mode']}:{ctx['profile']}" if ctx.get("profile") else ctx["mode"]
            console.print(
                f"{ctx['id']}  {ctx['controller']:<6} {len(ctx['tabs'])} tab(s)  ({tag})"
            )
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def tabs(context: str = typer.Option(..., "--context")):
    """List tabs in a context."""
    try:
        for tab in _client().list_tabs(context):
            console.print(f"{tab['id']}  {tab['url']}")
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def quick(url: str):
    """Open an ephemeral context and a tab in one call; print the tab id."""
    try:
        print(_client().quick(url))
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def runlog(
    context: str | None = typer.Option(None, "--context"),
    limit: int = typer.Option(50, "--limit"),
):
    """Show recent recorded actions."""
    try:
        rows = _client().runlog(context=context, limit=limit)
        if not rows:
            console.print("[dim]no records (start the daemon with --runlog)[/dim]")
        for r in rows:
            mark = "ok" if r.get("ok") else (r.get("error") or "error")
            console.print(
                f"{r.get('ctx', '-'):<10} {r['kind']:<10} {mark:<18} "
                f"{r.get('ms', '-')}ms  {r.get('locator') or ''}"
            )
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def token():
    """Print the daemon's auth token (from SAIDKICK_TOKEN or the token file)."""
    found = _cli_token()
    if found:
        print(found)
    else:
        err_console.print("[yellow]no token found[/yellow] — the daemon generates one on first run")
        raise typer.Exit(1)


@app.command()
def profiles():
    """List saved profiles."""
    try:
        found = _client().list_profiles()
        if not found:
            console.print("[dim]no profiles[/dim]")
        for prof in found:
            marks = []
            if prof["has_state"]:
                marks.append("seeded")
            if prof["has_userdata"]:
                marks.append("attached")
            console.print(f"{prof['name']:<20} {', '.join(marks) or 'empty'}")
    except Exception as exc:
        handle_client_error(exc)


@app.command("save-profile")
def save_profile(
    context: str = typer.Option(..., "--context"),
    name: str = typer.Option(..., "--name"),
):
    """Save a context's login to a named profile."""
    try:
        out = _client().save_profile(context, name)
        console.print(
            f"saved [green]{out['profile']}[/green]: "
            f"{out['cookies']} cookie(s), {out['origins']} origin(s)"
        )
    except Exception as exc:
        handle_client_error(exc)


@app.command("open")
def open_cmd(url: str, context: str = typer.Option(..., "--context")):
    """Open a tab in an existing context; print the tab id."""
    try:
        print(_client().open_tab(context, url)["id"])
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def close(tab: str = TAB):
    """Close a tab."""
    try:
        _client().close_tab(tab)
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def navigate(url: str, tab: str = TAB, wait: str = typer.Option("load", "--wait")):
    """Point a tab at a URL."""
    try:
        _client().navigate(tab, url, wait=wait)
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def snapshot(
    tab: str = TAB,
    mode: str = typer.Option("aria", "--mode", help="aria | text | html"),
    within_css: str | None = WITHIN,
):
    """Read the page. 'aria' is compact and maps onto locators."""
    try:
        print(_client().snapshot(tab, mode=mode, within_css=within_css))
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def find(
    tab: str = TAB, css: str | None = CSS, xpath: str | None = XPATH,
    by_text: str | None = BY_TEXT, by_label: str | None = BY_LABEL,
    by_placeholder: str | None = BY_PLACEHOLDER, by_role: str | None = BY_ROLE,
    within_css: str | None = WITHIN, nth: int | None = NTH,
    exact: bool = EXACT, regex: bool = REGEX, wait_ms: int = WAIT_MS,
):
    """Describe matching elements without acting on them."""
    try:
        loc = _locator(css, xpath, by_text, by_label, by_placeholder, by_role,
                       within_css, nth, exact, regex, wait_ms)
        for el in _client().find(tab, **loc):
            console.print(f"{el['tag']:<10} {el.get('text', '')[:60]}")
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def click(
    tab: str = TAB, css: str | None = CSS, xpath: str | None = XPATH,
    by_text: str | None = BY_TEXT, by_label: str | None = BY_LABEL,
    by_placeholder: str | None = BY_PLACEHOLDER, by_role: str | None = BY_ROLE,
    within_css: str | None = WITHIN, nth: int | None = NTH,
    exact: bool = EXACT, regex: bool = REGEX, wait_ms: int = WAIT_MS,
):
    """Click an element."""
    try:
        loc = _locator(css, xpath, by_text, by_label, by_placeholder, by_role,
                       within_css, nth, exact, regex, wait_ms)
        _client().click(tab, **loc)
    except Exception as exc:
        handle_client_error(exc)


@app.command("type")
def type_cmd(
    text: str, tab: str = TAB, submit: bool = typer.Option(False, "--submit"),
    css: str | None = CSS, xpath: str | None = XPATH,
    by_text: str | None = BY_TEXT, by_label: str | None = BY_LABEL,
    by_placeholder: str | None = BY_PLACEHOLDER, by_role: str | None = BY_ROLE,
    within_css: str | None = WITHIN, nth: int | None = NTH,
    exact: bool = EXACT, regex: bool = REGEX, wait_ms: int = WAIT_MS,
):
    """Type into a field, including rich-text editors."""
    try:
        loc = _locator(css, xpath, by_text, by_label, by_placeholder, by_role,
                       within_css, nth, exact, regex, wait_ms)
        _client().type(tab, text, submit=submit, **loc)
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def select(
    value: str, tab: str = TAB, css: str | None = CSS, xpath: str | None = XPATH,
    by_label: str | None = BY_LABEL, within_css: str | None = WITHIN,
    nth: int | None = NTH, wait_ms: int = WAIT_MS,
):
    """Choose an <option>."""
    try:
        loc = _locator(css, xpath, None, by_label, None, None, within_css, nth, False, False, wait_ms)
        _client().select(tab, [value], **loc)
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def press(
    key: str, tab: str = TAB,
    modifier: list[str] = typer.Option([], "--mod"),
    css: str | None = CSS, by_label: str | None = BY_LABEL,
    by_text: str | None = BY_TEXT, wait_ms: int = WAIT_MS,
):
    """Dispatch a real keyboard event."""
    try:
        loc = _locator(css, None, by_text, by_label, None, None, None, None, False, False, wait_ms)
        _client().press(tab, key, modifiers=list(modifier), **loc)
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def scroll(
    tab: str = TAB, css: str | None = CSS, by_text: str | None = BY_TEXT,
    by_label: str | None = BY_LABEL, wait_ms: int = WAIT_MS,
):
    """Scroll an element into view."""
    try:
        loc = _locator(css, None, by_text, by_label, None, None, None, None, False, False, wait_ms)
        _client().scroll(tab, **loc)
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def highlight(
    tab: str = TAB, color: str = typer.Option("#ef4444", "--color"),
    duration_ms: int = typer.Option(2000, "--duration-ms"),
    css: str | None = CSS, by_text: str | None = BY_TEXT,
    by_label: str | None = BY_LABEL, wait_ms: int = WAIT_MS,
):
    """Ring an element so a human can see what you mean."""
    try:
        loc = _locator(css, None, by_text, by_label, None, None, None, None, False, False, wait_ms)
        _client().highlight(tab, color=color, duration_ms=duration_ms, **loc)
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def screenshot(
    tab: str = TAB,
    output: str | None = typer.Option(None, "--output"),
    full_page: bool = typer.Option(False, "--full-page"),
):
    """Capture a PNG. Writes to --output, or raw bytes to stdout."""
    try:
        png = _client().screenshot(tab, full_page=full_page)
        if output:
            with open(output, "wb") as handle:
                handle.write(png)
            console.print(f"wrote {len(png)} bytes to {output}")
        else:
            sys.stdout.buffer.write(png)
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def pins(context: str = typer.Option(..., "--context")):
    """List the pins a human placed in a context."""
    try:
        found = _client().pins(context)
        if not found:
            console.print("[dim]no pins[/dim]")
        for pin in found:
            d = pin["descriptor"]
            console.print(f"{pin['handle']}  {pin.get('label') or d.get('text') or d['tag']}")
    except Exception as exc:
        handle_client_error(exc)


@app.command()
def requests():
    """Show pending human requests."""
    try:
        pending = _client().requests()
        if not pending:
            console.print("[dim]nothing pending[/dim]")
        for req in pending:
            console.print(f"{req['context']}  {req['reason']}  ({req['remaining_s']:.0f}s left)")
    except Exception as exc:
        handle_client_error(exc)


if __name__ == "__main__":  # pragma: no cover
    app()
