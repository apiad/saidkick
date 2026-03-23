import httpx
import json
from typing import List, Dict, Any, Optional

class SaidkickClient:
    def __init__(self, base_url: str = "http://localhost:6992"):
        self.base_url = base_url

    def get_logs(self, limit: int = 100, grep: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"limit": limit}
        if grep:
            params["grep"] = grep
        response = httpx.get(f"{self.base_url}/console", params=params)
        response.raise_for_status()
        return response.json()

    def get_dom(self, css: Optional[str] = None, xpath: Optional[str] = None, all_matches: bool = False) -> str:
        params = {"all": all_matches}
        if css:
            params["css"] = css
        if xpath:
            params["xpath"] = xpath
        response = httpx.get(f"{self.base_url}/dom", params=params)
        response.raise_for_status()
        return response.json()

    def execute(self, code: str) -> Any:
        response = httpx.post(f"{self.base_url}/execute", json={"code": code})
        response.raise_for_status()
        return response.json()

    def click(self, css: Optional[str] = None, xpath: Optional[str] = None) -> str:
        payload = {"css": css, "xpath": xpath}
        response = httpx.post(f"{self.base_url}/click", json=payload)
        response.raise_for_status()
        return response.json()

    def type(self, text: str, css: Optional[str] = None, xpath: Optional[str] = None, clear: bool = False) -> str:
        payload = {"css": css, "xpath": xpath, "text": text, "clear": clear}
        response = httpx.post(f"{self.base_url}/type", json=payload)
        response.raise_for_status()
        return response.json()

    def select(self, value: str, css: Optional[str] = None, xpath: Optional[str] = None) -> str:
        payload = {"css": css, "xpath": xpath, "value": value}
        response = httpx.post(f"{self.base_url}/select", json=payload)
        response.raise_for_status()
        return response.json()
