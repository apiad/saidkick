import base64
import logging
import sys
from typing import List, Optional

import typer
import uvicorn
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

from saidkick.client import SaidkickClient

app = typer.Typer(help="Saidkick Dev Tool CLI")
console = Console(
    theme=Theme({
        "info": "cyan", "warning": "yellow", "error": "red", "success": "green",
        "status": "blue", "cmd": "magenta", "browser": "white",
    })
)
client = SaidkickClient()


def handle_client_error(e: Exception):
    import httpx
    if isinstance(e, httpx.ConnectError):
        console.print("[error]Error: Saidkick server is not running.[/error]")
    elif isinstance(e, httpx.HTTPStatusError):
        try:
            detail = e.response.json().get("detail", str(e))
        except Exception:
            detail = str(e)
        console.print(f"[error]Error: {detail}[/error]")
    else:
        console.print(f"[error]Error: {e}[/error]")
    raise typer.Exit(1)


def _locator_kwargs(
    css: Optional[str], xpath: Optional[str],
    by_text: Optional[str], by_label: Optional[str], by_placeholder: Optional[str],
    within_css: Optional[str], nth: Optional[int],
    exact: bool, regex: bool,
):
    return dict(
        css=css, xpath=xpath,
        by_text=by_text, by_label=by_label, by_placeholder=by_placeholder,
        within_css=within_css, nth=nth, exact=exact, regex=regex,
    )


@app.command()
def start(
    host: str = typer.Option(
        "127.0.0.1", "--host",
        help="Bind address. Default 127.0.0.1 (localhost-only). "
             "Use '0.0.0.0' to expose on LAN — anyone who can reach this port "
             "can run arbitrary JS in your logged-in browser tabs.",
    ),
    port: int = typer.Option(6992, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload for development."),
):
    """Start the Saidkick FastAPI server."""
    logging.basicConfig(
        level="INFO", format="%(message)s", datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=console)],
    )
    logging.getLogger("saidkick").setLevel(logging.INFO)
    if host == "0.0.0.0":
        console.print(
            "[warning]⚠  host=0.0.0.0 exposes saidkick on every interface. "
            "Anyone who can reach this port can drive your browser. "
            "Use 127.0.0.1 (default) unless you intend LAN/remote access.[/warning]"
        )
    uvicorn.run("saidkick.server:app", host=host, port=port, reload=reload, log_level="info")


