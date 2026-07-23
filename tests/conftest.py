import functools
import http.server
import socket
import threading
from pathlib import Path

import pytest
import pytest_asyncio

SITE = Path(__file__).parent / "fixtures" / "site"

# saidkick.engine and saidkick.control are imported inside the fixtures rather
# than at module scope so that collection keeps working while those modules are
# still being built out. A missing module then fails only the tests that need
# it, instead of the whole session.


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep pytest output readable
        pass


@pytest.fixture(scope="session")
def fixture_url():
    """Serve tests/fixtures/site over HTTP so pages have a real origin.

    file:// would work for the DOM but not for localStorage, which the
    context-isolation tests depend on.
    """
    handler = functools.partial(_QuietHandler, directory=str(SITE))
    port = _free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture
def controller():
    from saidkick.control import Controller

    return Controller()


@pytest_asyncio.fixture
async def engine(controller):
    from saidkick.engine import Engine

    eng = Engine(controller=controller)
    await eng.start()
    yield eng
    await eng.stop()


@pytest_asyncio.fixture
async def ctx(engine):
    return await engine.open_context()


@pytest_asyncio.fixture
async def tab(ctx, fixture_url):
    return await ctx.open_tab(f"{fixture_url}/form.html")
