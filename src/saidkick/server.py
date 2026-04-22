import asyncio
import json
import logging
import re
import secrets
import uuid
from collections import deque
from typing import Any, Dict, List, Literal, Optional, Tuple

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


class Locator(BaseModel):
    """Mixin: shared locator fields for every selector-using endpoint."""
    css: Optional[str] = None
    xpath: Optional[str] = None
    by_text: Optional[str] = None
    by_label: Optional[str] = None
    by_placeholder: Optional[str] = None
    within_css: Optional[str] = None
    nth: Optional[int] = None
    exact: bool = False
    regex: bool = False


_LOCATOR_FIELDS = ("css", "xpath", "by_text", "by_label", "by_placeholder")


def _count_locators(loc: Locator) -> int:
    return sum(1 for f in _LOCATOR_FIELDS if getattr(loc, f) is not None)


def _validate_locator(loc: Locator) -> None:
    """Shared checks that apply whether or not a locator is required."""
    if loc.exact and loc.regex:
        raise HTTPException(status_code=400, detail="exact and regex are mutually exclusive")
    if _count_locators(loc) > 1:
        raise HTTPException(
            status_code=400,
            detail="Ambiguous locator options: specify exactly one",
        )


def _validate_required_locator(loc: Locator) -> None:
    _validate_locator(loc)
    if _count_locators(loc) == 0:
        raise HTTPException(
            status_code=400,
            detail="No locator: specify one of css/xpath/by-text/by-label/by-placeholder",
        )


def _locator_payload(loc: Locator) -> Dict[str, Any]:
    """Serialise locator fields for the extension payload."""
    return {
        "css": loc.css, "xpath": loc.xpath,
        "by_text": loc.by_text, "by_label": loc.by_label,
        "by_placeholder": loc.by_placeholder,
        "within_css": loc.within_css, "nth": loc.nth,
        "exact": loc.exact, "regex": loc.regex,
    }


def _command_timeout(wait_ms: int = 0, timeout_ms: int = 0) -> float:
    """Compute the server-side asyncio.wait_for budget for an extension command.

    Default 10s. Extended by the larger of wait_ms/timeout_ms plus 2s overhead
    to leave room for extension scheduling.
    """
    base = 10.0
    extension_budget_s = max(wait_ms, timeout_ms) / 1000.0
    return max(base, extension_budget_s + 2.0)

class ExecuteRequest(BaseModel):
    tab: str
    code: str

class SelectorRequest(Locator):
    tab: str
    wait_ms: int = 0

class TypeRequest(SelectorRequest):
    text: str
    clear: bool = False

class SelectRequest(SelectorRequest):
    value: str

WaitMode = Literal["dom", "full", "none"]

class NavigateRequest(BaseModel):
    tab: str
    url: str
    wait: WaitMode = "dom"
    timeout_ms: int = 15000

_VALID_MODIFIERS = {"ctrl", "shift", "alt", "meta"}


class PressRequest(Locator):
    tab: str
    key: str
    modifiers: List[str] = []
    wait_ms: int = 0


class OpenRequest(BaseModel):
    browser: str
    url: str
    wait: WaitMode = "dom"
    timeout_ms: int = 15000
    activate: bool = False


ScrollBlock = Literal["start", "center", "end", "nearest"]
ScrollBehavior = Literal["auto", "smooth"]


class ScrollRequest(Locator):
    tab: str
    block: ScrollBlock = "center"
    behavior: ScrollBehavior = "auto"
    wait_ms: int = 0


