import logging
import sys
from typing import Optional

import typer
import uvicorn
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

from saidkick.client import SaidkickClient

app = typer.Typer(help="Saidkick Dev Tool CLI")
console = Console(
    theme=Theme(
        {
            "info": "cyan",
            "warning": "yellow",
            "error": "red",
            "success": "green",
            "status": "blue",
            "cmd": "magenta",
            "browser": "white",
        }
    )
)

client = SaidkickClient()

def handle_client_error(e: Exception):
    import httpx
    if isinstance(e, httpx.ConnectError):
        console.print("[error]Error: Saidkick server is not running.[/error]")
    elif isinstance(e, httpx.HTTPStatusError):
        try:
            detail = e.response.json().get("detail", str(e))
        except:
            detail = str(e)
        console.print(f"[error]Error: {detail}[/error]")
    else:
        console.print(f"[error]Error: {e}[/error]")
    raise typer.Exit(1)

@app.command()
def start(host: str = "0.0.0.0", port: int = 6992, reload: bool = False):
    """Start the Saidkick FastAPI server."""
    logging.basicConfig(
        level="INFO",
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=console)],
    )
    saidkick_logger = logging.getLogger("saidkick")
    saidkick_logger.setLevel(logging.INFO)

    uvicorn.run(
        "saidkick.server:app", host=host, port=port, reload=reload, log_level="info"
    )

@app.command()
def logs(
    limit: int = typer.Option(100, "--limit", "-n", help="Limit number of logs"),
    grep: str = typer.Option(None, "--grep", "-g", help="Filter logs by regex"),
):
    """Fetch and display browser console logs."""
    try:
        logs_data = client.get_logs(limit=limit, grep=grep)
        for log in logs_data:
            level = log.get("level", "info").upper()
            data = log.get("data")
            console.print(f"[browser]{level}: {data}[/browser]")
    except Exception as e:
        handle_client_error(e)

@app.command()
def dom(
    css: str = typer.Option(None, help="CSS selector"),
    xpath: str = typer.Option(None, help="XPath selector"),
    all_matches: bool = typer.Option(False, "--all", help="Return all matches"),
):
    """Get the current page DOM."""
    try:
        result = client.get_dom(css=css, xpath=xpath, all_matches=all_matches)
        sys.stdout.write(str(result))
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)

@app.command()
def click(
    css: str = typer.Option(None, help="CSS selector"),
    xpath: str = typer.Option(None, help="XPath selector"),
):
    """Click an element."""
    try:
        result = client.click(css=css, xpath=xpath)
        console.print(f"[success]{result}[/success]")
    except Exception as e:
        handle_client_error(e)

@app.command()
def type(
    text: str = typer.Argument(..., help="Text to type"),
    css: str = typer.Option(None, help="CSS selector"),
    xpath: str = typer.Option(None, help="XPath selector"),
    clear: bool = typer.Option(False, "--clear", help="Clear field before typing"),
):
    """Type text into an element."""
    try:
        result = client.type(text, css=css, xpath=xpath, clear=clear)
        console.print(f"[success]{result}[/success]")
    except Exception as e:
        handle_client_error(e)

@app.command()
def select(
    value: str = typer.Argument(..., help="Value or text to select"),
    css: str = typer.Option(None, help="CSS selector"),
    xpath: str = typer.Option(None, help="XPath selector"),
):
    """Select an option in a <select> element."""
    try:
        result = client.select(value, css=css, xpath=xpath)
        console.print(f"[success]{result}[/success]")
    except Exception as e:
        handle_client_error(e)

@app.command()
def exec(
    code: Optional[str] = typer.Argument(
        None, help="JS code to execute. Reads from stdin if not provided."
    )
):
    """Execute JavaScript in the connected browser."""
    if code is None:
        if sys.stdin.isatty():
            console.print(
                "[warning]Waiting for JS from stdin... (Ctrl+D to finish)[/warning]"
            )
        code = sys.stdin.read()

    if not code or not code.strip():
        console.print("[error]Error: No code provided.[/error]")
        raise typer.Exit(1)

    try:
        result = client.execute(code)
        if isinstance(result, (dict, list)):
            import json
            sys.stdout.write(json.dumps(result))
        else:
            sys.stdout.write(str(result))
        sys.stdout.write("\n")
    except Exception as e:
        handle_client_error(e)

if __name__ == "__main__":
    app()
