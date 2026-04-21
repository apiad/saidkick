import httpx
from typing import List, Dict, Any, Optional


class SaidkickClient:
    def __init__(self, base_url: str = "http://localhost:6992"):
        self.base_url = base_url

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

    def get_dom(
        self, tab: str, css: Optional[str] = None,
        xpath: Optional[str] = None, all_matches: bool = False,
    ) -> str:
        params: Dict[str, Any] = {"tab": tab, "all": all_matches}
        if css:
            params["css"] = css
        if xpath:
            params["xpath"] = xpath
        r = httpx.get(f"{self.base_url}/dom", params=params)
        r.raise_for_status()
        return r.json()

    def execute(self, tab: str, code: str) -> Any:
        r = httpx.post(
            f"{self.base_url}/execute", json={"tab": tab, "code": code}
        )
        r.raise_for_status()
        return r.json()

    def click(
        self, tab: str,
        css: Optional[str] = None, xpath: Optional[str] = None,
    ) -> str:
        r = httpx.post(
            f"{self.base_url}/click",
            json={"tab": tab, "css": css, "xpath": xpath},
        )
        r.raise_for_status()
        return r.json()

    def type(
        self, tab: str, text: str,
        css: Optional[str] = None, xpath: Optional[str] = None,
        clear: bool = False,
    ) -> str:
        r = httpx.post(
            f"{self.base_url}/type",
            json={
                "tab": tab, "css": css, "xpath": xpath,
                "text": text, "clear": clear,
            },
        )
        r.raise_for_status()
        return r.json()

    def select(
        self, tab: str, value: str,
        css: Optional[str] = None, xpath: Optional[str] = None,
    ) -> str:
        r = httpx.post(
            f"{self.base_url}/select",
            json={"tab": tab, "css": css, "xpath": xpath, "value": value},
        )
        r.raise_for_status()
        return r.json()
