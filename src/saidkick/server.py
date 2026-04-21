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


def parse_tab_id(composite: str) -> Tuple[str, int]:
    """Parse 'br-XXXX:N' into (browser_id, tab_id). Raises ValueError on malformed input."""
    if not isinstance(composite, str):
        raise ValueError(f"tab ID must be a string, got {type(composite).__name__}")
    m = _TAB_ID_RE.match(composite)
    if not m:
        raise ValueError(f"invalid tab ID: expected 'br-XXXX:N', got {composite!r}")
    browser_id, tab_str = composite.rsplit(":", 1)
    return browser_id, int(tab_str)

class ExecuteRequest(BaseModel):
    code: str

class SelectorRequest(BaseModel):
    css: Optional[str] = None
    xpath: Optional[str] = None

class TypeRequest(SelectorRequest):
    text: str
    clear: bool = False

class SelectRequest(SelectorRequest):
    value: str

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

    async def send_command(self, command_type: str, payload: Any = None) -> Dict[str, Any]:
        if not self.active_connections:
            logger.warning(f"[warn] Failed to send {command_type}: No active browser connection")
            raise HTTPException(status_code=400, detail="No active browser connection")

        request_id = str(uuid.uuid4())
        future = asyncio.get_event_loop().create_future()
        self.pending_requests[request_id] = future

        # Use the most recent connection
        websocket = self.active_connections[-1]
        logger.info(f"[CMD] Sending {command_type} to browser")
        await websocket.send_text(
            json.dumps({"type": command_type, "id": request_id, "payload": payload})
        )

        try:
            # Wait for response with timeout
            response = await asyncio.wait_for(future, timeout=10.0)
            logger.info(f"[CMD] Received response for {command_type}")
            return response
        except asyncio.TimeoutError as e:
            logger.error(f"[error] Timeout waiting for {command_type}")
            self.pending_requests.pop(request_id, None)
            raise HTTPException(status_code=504, detail="Browser response timeout") from e

    def get_logs(self, limit: int = 100, grep: Optional[str] = None) -> List[Dict[str, Any]]:
        all_logs = list(self.logs)
        if grep:
            import re
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
async def get_console(limit: int = 100, grep: Optional[str] = None):
    return manager.get_logs(limit, grep)

@app.get("/dom")
async def get_dom(css: Optional[str] = None, xpath: Optional[str] = None, all: bool = False):
    response = await manager.send_command("GET_DOM", payload={"css": css, "xpath": xpath, "all": all})
    return response.get("payload")

@app.post("/execute")
async def post_execute(req: ExecuteRequest):
    response = await manager.send_command("EXECUTE", req.code)
    if not response.get("success"):
        error_msg = response.get("payload")
        logger.error(f"[error] Execution failed: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)
    return response.get("payload")

@app.post("/click")
async def post_click(req: SelectorRequest):
    response = await manager.send_command("CLICK", req.model_dump())
    if not response.get("success"):
        raise HTTPException(status_code=500, detail=response.get("payload"))
    return response.get("payload")

@app.post("/type")
async def post_type(req: TypeRequest):
    response = await manager.send_command("TYPE", req.model_dump())
    if not response.get("success"):
        raise HTTPException(status_code=500, detail=response.get("payload"))
    return response.get("payload")

@app.post("/select")
async def post_select(req: SelectRequest):
    response = await manager.send_command("SELECT", req.model_dump())
    if not response.get("success"):
        raise HTTPException(status_code=500, detail=response.get("payload"))
    return response.get("payload")
