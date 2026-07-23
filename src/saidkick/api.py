"""The daemon's HTTP surface: REST, WebSockets, and the mounted MCP app.

Routes raise domain errors from :mod:`saidkick.errors` and never build HTTP
responses themselves. One exception handler turns those into status codes, so
the mapping lives in exactly one place.
"""

import asyncio
import base64
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import actions as A
from . import errors as E
from . import overlay
from .control import Controller
from .engine import Engine
from .events import EventBus
from .input_bridge import forward
from .locators import Locator
from .screencast import (
    OBSERVE_MAX_WIDTH,
    OBSERVE_QUALITY,
    TAKEOVER_MAX_WIDTH,
    TAKEOVER_QUALITY,
    ScreencastPump,
)
from .snapshot import snapshot

log = logging.getLogger("saidkick.api")

COCKPIT = Path(__file__).parent / "cockpit"

ACTIONS = {
    "click": lambda tab, loc, body: A.click(tab, loc),
    "type": lambda tab, loc, body: A.type_text(
        tab, loc, body.get("text", ""), submit=body.get("submit", False)
    ),
    "select": lambda tab, loc, body: A.select(tab, loc, body.get("values", [])),
    "hover": lambda tab, loc, body: A.hover(tab, loc),
    "scroll": lambda tab, loc, body: A.scroll(tab, loc),
    "upload": lambda tab, loc, body: A.upload(tab, loc, body.get("paths", [])),
    "highlight": lambda tab, loc, body: A.highlight(
        tab, loc, color=body.get("color", "#ef4444"),
        duration_ms=body.get("duration_ms", 2000),
    ),
    "find": lambda tab, loc, body: A.find(tab, loc),
}


def _locator(body: dict) -> Locator:
    return Locator(**{k: v for k, v in body.items() if k in Locator.model_fields})


