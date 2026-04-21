import httpx
from typing import List, Dict, Any, Optional


class SaidkickClient:
    def __init__(self, base_url: str = "http://localhost:6992"):
        self.base_url = base_url

    # Introspection

    def list_tabs(self, active: bool = False) -> List[Dict[str, Any]]:
        params = {"active": "true" if active else "false"}
        r = httpx.get(f"{self.base_url}/tabs", params=params)
        r.raise_for_status()
        return r.json()

    def get_logs(
        self, limit: int = 100, grep: Optional[str] = None,
        browser: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit}
        if grep:
            params["grep"] = grep
        if browser:
            params["browser"] = browser
        r = httpx.get(f"{self.base_url}/console", params=params)
        r.raise_for_status()
        return r.json()

    # Navigation

    def navigate(
        self, tab: str, url: str,
        wait: str = "dom", timeout_ms: int = 15000,
    ) -> Dict[str, Any]:
        r = httpx.post(
            f"{self.base_url}/navigate",
            json={"tab": tab, "url": url, "wait": wait, "timeout_ms": timeout_ms},
            timeout=timeout_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def open(
        self, browser: str, url: str,
        wait: str = "dom", timeout_ms: int = 15000, activate: bool = False,
    ) -> Dict[str, Any]:
        r = httpx.post(
            f"{self.base_url}/open",
            json={
                "browser": browser, "url": url,
                "wait": wait, "timeout_ms": timeout_ms, "activate": activate,
            },
            timeout=timeout_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    # DOM inspection

    def get_dom(
        self, tab: str, css: Optional[str] = None,
        xpath: Optional[str] = None, all_matches: bool = False,
        wait_ms: int = 0,
    ) -> str:
        params: Dict[str, Any] = {"tab": tab, "all": all_matches, "wait_ms": wait_ms}
        if css:
            params["css"] = css
        if xpath:
            params["xpath"] = xpath
        r = httpx.get(
            f"{self.base_url}/dom", params=params,
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def text(
        self, tab: str, css: Optional[str] = None, wait_ms: int = 0,
    ) -> str:
        params: Dict[str, Any] = {"tab": tab, "wait_ms": wait_ms}
        if css:
            params["css"] = css
        r = httpx.get(
            f"{self.base_url}/text", params=params,
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    # JS execution

    def execute(self, tab: str, code: str) -> Any:
        r = httpx.post(
            f"{self.base_url}/execute", json={"tab": tab, "code": code}
        )
        r.raise_for_status()
        return r.json()

    # Interaction

    def click(
        self, tab: str, css: Optional[str] = None,
        xpath: Optional[str] = None, wait_ms: int = 0,
    ) -> str:
        r = httpx.post(
            f"{self.base_url}/click",
            json={"tab": tab, "css": css, "xpath": xpath, "wait_ms": wait_ms},
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def type(
        self, tab: str, text: str,
        css: Optional[str] = None, xpath: Optional[str] = None,
        clear: bool = False, wait_ms: int = 0,
    ) -> str:
        r = httpx.post(
            f"{self.base_url}/type",
            json={
                "tab": tab, "css": css, "xpath": xpath,
                "text": text, "clear": clear, "wait_ms": wait_ms,
            },
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def select(
        self, tab: str, value: str,
        css: Optional[str] = None, xpath: Optional[str] = None,
        wait_ms: int = 0,
    ) -> str:
        r = httpx.post(
            f"{self.base_url}/select",
            json={
                "tab": tab, "css": css, "xpath": xpath,
                "value": value, "wait_ms": wait_ms,
            },
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()
