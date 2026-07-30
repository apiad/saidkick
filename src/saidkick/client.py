"""Synchronous HTTP client for the saidkick daemon.

Everything the CLI does is available here, so scripts can drive a browser
without shelling out.
"""

import base64
import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:6992"

#: Point the CLI and client at a daemon elsewhere (another port, another host).
BASE_URL_ENV = "SAIDKICK_URL"

LOCATOR_KEYS = (
    "css",
    "xpath",
    "by_text",
    "by_label",
    "by_placeholder",
    "by_role",
    "within_css",
    "nth",
    "exact",
    "regex",
    "wait_ms",
)


class SaidkickClient:
    def __init__(
        self, base_url: str | None = None, timeout: float = 30.0, token: str | None = None
    ):
        resolved = base_url or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL
        self.base_url = resolved.rstrip("/")
        self.token = token or os.environ.get("SAIDKICK_TOKEN")
        headers = {"X-Saidkick-Token": self.token} if self.token else {}
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, headers=headers)

    # -- plumbing ---------------------------------------------------------

    @staticmethod
    def _locator(kwargs: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in kwargs.items() if k in LOCATOR_KEYS and v is not None}

    def _json(self, response: httpx.Response) -> Any:
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()

    # -- health and contexts ----------------------------------------------

    def health(self) -> dict:
        return self._json(self._client.get("/health"))

    def list_contexts(self) -> list[dict]:
        return self._json(self._client.get("/contexts"))

    def open_context(
        self, profile: str | None = None, mode: str = "ephemeral", viewport: dict | None = None
    ) -> dict:
        body: dict[str, Any] = {"mode": mode}
        if profile:
            body["profile"] = profile
        if viewport:
            body["viewport"] = viewport
        return self._json(self._client.post("/contexts", json=body))

    def runlog(self, context: str | None = None, limit: int = 100) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if context:
            params["context"] = context
        return self._json(self._client.get("/runlog", params=params))

    def list_profiles(self) -> list[dict]:
        return self._json(self._client.get("/profiles"))

    def save_profile(self, context: str, name: str) -> dict:
        return self._json(
            self._client.post(f"/contexts/{context}/save-profile", json={"name": name})
        )

    def close_context(self, context: str) -> dict:
        return self._json(self._client.delete(f"/contexts/{context}"))

    # -- tabs --------------------------------------------------------------

    def list_tabs(self, context: str) -> list[dict]:
        return self._json(self._client.get(f"/contexts/{context}/tabs"))

    def open_tab(self, context: str, url: str | None = None, wait: str = "load") -> dict:
        return self._json(
            self._client.post(f"/contexts/{context}/tabs", json={"url": url, "wait": wait})
        )

    def close_tab(self, tab: str) -> dict:
        return self._json(self._client.delete(f"/tabs/{tab}"))

    def navigate(self, tab: str, url: str, wait: str = "load") -> dict:
        return self._json(
            self._client.post(f"/tabs/{tab}/navigate", json={"url": url, "wait": wait})
        )

    def quick(self, url: str) -> str:
        """Open an ephemeral context and a tab in one call; return the tab id."""
        ctx = self.open_context()
        return self.open_tab(ctx["id"], url)["id"]

    # -- reading -----------------------------------------------------------

    def snapshot(self, tab: str, mode: str = "aria", within_css: str | None = None) -> str:
        params: dict[str, Any] = {"mode": mode}
        if within_css:
            params["within_css"] = within_css
        return self._json(self._client.get(f"/tabs/{tab}/snapshot", params=params))["snapshot"]

    def find(self, tab: str, **locator: Any) -> list[dict]:
        return self._json(self._client.post(f"/tabs/{tab}/find", json=self._locator(locator)))[
            "result"
        ]

    def screenshot(self, tab: str, full_page: bool = False) -> bytes:
        response = self._client.get(f"/tabs/{tab}/screenshot", params={"full_page": full_page})
        response.raise_for_status()
        return response.content

    # -- acting ------------------------------------------------------------

    def _act(self, tab: str, action: str, extra: dict, locator: dict) -> Any:
        body = {**self._locator(locator), **extra}
        return self._json(self._client.post(f"/tabs/{tab}/{action}", json=body))

    def click(self, tab: str, button: str = "left", **locator: Any) -> Any:
        """Click an element. `button="right"` opens a context menu — the only
        way to reach an affordance that has no left-click equivalent."""
        return self._act(tab, "click", {"button": button}, locator)

    def type(self, tab: str, text: str, submit: bool = False, **locator: Any) -> Any:
        return self._act(tab, "type", {"text": text, "submit": submit}, locator)

    def select(self, tab: str, values: list[str], **locator: Any) -> Any:
        return self._act(tab, "select", {"values": values}, locator)

    def hover(self, tab: str, **locator: Any) -> Any:
        return self._act(tab, "hover", {}, locator)

    def scroll(self, tab: str, **locator: Any) -> Any:
        return self._act(tab, "scroll", {}, locator)

    def highlight(
        self, tab: str, color: str = "#ef4444", duration_ms: int = 2000, **locator: Any
    ) -> Any:
        return self._act(tab, "highlight", {"color": color, "duration_ms": duration_ms}, locator)

    def press(
        self, tab: str, key: str, modifiers: list[str] | None = None, **locator: Any
    ) -> Any:
        body = {**self._locator(locator), "key": key, "modifiers": modifiers}
        return self._json(self._client.post(f"/tabs/{tab}/press", json=body))

    # -- control -----------------------------------------------------------

    def requests(self) -> list[dict]:
        return self._json(self._client.get("/requests"))

    def events(self, context: str, since: int = 0) -> list[dict]:
        return self._json(
            self._client.get(f"/contexts/{context}/events", params={"since": since})
        )

    def pins(self, context: str) -> list[dict]:
        return self._json(self._client.get(f"/contexts/{context}/pins"))

    def read_pin(self, handle: str, screenshot: bool = False) -> dict:
        return self._json(
            self._client.get(f"/pins/{handle}", params={"screenshot": screenshot})
        )


def b64decode(data: str) -> bytes:
    return base64.b64decode(data)
