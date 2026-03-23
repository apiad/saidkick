import asyncio
import json
import logging
import uuid
from collections import deque
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

# Configure logger
logger = logging.getLogger("saidkick")

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
        self.active_connections: List[WebSocket] = []
        self.pending_requests: Dict[str, asyncio.Future] = {}

    async def add_connection(self, websocket: WebSocket):
        await websocket.accept()
        client_host = websocket.client.host if websocket.client else "unknown"
        logger.info(f"[status] Tab connected: {client_host}")
        self.active_connections.append(websocket)
        return client_host

    def remove_connection(self, websocket: WebSocket, client_host: str):
        logger.info(f"[status] Tab disconnected: {client_host}")
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    def handle_log(self, message: Dict[str, Any]):
        level = message.get("level", "info").upper()
        content = message.get("data")
        logger.info(f"[BROWSER] {level}: {content}")
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
    client_host = await manager.add_connection(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            if message.get("type") == "log":
                manager.handle_log(message)
            elif message.get("type") == "RESPONSE":
                manager.handle_response(message)
    except WebSocketDisconnect:
        manager.remove_connection(websocket, client_host)
    except Exception as e:
        logger.error(f"[error] WebSocket error: {e}")
        manager.remove_connection(websocket, client_host)

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
