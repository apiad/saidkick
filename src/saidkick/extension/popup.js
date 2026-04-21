const dot = document.getElementById("dot");
const statusText = document.getElementById("status-text");
const browserIdEl = document.getElementById("browser-id");
const serverUrlEl = document.getElementById("server-url");
const reconnectBtn = document.getElementById("reconnect");

const STATE_CLASS = ["ok", "bad", "pending"];

function setState(cls, text) {
    STATE_CLASS.forEach(c => {
        dot.classList.remove(c);
        statusText.classList.remove(c);
    });
    dot.classList.add(cls);
    statusText.classList.add(cls);
    statusText.textContent = text;
}

async function refresh() {
    try {
        const res = await chrome.runtime.sendMessage({ type: "GET_STATUS" });
        if (!res) {
            setState("bad", "no response");
            return;
        }
        if (res.connected) {
            setState("ok", "connected");
        } else if (res.connecting) {
            setState("pending", "connecting");
        } else {
            setState("bad", "disconnected");
        }
        browserIdEl.textContent = res.browserId || "—";
        serverUrlEl.textContent = res.serverUrl || "—";
    } catch (err) {
        setState("bad", "error");
        browserIdEl.textContent = "—";
        serverUrlEl.textContent = String(err.message || err);
    }
}

reconnectBtn.addEventListener("click", async () => {
    reconnectBtn.disabled = true;
    setState("pending", "reconnecting");
    try {
        await chrome.runtime.sendMessage({ type: "RECONNECT" });
    } catch (_) { /* fall through to refresh */ }
    setTimeout(() => {
        refresh();
        reconnectBtn.disabled = false;
    }, 400);
});

refresh();
// Light polling while the popup is open.
const poll = setInterval(refresh, 1500);
window.addEventListener("unload", () => clearInterval(poll));
