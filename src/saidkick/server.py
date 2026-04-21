import asyncio
import json
import logging
import re
import secrets
import uuid
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

# Configure logger
logger = logging.getLogger("saidkick")


_TAB_ID_RE = re.compile(r"^br-[0-9a-f]{4}:(\d+)$")
_BROWSER_ID_RE = re.compile(r"^br-[0-9a-f]{4}$")


def parse_tab_id(composite: str) -> Tuple[str, int]:
    """Parse 'br-XXXX:N' into (browser_id, tab_id). Raises ValueError on malformed input."""
    if not isinstance(composite, str):
        raise ValueError(f"tab ID must be a string, got {type(composite).__name__}")
    m = _TAB_ID_RE.match(composite)
    if not m:
        raise ValueError(f"invalid tab ID: expected 'br-XXXX:N', got {composite!r}")
    browser_id, tab_str = composite.rsplit(":", 1)
    return browser_id, int(tab_str)


def _raise_for_extension_error(payload: str) -> None:
    """Map a failure string from the extension into an HTTPException.

    500 is reserved for server bugs; anything caller-observable gets a 4xx or
    502/504. The extension's message strings are ours, so keyword matches are
    deterministic.
    """
    m = (payload or "").lower()
    if ("element not found" in m
        or "option not found" in m
        or "tab not found" in m):
        raise HTTPException(status_code=404, detail=payload)
    if ("ambiguous selector" in m
        or "element is not a" in m
        or "no selector provided" in m
        or "invalid url" in m):
        raise HTTPException(status_code=400, detail=payload)
    if "timeout" in m or "not resolved within" in m:
        raise HTTPException(status_code=504, detail=payload)
    raise HTTPException(status_code=502, detail=payload)


def _validate_http_url(url: str) -> None:
    """Raise HTTPException(400) if `url` is not a well-formed http(s) URL."""
    from urllib.parse import urlparse
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"invalid url: {url!r}")


def _validate_browser_id(browser_id: str) -> None:
    if not isinstance(browser_id, str) or not _BROWSER_ID_RE.match(browser_id):
        raise HTTPException(
            status_code=400,
            detail=f"invalid browser ID: expected 'br-XXXX', got {browser_id!r}",
        )

class ExecuteRequest(BaseModel):
    tab: str
    code: str

class SelectorRequest(BaseModel):
    tab: str
    css: Optional[str] = None
    xpath: Optional[str] = None
    wait_ms: int = 0

class TypeRequest(SelectorRequest):
    text: str
    clear: bool = False

class SelectRequest(SelectorRequest):
    value: str


def _parse_or_400(tab: str) -> Tuple[str, int]:
    try:
        return parse_tab_id(tab)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