def create_app(
    engine: Engine,
    controller: Controller | None = None,
    events: EventBus | None = None,
    mcp: Any = None,
) -> FastAPI:
    controller = controller or engine.controller or Controller()
    engine.controller = controller
    events = events or EventBus()
    pumps: dict[str, ScreencastPump] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Setting a lifespan silently disables @app.on_event handlers, so
        # engine startup has to live here. start() is idempotent; we only stop
        # what we started, so an engine owned by a caller is left alone.
        started_here = not engine.is_running
        if started_here:
            await engine.start()
        try:
            # The MCP Starlette app carries its own lifespan (the session
            # manager). Mounting it without running that lifespan yields
            # requests that hang rather than a clear error.
            if mcp is not None:
                async with mcp.session_manager.run():
                    yield
            else:
                yield
        finally:
            if started_here:
                await engine.stop()

    app = FastAPI(title="saidkick", lifespan=lifespan)
    app.state.engine = engine
    app.state.controller = controller
    app.state.events = events

    if mcp is not None:
        app.mount("/mcp", mcp.streamable_http_app())

    if COCKPIT.exists():
        app.mount("/static", StaticFiles(directory=COCKPIT / "static"), name="static")
        templates = Jinja2Templates(directory=str(COCKPIT / "templates"))
    else:  # pragma: no cover
        templates = None

    @app.exception_handler(E.SaidkickError)
    async def _domain_error(request: Request, exc: E.SaidkickError):
        return JSONResponse(status_code=exc.status, content=E.http_detail(exc))

    def _pump(tab) -> ScreencastPump:
        if tab.id not in pumps:
            pumps[tab.id] = ScreencastPump(tab)
        return pumps[tab.id]

    # -- health and contexts ---------------------------------------------

    @app.get("/health")
    async def health():
        return {"ok": True, "contexts": len(engine.list_contexts())}

    @app.get("/contexts")
    async def list_contexts():
        return engine.list_contexts()

    @app.post("/contexts")
    async def open_context(body: dict = Body(default={})):
        ctx = await engine.open_context(viewport=body.get("viewport"))
        events.emit(ctx.id, "context_opened")
        return ctx.info()

    @app.delete("/contexts/{cid}")
    async def close_context(cid: str):
        await engine.close_context(cid)
        events.emit(cid, "context_closed")
        return {"ok": True}

    @app.get("/contexts/{cid}/tabs")
    async def list_tabs(cid: str):
        return engine.get_context(cid).list_tabs()

    @app.post("/contexts/{cid}/tabs")
    async def open_tab(cid: str, body: dict = Body(default={})):
        tab = await engine.get_context(cid).open_tab(
            body.get("url"), wait=body.get("wait", "load")
        )
        events.emit(cid, "tab_opened", tab=tab.id, url=body.get("url"))
        return tab.info()

    @app.delete("/tabs/{tid}")
    async def close_tab(tid: str):
        ctx = engine.get_context(tid.split(":", 1)[0])
        await ctx.close_tab(tid)
        events.emit(ctx.id, "tab_closed", tab=tid)
        return {"ok": True}

    # -- tab operations ---------------------------------------------------

    @app.post("/tabs/{tid}/navigate")
    async def navigate(tid: str, body: dict = Body(...)):
        tab = engine.find_tab(tid)
        out = await tab.navigate(body["url"], wait=body.get("wait", "load"))
        events.emit(tab.context.id, "navigated", tab=tid, url=body["url"])
        return out

    @app.get("/tabs/{tid}/snapshot")
    async def get_snapshot(tid: str, mode: str = "aria", within_css: str | None = None):
        tab = engine.find_tab(tid)
        try:
            return {"mode": mode, "snapshot": await snapshot(tab, mode, within_css)}
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": "BadMode", "detail": str(exc)})

    @app.get("/tabs/{tid}/screenshot")
    async def get_screenshot(tid: str, full_page: bool = False):
        png = await A.screenshot(engine.find_tab(tid), full_page=full_page)
        return Response(content=png, media_type="image/png")

    @app.post("/tabs/{tid}/press")
    async def do_press(tid: str, body: dict = Body(...)):
        tab = engine.find_tab(tid)
        return await A.press(tab, body["key"], _locator(body), body.get("modifiers"))

    @app.post("/tabs/{tid}/{action}")
    async def do_action(tid: str, action: str, body: dict = Body(default={})):
        if action not in ACTIONS:
            raise E.NoSuchTab(f"unknown action: {action}")
        tab = engine.find_tab(tid)
        return {"result": await ACTIONS[action](tab, _locator(body), body)}

    # -- control ----------------------------------------------------------

    @app.get("/requests")
    async def list_requests():
        return [r.info() for r in controller.list_pending()]

    @app.get("/contexts/{cid}/events")
    async def get_events(cid: str, since: int = 0, wait_s: float = 0):
        if wait_s > 0:
            return await events.wait(cid, since, timeout_s=wait_s)
        return events.since(cid, since)

    # -- websockets -------------------------------------------------------

    @app.websocket("/ws/view/{tid}")
    async def ws_view(ws: WebSocket, tid: str):
        await ws.accept()
        tab = engine.find_tab(tid)
        pump = _pump(tab)
        queue: asyncio.Queue = asyncio.Queue(maxsize=4)
        pump.add_viewer(queue)
        await pump.start()

        async def send_frames():
            while True:
                frame = await queue.get()
                await ws.send_json({"type": "frame", **frame})

        sender = asyncio.create_task(send_frames())
        try:
            while True:
                msg = await ws.receive_json()
                if msg.get("type") == "quality":
                    await pump.set_quality(msg["quality"], msg["max_width"])
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            sender.cancel()
            await pump.remove_viewer(queue)

    @app.websocket("/ws/control/{tid}")
    async def ws_control(ws: WebSocket, tid: str):
        await ws.accept()
        tab = engine.find_tab(tid)
        cid = tab.context.id
        pump = _pump(tab)
        held = False
        try:
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type")
                if kind == "take":
                    controller.take(cid)
                    held = True
                    events.emit(cid, "control_taken")
                    if pump.running:
                        await pump.set_quality(TAKEOVER_QUALITY, TAKEOVER_MAX_WIDTH)
                    await ws.send_json({"state": controller.state(cid)})
                elif kind == "release":
                    controller.release(cid, note=msg.get("note"))
                    held = False
                    events.emit(cid, "control_released", note=msg.get("note"))
                    await overlay.hide(tab)
                    if pump.running:
                        await pump.set_quality(OBSERVE_QUALITY, OBSERVE_MAX_WIDTH)
                    await ws.send_json({"state": controller.state(cid)})
                else:
                    meta = msg.pop("metadata", {}) or {}
                    await forward(tab, msg, meta)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            # A closed laptop lid must not leave the agent permanently locked
            # out, so control is released when the socket dies.
            if held:
                controller.release(cid, note="control socket closed")
                events.emit(cid, "control_released", note="socket closed")

    # -- cockpit ----------------------------------------------------------

    if templates is not None:

        @app.get("/", response_class=HTMLResponse)
        async def cockpit_index(request: Request):
            return templates.TemplateResponse(
                request, "index.html",
                {"contexts": engine.list_contexts(),
                 "requests": [r.info() for r in controller.list_pending()]},
            )

        @app.get("/session/{cid}", response_class=HTMLResponse)
        async def cockpit_session(request: Request, cid: str):
            ctx = engine.get_context(cid)
            return templates.TemplateResponse(
                request, "session.html",
                {"ctx": ctx.info(), "pending": controller.pending(cid)},
            )

    return app


def b64png(data: bytes) -> str:  # pragma: no cover - used by MCP image results
    return base64.b64encode(data).decode()