@app.command()
def logs(
    limit: int = typer.Option(100, "--limit", "-n"),
    grep: str = typer.Option(None, "--grep", "-g"),
    browser: str = typer.Option(None, "--browser"),
):
    """Fetch console logs."""
    try:
        for log in client.get_logs(limit=limit, grep=grep, browser=browser):
            level = log.get("level", "info").upper()
            console.print(f"[browser]{log.get('browser_id','')} {level}: {log.get('data')}[/browser]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def tabs(active: bool = typer.Option(False, "--active")):
    """List tabs across connected browsers."""
    try:
        entries = client.list_tabs(active=active)
        if not entries:
            console.print("[warning]No tabs. Is a browser connected?[/warning]")
            return
        for e in entries:
            marker = "  [success](active)[/success]" if e.get("active") else ""
            console.print(
                f"[cmd]{e['tab']}[/cmd]  {e.get('url') or ''}  "
                f"[info]\"{e.get('title') or ''}\"[/info]{marker}"
            )
    except Exception as e:
        handle_client_error(e)


@app.command()
def find(
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Return JSON list of locator matches (debug aid)."""
    try:
        import json
        out = client.find(
            tab=tab, wait_ms=wait_ms,
            **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                              within_css, nth, exact, regex),
        )
        sys.stdout.write(json.dumps(out, indent=2))
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


@app.command()
def dom(
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    all_matches: bool = typer.Option(False, "--all"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Get DOM of the matched element(s)."""
    try:
        out = client.get_dom(
            tab=tab, all_matches=all_matches, wait_ms=wait_ms,
            **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                              within_css, nth, exact, regex),
        )
        sys.stdout.write(str(out)); sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


@app.command()
def text(
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Print innerText of the tab or a located element."""
    try:
        out = client.text(
            tab=tab, wait_ms=wait_ms,
            **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                              within_css, nth, exact, regex),
        )
        sys.stdout.write(str(out)); sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


@app.command()
def click(
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Click a located element."""
    try:
        out = client.click(
            tab=tab, wait_ms=wait_ms,
            **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                              within_css, nth, exact, regex),
        )
        console.print(f"[success]{out}[/success]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def type(
    text: str = typer.Argument(...),
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    clear: bool = typer.Option(False, "--clear"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Type into a located element (contenteditable-aware)."""
    try:
        out = client.type(
            tab=tab, text=text, clear=clear, wait_ms=wait_ms,
            **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                              within_css, nth, exact, regex),
        )
        console.print(f"[success]{out}[/success]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def select(
    value: str = typer.Argument(...),
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Select an option in a <select>."""
    try:
        out = client.select(
            tab=tab, value=value, wait_ms=wait_ms,
            **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                              within_css, nth, exact, regex),
        )
        console.print(f"[success]{out}[/success]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def scroll(
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    block: str = typer.Option("center", "--block", help="start | center | end | nearest"),
    behavior: str = typer.Option("auto", "--behavior", help="auto | smooth"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Scroll a located element into view."""
    try:
        out = client.scroll(
            tab=tab, block=block, behavior=behavior, wait_ms=wait_ms,
            **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                              within_css, nth, exact, regex),
        )
        console.print(f"[success]{out}[/success]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def highlight(
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    xpath: str = typer.Option(None, "--xpath"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    exact: bool = typer.Option(False, "--exact"),
    regex: bool = typer.Option(False, "--regex"),
    color: str = typer.Option("#ff3b30", "--color", help="Any CSS color"),
    duration_ms: int = typer.Option(2000, "--duration-ms", help="0 = persist until page reload"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Draw a temporary ring around a located element — point the user at it."""
    try:
        out = client.highlight(
            tab=tab, color=color, duration_ms=duration_ms, wait_ms=wait_ms,
            **_locator_kwargs(css, xpath, by_text, by_label, by_placeholder,
                              within_css, nth, exact, regex),
        )
        console.print(f"[success]{out}[/success]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def press(
    key: str = typer.Argument(..., help="Enter, Escape, Tab, a, ArrowDown, ..."),
    tab: str = typer.Option(..., "--tab"),
    mod: List[str] = typer.Option([], "--mod", help="ctrl,shift,alt,meta (comma or repeated)"),
    css: str = typer.Option(None, "--css"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    wait_ms: int = typer.Option(0, "--wait-ms"),
):
    """Press a key, optionally focusing a target first."""
    mods: List[str] = []
    for entry in mod:
        for part in entry.split(","):
            part = part.strip()
            if part:
                mods.append(part)
    try:
        out = client.press(
            tab=tab, key=key, modifiers=mods, wait_ms=wait_ms,
            **_locator_kwargs(css, None, by_text, by_label, by_placeholder,
                              within_css, nth, False, False),
        )
        console.print(f"[success]{out}[/success]")
    except Exception as e:
        handle_client_error(e)


@app.command()
def screenshot(
    tab: str = typer.Option(..., "--tab"),
    css: str = typer.Option(None, "--css"),
    by_text: str = typer.Option(None, "--by-text"),
    by_label: str = typer.Option(None, "--by-label"),
    by_placeholder: str = typer.Option(None, "--by-placeholder"),
    within_css: str = typer.Option(None, "--within-css"),
    nth: int = typer.Option(None, "--nth"),
    full_page: bool = typer.Option(False, "--full-page"),
    output: str = typer.Option(None, "--output"),
):
    """Capture a PNG. Default: stdout raw bytes. --output to write to file."""
    try:
        result = client.screenshot(
            tab=tab, full_page=full_page,
            **_locator_kwargs(css, None, by_text, by_label, by_placeholder,
                              within_css, nth, False, False),
        )
        data = base64.b64decode(result["png_base64"])
        if output:
            with open(output, "wb") as f:
                f.write(data)
            console.print(f"[success]Wrote {len(data)} bytes to {output}[/success]")
        else:
            sys.stdout.buffer.write(data)
    except Exception as e:
        handle_client_error(e)


@app.command()
def navigate(
    url: str = typer.Argument(...),
    tab: str = typer.Option(..., "--tab"),
    wait: str = typer.Option("dom", "--wait"),
    timeout_ms: int = typer.Option(15000, "--timeout-ms"),
):
    """Navigate a tab to URL."""
    try:
        out = client.navigate(tab=tab, url=url, wait=wait, timeout_ms=timeout_ms)
        sys.stdout.write(out.get("url", "")); sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


@app.command("open")
def open_cmd(
    url: str = typer.Argument(...),
    browser: str = typer.Option(..., "--browser"),
    wait: str = typer.Option("dom", "--wait"),
    timeout_ms: int = typer.Option(15000, "--timeout-ms"),
    activate: bool = typer.Option(False, "--activate"),
):
    """Open URL in new tab."""
    try:
        out = client.open(browser=browser, url=url, wait=wait,
                          timeout_ms=timeout_ms, activate=activate)
        sys.stdout.write(out.get("tab", "")); sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


@app.command()
def exec(
    tab: str = typer.Option(..., "--tab"),
    code: Optional[str] = typer.Argument(None),
):
    """Execute JS in tab. Must 'return' a value to see it (0.4.0 breaking change)."""
    if code is None:
        if sys.stdin.isatty():
            console.print("[warning]Waiting for JS from stdin... (Ctrl+D to finish)[/warning]")
        code = sys.stdin.read()
    if not code or not code.strip():
        console.print("[error]Error: No code provided.[/error]")
        raise typer.Exit(1)
    try:
        result = client.execute(tab=tab, code=code)
        if isinstance(result, (dict, list)):
            import json
            sys.stdout.write(json.dumps(result))
        else:
            sys.stdout.write(str(result) if result is not None else "")
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)


if __name__ == "__main__":
    app()
