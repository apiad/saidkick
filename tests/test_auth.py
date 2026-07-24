import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from saidkick.api import create_app
from saidkick.config import Settings


@pytest_asyncio.fixture
async def authed(engine):
    app = create_app(engine, settings=Settings(token="t0k", require_auth=True))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_health_is_open(authed):
    """Liveness probes must work without a credential."""
    assert (await authed.get("/health")).status_code == 200


async def test_unauthenticated_request_is_401(authed):
    r = await authed.get("/contexts")
    assert r.status_code == 401 and r.json()["error"] == "Unauthorized"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"headers": {"Authorization": "Bearer t0k"}},
        {"headers": {"X-Saidkick-Token": "t0k"}},
        {"params": {"token": "t0k"}},
        {"cookies": {"saidkick_token": "t0k"}},
    ],
)
async def test_every_accepted_credential_form(authed, kwargs):
    assert (await authed.get("/contexts", **kwargs)).status_code == 200


async def test_wrong_token_is_401(authed):
    r = await authed.get("/contexts", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


async def test_auth_disabled_allows_everything(engine):
    app = create_app(engine, settings=Settings(token=None, require_auth=False))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/contexts")).status_code == 200


async def test_mutating_routes_are_protected_too(authed):
    """A read-only guard would be worthless; POST must be covered."""
    assert (await authed.post("/contexts")).status_code == 401


def test_token_file_is_generated_0600(tmp_path, monkeypatch):
    monkeypatch.setenv("SAIDKICK_HOME", str(tmp_path))
    monkeypatch.delenv("SAIDKICK_TOKEN", raising=False)
    from saidkick.auth import resolve_token

    t1 = resolve_token(Settings(require_auth=True))
    assert t1 and (tmp_path / "token").is_file()
    assert oct((tmp_path / "token").stat().st_mode)[-3:] == "600"
    assert resolve_token(Settings(require_auth=True)) == t1  # stable across calls


def test_env_token_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("SAIDKICK_HOME", str(tmp_path))
    monkeypatch.setenv("SAIDKICK_TOKEN", "from-env")
    from saidkick.auth import resolve_token

    assert resolve_token(Settings(require_auth=True)) == "from-env"


def test_no_token_when_auth_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("SAIDKICK_HOME", str(tmp_path))
    from saidkick.auth import resolve_token

    assert resolve_token(Settings(require_auth=False)) is None
    assert not (tmp_path / "token").exists()


@pytest.mark.browser
def test_websocket_requires_a_token(fixture_url):
    """WS cannot send headers, so it authenticates by query/cookie — and must."""
    from fastapi.testclient import TestClient

    from saidkick.engine import Engine

    engine = Engine()
    app = create_app(engine, settings=Settings(token="t0k", require_auth=True))
    auth_header = {"X-Saidkick-Token": "t0k"}
    with TestClient(app) as client:
        cid = client.post("/contexts", headers=auth_header).json()["id"]
        tid = client.post(
            f"/contexts/{cid}/tabs",
            json={"url": f"{fixture_url}/form.html"},
            headers=auth_header,
        ).json()["id"]

        with (
            pytest.raises(Exception),  # close(4401) surfaces as varying exception types
            client.websocket_connect(f"/ws/view/{tid}") as ws,
        ):
            ws.receive_json()

        # With the token it connects and streams.
        with client.websocket_connect(f"/ws/view/{tid}?token=t0k") as ws:
            assert ws.receive_json()["type"] == "frame"


def test_non_loopback_bind_without_auth_is_refused():
    """--host 0.0.0.0 with auth off would publish a browser holding real logins."""
    import click

    from saidkick.cli import _guard_bind

    with pytest.raises(click.exceptions.Exit):
        _guard_bind("0.0.0.0", require_auth=False)
    with pytest.raises(click.exceptions.Exit):
        _guard_bind("192.168.1.5", require_auth=False)
    _guard_bind("127.0.0.1", require_auth=False)  # loopback is fine
    _guard_bind("0.0.0.0", require_auth=True)  # auth on is fine


def test_token_comparison_is_constant_time():
    """Guard against a `==` regression that would leak the token by timing."""
    import inspect

    from saidkick import auth

    assert "compare_digest" in inspect.getsource(auth)


def test_engine_layer_never_imports_auth():
    """Auth is daemon-only; an engine-layer import would break the library path."""
    import inspect

    from saidkick import actions, engine, locators, pins, profiles, snapshot

    for mod in (engine, actions, locators, pins, profiles, snapshot):
        src = inspect.getsource(mod)
        assert "from .auth" not in src and "import auth" not in src, mod.__name__
