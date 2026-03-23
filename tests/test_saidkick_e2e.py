import os
import subprocess
import time
import pytest
import httpx
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn
import threading

# Constants
SAIDKICK_PORT = 6992
TEST_SERVER_PORT = 8088
BASE_URL = f"http://localhost:{SAIDKICK_PORT}"
EXTENSION_PATH = Path("src/saidkick/extension").absolute()
TEST_HTML_PATH = Path("tests/assets/test.html").absolute()

@pytest.fixture(scope="module")
def test_page_server():
    """Serve test.html over HTTP."""
    app = FastAPI()
    @app.get("/", response_class=HTMLResponse)
    def read_item():
        return TEST_HTML_PATH.read_text()
    
    config = uvicorn.Config(app, host="127.0.0.1", port=TEST_SERVER_PORT, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run)
    thread.start()
    
    # Wait for server
    time.sleep(1)
    yield f"http://localhost:{TEST_SERVER_PORT}/"
    
    server.should_exit = True
    thread.join()

@pytest.fixture(scope="module")
def saidkick_server():
    """Launch Saidkick server."""
    # Ensure any old process is killed
    subprocess.run(["pkill", "-f", "saidkick start"], capture_output=True)
    
    process = subprocess.Popen(
        ["uv", "run", "saidkick", "start", "--port", str(SAIDKICK_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    # Wait for server to be ready
    start_time = time.time()
    while time.time() - start_time < 10:
        try:
            httpx.get(f"{BASE_URL}/console")
            break
        except httpx.ConnectError:
            time.sleep(0.5)
    else:
        process.terminate()
        raise RuntimeError("Saidkick server failed to start")
    
    yield process
    
    process.terminate()
    process.wait()

@pytest.fixture(scope="module")
def chrome_browser(saidkick_server, test_page_server):
    """Launch Chrome with Saidkick extension."""
    user_data_dir = "/tmp/saidkick-e2e-profile"
    subprocess.run(["rm", "-rf", user_data_dir])
    
    chrome_cmd = [
        "google-chrome-stable",
        f"--load-extension={EXTENSION_PATH}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        test_page_server
    ]
    
    process = subprocess.Popen(chrome_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Wait for extension to connect to server
    start_time = time.time()
    while time.time() - start_time < 20:
        try:
            response = httpx.get(f"{BASE_URL}/console")
            logs = [str(log.get("data")) for log in response.json()]
            if any("Background script connected" in l or "Content script connected" in l for l in logs):
                break
        except httpx.ConnectError:
            pass
        time.sleep(1)
    else:
        print("WARNING: Extension connection log not found, continuing anyway...")
    
    # Wait for page to load and logs to be sent
    time.sleep(2)
    
    yield process
    
    process.terminate()
    process.wait()
    subprocess.run(["rm", "-rf", user_data_dir])

@pytest.mark.e2e
def test_saidkick_full_flow(chrome_browser):
    """Verify logs, exec, click, type, and select."""
    
    # 1. Verify Initial Logs
    response = httpx.get(f"{BASE_URL}/console")
    logs = response.json()
    print(f"DEBUG: Fetched logs: {logs}")
    assert any("Page loaded" in str(log.get("data")) for log in logs)

    # 2. Test Exec (JS)
    exec_response = httpx.post(f"{BASE_URL}/execute", json={"code": "document.title"})
    assert exec_response.status_code == 200
    assert exec_response.json() == "Saidkick E2E Test Page"

    # 3. Test Type
    type_response = httpx.post(f"{BASE_URL}/type", json={
        "css": "#type-here",
        "text": "E2E Testing",
        "clear": True
    })
    assert type_response.status_code == 200
    
    # Verify effect in DOM via Exec
    status_text = httpx.post(f"{BASE_URL}/execute", json={"code": "document.getElementById('status').innerText"}).json()
    assert status_text == "E2E Testing"

    # 4. Test Select
    select_response = httpx.post(f"{BASE_URL}/select", json={
        "css": "#select-me",
        "value": "opt2"
    })
    assert select_response.status_code == 200
    
    status_text = httpx.post(f"{BASE_URL}/execute", json={"code": "document.getElementById('status').innerText"}).json()
    assert status_text == "Selected: opt2"

    # 5. Test Click
    click_response = httpx.post(f"{BASE_URL}/click", json={"css": "#click-me"})
    assert click_response.status_code == 200
    
    status_text = httpx.post(f"{BASE_URL}/execute", json={"code": "document.getElementById('status').innerText"}).json()
    assert status_text == "Clicked"

    # 6. Test DOM anchoring
    dom_response = httpx.get(f"{BASE_URL}/dom?css=#status")
    assert dom_response.status_code == 200
    assert "Clicked" in dom_response.json()

    # 7. Verify Log Filtering (Limit & Grep)
    # Trigger a specific log
    httpx.post(f"{BASE_URL}/execute", json={"code": "console.log('Final Verification')"})
    
    grep_response = httpx.get(f"{BASE_URL}/console?grep=Final")
    assert len(grep_response.json()) >= 1
    assert "Final Verification" in str(grep_response.json()[-1].get("data"))