class SaidkickManager:
    def __init__(self, max_logs: int = 100):
        self.logs = deque(maxlen=max_logs)
        self.connections: Dict[str, WebSocket] = {}
        self.pending_requests: Dict[str, asyncio.Future] = {}

    def _random_browser_id(self) -> str:
        # 4 hex chars = 65k space, ample for expected usage.
        return "br-" + secrets.token_hex(2)

    def generate_browser_id(self) -> str:
        # Avoid collision with an already-connected browser.
        for _ in range(1000):
            bid = self._random_browser_id()
            if bid not in self.connections:
                return bid
        raise RuntimeError("could not generate unique browser_id after 1000 attempts")

    async def add_connection(self, websocket: WebSocket) -> str:
        await websocket.accept()
        browser_id = self.generate_browser_id()
        self.connections[browser_id] = websocket
        logger.info(f"[status] Browser connected: {browser_id}")
        return browser_id

    def remove_connection(self, browser_id: str):
        if browser_id in self.connections:
            del self.connections[browser_id]
            logger.info(f"[status] Browser disconnected: {browser_id}")

    def handle_log(self, browser_id: str, message: Dict[str, Any]):
        level = message.get("level", "info").upper()
        content = message.get("data")
        logger.info(f"[BROWSER {browser_id}] {level}: {content}")
        message = {**message, "browser_id": browser_id}
        self.logs.append(message)

    def handle_response(self, message: Dict[str, Any]):
        request_id = message.get("id")
        if request_id in self.pending_requests:
            future = self.pending_requests.pop(request_id)
            if not future.done():
                future.set_result(message)

    async def send_command(
        self, browser_id: str, command_type: str, payload: Any = None
    ) -> Dict[str, Any]:
        ws = self.connections.get(browser_id)
        if ws is None:
            raise HTTPException(
                status_code=404, detail=f"browser not connected: {browser_id}"
            )

        request_id = str(uuid.uuid4())
        future = asyncio.get_event_loop().create_future()
        self.pending_requests[request_id] = future

        logger.info(f"[CMD] {browser_id} <- {command_type}")
        try:
            await ws.send_text(
                json.dumps({"type": command_type, "id": request_id, "payload": payload})
            )
        except Exception as e:
            self.pending_requests.pop(request_id, None)
            raise HTTPException(
                status_code=503, detail=f"browser send failed: {e}"
            ) from e

        try:
            response = await asyncio.wait_for(future, timeout=10.0)
            return response
        except asyncio.TimeoutError as e:
            self.pending_requests.pop(request_id, None)
            raise HTTPException(
                status_code=504, detail="Browser response timeout"
            ) from e

    def get_logs(
        self, limit: int = 100,
        grep: Optional[str] = None, browser: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        all_logs = list(self.logs)
        if browser:
            all_logs = [l for l in all_logs if l.get("browser_id") == browser]
        if grep:
            pattern = re.compile(grep)
            all_logs = [l for l in all_logs if pattern.search(str(l.get("data", "")))]
        return all_logs[-limit:] if limit > 0 else all_logs

manager = SaidkickManager()
app = FastAPI(title="Saidkick Dev Tool")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    browser_id = await manager.add_connection(websocket)
    await websocket.send_text(json.dumps({"type": "HELLO", "browser_id": browser_id}))
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get("type")
            if msg_type == "log":
                manager.handle_log(browser_id, message)
            elif msg_type == "RESPONSE":
                manager.handle_response(message)
    except WebSocketDisconnect:
        manager.remove_connection(browser_id)
    except Exception as e:
        logger.error(f"[error] WebSocket error on {browser_id}: {e}")
        manager.remove_connection(browser_id)

@app.get("/console")
async def get_console(
    limit: int = 100,
    grep: Optional[str] = None,
    browser: Optional[str] = None,
):
    return manager.get_logs(limit=limit, grep=grep, browser=browser)

@app.get("/dom")
async def get_dom(
    tab: str,
    css: Optional[str] = None,
    xpath: Optional[str] = None,
    all: bool = False,
    wait_ms: int = 0,
):
    browser_id, tab_id = _parse_or_400(tab)
    response = await manager.send_command(
        browser_id, "GET_DOM",
        payload={
            "tab_id": tab_id, "css": css, "xpath": xpath,
            "all": all, "wait_ms": wait_ms,
        },
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.post("/execute")
async def post_execute(req: ExecuteRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    response = await manager.send_command(
        browser_id, "EXECUTE",
        payload={"tab_id": tab_id, "code": req.code},
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.post("/click")
async def post_click(req: SelectorRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    response = await manager.send_command(
        browser_id, "CLICK",
        payload={
            "tab_id": tab_id, "css": req.css, "xpath": req.xpath,
            "wait_ms": req.wait_ms,
        },
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.post("/type")
async def post_type(req: TypeRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    response = await manager.send_command(
        browser_id, "TYPE",
        payload={
            "tab_id": tab_id, "css": req.css, "xpath": req.xpath,
            "text": req.text, "clear": req.clear, "wait_ms": req.wait_ms,
        },
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.post("/select")
async def post_select(req: SelectRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    response = await manager.send_command(
        browser_id, "SELECT",
        payload={
            "tab_id": tab_id, "css": req.css, "xpath": req.xpath,
            "value": req.value, "wait_ms": req.wait_ms,
        },
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.get("/tabs")
async def get_tabs(active: bool = False):
    browser_ids = list(manager.connections.keys())

    async def _fetch(bid: str):
        try:
            resp = await manager.send_command(bid, "LIST_TABS")
        except HTTPException:
            return bid, None
        if not resp.get("success"):
            return bid, None
        return bid, resp.get("payload") or []

    results = await asyncio.gather(*(_fetch(bid) for bid in browser_ids))
    tabs: List[Dict[str, Any]] = []
    for bid, raw_tabs in results:
        if raw_tabs is None:
            continue
        for t in raw_tabs:
            if active and not t.get("active"):
                continue
            tabs.append({
                "tab": f"{bid}:{t['id']}",
                "browser_id": bid,
                "tab_id": t["id"],
                "url": t.get("url"),
                "title": t.get("title"),
                "active": bool(t.get("active")),
                "windowId": t.get("windowId"),
            })
    return tabs