class HighlightRequest(Locator):
    tab: str
    color: str = "#ff3b30"
    duration_ms: int = 2000
    wait_ms: int = 0


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
        self.last_seen: Dict[str, float] = {}  # browser_id -> epoch seconds

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
        import time
        self.last_seen[browser_id] = time.time()
        logger.info(f"[status] Browser connected: {browser_id}")
        return browser_id

    def remove_connection(self, browser_id: str):
        if browser_id in self.connections:
            del self.connections[browser_id]
            self.last_seen.pop(browser_id, None)
            logger.info(f"[status] Browser disconnected: {browser_id}")

    def touch(self, browser_id: str) -> None:
        """Mark the browser as recently seen. Called on every inbound frame."""
        import time
        self.last_seen[browser_id] = time.time()

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
        self, browser_id: str, command_type: str,
        payload: Any = None, timeout: Optional[float] = None,
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
                status_code=502, detail=f"browser send failed: {e}"
            ) from e

        try:
            response = await asyncio.wait_for(
                future, timeout=timeout if timeout is not None else 10.0
            )
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
            manager.touch(browser_id)
            msg_type = message.get("type")
            if msg_type == "log":
                manager.handle_log(browser_id, message)
            elif msg_type == "RESPONSE":
                manager.handle_response(message)
            elif msg_type == "PING":
                # Keepalive: reply with PONG so the extension's active
                # WebSocket traffic keeps the MV3 service worker awake.
                await websocket.send_text(json.dumps({"type": "PONG"}))
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
    by_text: Optional[str] = None,
    by_label: Optional[str] = None,
    by_placeholder: Optional[str] = None,
    within_css: Optional[str] = None,
    nth: Optional[int] = None,
    exact: bool = False,
    regex: bool = False,
    all: bool = False,
    wait_ms: int = 0,
):
    browser_id, tab_id = _parse_or_400(tab)
    loc = Locator(
        css=css, xpath=xpath, by_text=by_text, by_label=by_label,
        by_placeholder=by_placeholder, within_css=within_css,
        nth=nth, exact=exact, regex=regex,
    )
    _validate_required_locator(loc)
    response = await manager.send_command(
        browser_id, "GET_DOM",
        payload={
            "tab_id": tab_id, "wait_ms": wait_ms, "all": all,
            **_locator_payload(loc),
        },
        timeout=_command_timeout(wait_ms=wait_ms),
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
    _validate_required_locator(req)
    response = await manager.send_command(
        browser_id, "CLICK",
        payload={
            "tab_id": tab_id, "wait_ms": req.wait_ms,
            **_locator_payload(req),
        },
        timeout=_command_timeout(wait_ms=req.wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.post("/type")
async def post_type(req: TypeRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    _validate_required_locator(req)
    response = await manager.send_command(
        browser_id, "TYPE",
        payload={
            "tab_id": tab_id, "wait_ms": req.wait_ms,
            "text": req.text, "clear": req.clear,
            **_locator_payload(req),
        },
        timeout=_command_timeout(wait_ms=req.wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.get("/text")
async def get_text(
    tab: str,
    css: Optional[str] = None,
    xpath: Optional[str] = None,
    by_text: Optional[str] = None,
    by_label: Optional[str] = None,
    by_placeholder: Optional[str] = None,
    within_css: Optional[str] = None,
    nth: Optional[int] = None,
    exact: bool = False,
    regex: bool = False,
    wait_ms: int = 0,
):
    browser_id, tab_id = _parse_or_400(tab)
    loc = Locator(
        css=css, xpath=xpath, by_text=by_text, by_label=by_label,
        by_placeholder=by_placeholder, within_css=within_css,
        nth=nth, exact=exact, regex=regex,
    )
    _validate_locator(loc)  # optional: no locator = whole body
    response = await manager.send_command(
        browser_id, "GET_TEXT",
        payload={
            "tab_id": tab_id, "wait_ms": wait_ms,
            **_locator_payload(loc),
        },
        timeout=_command_timeout(wait_ms=wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.post("/select")
async def post_select(req: SelectRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    _validate_required_locator(req)
    response = await manager.send_command(
        browser_id, "SELECT",
        payload={
            "tab_id": tab_id, "wait_ms": req.wait_ms,
            "value": req.value,
            **_locator_payload(req),
        },
        timeout=_command_timeout(wait_ms=req.wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.post("/navigate")
async def post_navigate(req: NavigateRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    _validate_http_url(req.url)
    response = await manager.send_command(
        browser_id, "NAVIGATE",
        payload={
            "tab_id": tab_id, "url": req.url,
            "wait": req.wait, "timeout_ms": req.timeout_ms,
        },
        timeout=_command_timeout(timeout_ms=req.timeout_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.post("/open")
async def post_open(req: OpenRequest):
    _validate_browser_id(req.browser)
    _validate_http_url(req.url)
    response = await manager.send_command(
        req.browser, "OPEN",
        payload={
            "url": req.url, "wait": req.wait,
            "timeout_ms": req.timeout_ms, "activate": req.activate,
        },
        timeout=_command_timeout(timeout_ms=req.timeout_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    ext_payload = response.get("payload") or {}
    ext_tab_id = ext_payload.get("tab_id")
    return {
        "tab": f"{req.browser}:{ext_tab_id}",
        "url": ext_payload.get("url"),
    }


@app.get("/screenshot")
async def get_screenshot(
    tab: str,
    css: Optional[str] = None,
    xpath: Optional[str] = None,
    by_text: Optional[str] = None,
    by_label: Optional[str] = None,
    by_placeholder: Optional[str] = None,
    within_css: Optional[str] = None,
    nth: Optional[int] = None,
    exact: bool = False,
    regex: bool = False,
    full_page: bool = False,
):
    browser_id, tab_id = _parse_or_400(tab)
    loc = Locator(
        css=css, xpath=xpath, by_text=by_text, by_label=by_label,
        by_placeholder=by_placeholder, within_css=within_css,
        nth=nth, exact=exact, regex=regex,
    )
    _validate_locator(loc)
    response = await manager.send_command(
        browser_id, "SCREENSHOT",
        payload={
            "tab_id": tab_id, "full_page": full_page,
            **_locator_payload(loc),
        },
        timeout=_command_timeout(timeout_ms=15000),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.post("/press")
async def post_press(req: PressRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    _validate_locator(req)
    bad = [m for m in req.modifiers if m not in _VALID_MODIFIERS]
    if bad:
        raise HTTPException(
            status_code=400, detail=f"unknown modifier: {bad[0]}",
        )
    response = await manager.send_command(
        browser_id, "PRESS",
        payload={
            "tab_id": tab_id, "key": req.key,
            "modifiers": req.modifiers, "wait_ms": req.wait_ms,
            **_locator_payload(req),
        },
        timeout=_command_timeout(wait_ms=req.wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.post("/scroll")
async def post_scroll(req: ScrollRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    _validate_required_locator(req)
    response = await manager.send_command(
        browser_id, "SCROLL",
        payload={
            "tab_id": tab_id, "wait_ms": req.wait_ms,
            "block": req.block, "behavior": req.behavior,
            **_locator_payload(req),
        },
        timeout=_command_timeout(wait_ms=req.wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.post("/highlight")
async def post_highlight(req: HighlightRequest):
    browser_id, tab_id = _parse_or_400(req.tab)
    _validate_required_locator(req)
    response = await manager.send_command(
        browser_id, "HIGHLIGHT",
        payload={
            "tab_id": tab_id, "wait_ms": req.wait_ms,
            "color": req.color, "duration_ms": req.duration_ms,
            **_locator_payload(req),
        },
        timeout=_command_timeout(wait_ms=req.wait_ms),
    )
    if not response.get("success"):
        _raise_for_extension_error(response.get("payload"))
    return response.get("payload")


@app.get("/find")
async def get_find(
    tab: str,
    css: Optional[str] = None,
    xpath: Optional[str] = None,
    by_text: Optional[str] = None,
    by_label: Optional[str] = None,
    by_placeholder: Optional[str] = None,
    within_css: Optional[str] = None,
    nth: Optional[int] = None,
    exact: bool = False,
    regex: bool = False,
    wait_ms: int = 0,
):
    browser_id, tab_id = _parse_or_400(tab)
    loc = Locator(
        css=css, xpath=xpath, by_text=by_text, by_label=by_label,
        by_placeholder=by_placeholder, within_css=within_css,
        nth=nth, exact=exact, regex=regex,
    )
    _validate_required_locator(loc)
    response = await manager.send_command(
        browser_id, "FIND",
        payload={"tab_id": tab_id, "wait_ms": wait_ms, **_locator_payload(loc)},
        timeout=_command_timeout(wait_ms=wait_ms),
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
