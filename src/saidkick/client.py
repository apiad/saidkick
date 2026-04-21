import httpx
from typing import List, Dict, Any, Optional


class SaidkickClient:
    def __init__(self, base_url: str = "http://localhost:6992"):
        self.base_url = base_url

    @staticmethod
    def _locator_params(
        css=None, xpath=None, by_text=None, by_label=None, by_placeholder=None,
        within_css=None, nth=None, exact=False, regex=False,
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if css is not None: out["css"] = css
        if xpath is not None: out["xpath"] = xpath
        if by_text is not None: out["by_text"] = by_text
        if by_label is not None: out["by_label"] = by_label
        if by_placeholder is not None: out["by_placeholder"] = by_placeholder
        if within_css is not None: out["within_css"] = within_css
        if nth is not None: out["nth"] = nth
        if exact: out["exact"] = True
        if regex: out["regex"] = True
        return out

    # ---- introspection ----
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
        if grep: params["grep"] = grep
        if browser: params["browser"] = browser
        r = httpx.get(f"{self.base_url}/console", params=params)
        r.raise_for_status()
        return r.json()

    def find(self, tab: str, wait_ms: int = 0, **locator) -> List[Dict[str, Any]]:
        params = {"tab": tab, "wait_ms": wait_ms, **self._locator_params(**locator)}
        r = httpx.get(f"{self.base_url}/find", params=params,
                      timeout=wait_ms / 1000 + 10)
        r.raise_for_status()
        return r.json()

    # ---- navigation ----
    def navigate(self, tab: str, url: str, wait: str = "dom", timeout_ms: int = 15000):
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
    ):
        r = httpx.post(
            f"{self.base_url}/open",
            json={"browser": browser, "url": url, "wait": wait,
                  "timeout_ms": timeout_ms, "activate": activate},
            timeout=timeout_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    # ---- DOM / text / screenshot ----
    def get_dom(self, tab: str, all_matches: bool = False, wait_ms: int = 0, **locator) -> str:
        params = {"tab": tab, "all": all_matches, "wait_ms": wait_ms,
                  **self._locator_params(**locator)}
        r = httpx.get(f"{self.base_url}/dom", params=params,
                      timeout=wait_ms / 1000 + 10)
        r.raise_for_status()
        return r.json()

    def text(self, tab: str, wait_ms: int = 0, **locator) -> str:
        params = {"tab": tab, "wait_ms": wait_ms, **self._locator_params(**locator)}
        r = httpx.get(f"{self.base_url}/text", params=params,
                      timeout=wait_ms / 1000 + 10)
        r.raise_for_status()
        return r.json()

    def screenshot(self, tab: str, full_page: bool = False, **locator) -> Dict[str, Any]:
        params = {"tab": tab, "full_page": "true" if full_page else "false",
                  **self._locator_params(**locator)}
        r = httpx.get(f"{self.base_url}/screenshot", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    # ---- JS execution ----
    def execute(self, tab: str, code: str) -> Any:
        r = httpx.post(f"{self.base_url}/execute", json={"tab": tab, "code": code})
        r.raise_for_status()
        return r.json()

    # ---- interaction ----
    def click(self, tab: str, wait_ms: int = 0, **locator) -> str:
        r = httpx.post(
            f"{self.base_url}/click",
            json={"tab": tab, "wait_ms": wait_ms, **self._locator_params(**locator)},
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def type(
        self, tab: str, text: str,
        clear: bool = False, wait_ms: int = 0, **locator,
    ) -> str:
        r = httpx.post(
            f"{self.base_url}/type",
            json={"tab": tab, "text": text, "clear": clear, "wait_ms": wait_ms,
                  **self._locator_params(**locator)},
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def select(self, tab: str, value: str, wait_ms: int = 0, **locator) -> str:
        r = httpx.post(
            f"{self.base_url}/select",
            json={"tab": tab, "value": value, "wait_ms": wait_ms,
                  **self._locator_params(**locator)},
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()

    def press(
        self, tab: str, key: str,
        modifiers: Optional[List[str]] = None, wait_ms: int = 0, **locator,
    ) -> Dict[str, Any]:
        r = httpx.post(
            f"{self.base_url}/press",
            json={"tab": tab, "key": key, "modifiers": modifiers or [],
                  "wait_ms": wait_ms, **self._locator_params(**locator)},
            timeout=wait_ms / 1000 + 10,
        )
        r.raise_for_status()
        return r.json()
