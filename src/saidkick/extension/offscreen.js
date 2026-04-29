// Saidkick offscreen document: holds the persistent WebSocket and
// proxies command frames to the service worker via a long-lived Port.
//
// Lifecycle:
//   - On load: open WS to ws://localhost:6992/ws and connect Port to SW.
//   - On WS message: forward to SW via Port (except PING/PONG, handled here).
//   - On Port message from SW: forward to WS as a RESPONSE frame.
//   - On WS close: retry in 1s.
//   - On Port disconnect (SW died): re-open Port. WS stays alive.
//
// PING/PONG cadence: 60s (slower than the legacy 20s — SW idle is no
// longer the constraint). Used as a half-dead-connection detector: if
// no PONG within 90s of a PING, force a WS reconnect.

const SERVER_URL = "ws://localhost:6992/ws";
const PING_INTERVAL_MS = 60_000;
const PONG_TIMEOUT_MS = 90_000;
const RECONNECT_DELAY_MS = 1_000;

let socket = null;
let port = null;
let pingInterval = null;
let lastPongAt = 0;
let logQueue = [];
const LOG_QUEUE_MAX = 200;

function ensurePort() {
    if (port) return port;
    port = chrome.runtime.connect({ name: "saidkick-cmd" });
    port.onMessage.addListener((msg) => {
        // SW sends two kinds of message: RESPONSE frames (forward to WS)
        // and STATUS frames (cached locally; not forwarded). Today the SW
        // only sends RESPONSE frames; STATUS is offscreen→SW only.
        if (msg && msg.type === "RESPONSE" && socket && socket.readyState === WebSocket.OPEN) {
            try { socket.send(JSON.stringify(msg)); }
            catch (e) { console.warn("offscreen: WS send failed:", e); }
        } else if (msg && msg.type === "log" && socket && socket.readyState === WebSocket.OPEN) {
            // SW-originated log (e.g. from a command handler).
            try { socket.send(JSON.stringify(msg)); }
            catch (e) { /* drop */ }
        }
    });
    port.onDisconnect.addListener(() => {
        port = null;
        // Don't immediately re-open; next inbound WS frame will trigger
        // ensurePort() and wake the SW.
    });
    return port;
}

function pushStatusToSW() {
    // Push a STATUS frame to the SW so the popup's GET_STATUS reply is
    // immediate (no offscreen round trip needed).
    const p = ensurePort();
    try {
        p.postMessage({
            type: "STATUS",
            connected: socket?.readyState === WebSocket.OPEN,
            connecting: socket?.readyState === WebSocket.CONNECTING,
        });
    } catch (e) {
        // Port may have raced disconnect; rely on next status push.
    }
}

function connect() {
    socket = new WebSocket(SERVER_URL);

    socket.onopen = () => {
        console.log("Saidkick offscreen: WS connected");
        pushStatusToSW();
        // Drain any queued logs.
        while (logQueue.length > 0) {
            try { socket.send(JSON.stringify(logQueue.shift())); }
            catch (_) { break; }
        }
        // Start PING heartbeat.
        if (pingInterval) clearInterval(pingInterval);
        lastPongAt = Date.now();
        pingInterval = setInterval(() => {
            if (!socket || socket.readyState !== WebSocket.OPEN) return;
            // Half-dead detection: if PONG hasn't arrived within
            // PONG_TIMEOUT_MS, force a reconnect.
            if (Date.now() - lastPongAt > PONG_TIMEOUT_MS) {
                console.warn("Saidkick offscreen: PONG timeout, forcing reconnect");
                try { socket.close(); } catch (_) { /* ignore */ }
                return;
            }
            try { socket.send(JSON.stringify({ type: "PING" })); }
            catch (_) { /* racing close */ }
        }, PING_INTERVAL_MS);
    };

    socket.onmessage = (event) => {
        if (typeof event.data !== "string") return;
        let msg;
        try { msg = JSON.parse(event.data); }
        catch (e) { return; }

        if (msg.type === "PONG") {
            lastPongAt = Date.now();
            return;
        }
        // HELLO is forwarded to SW so it can cache the browser_id for popup.
        // All other frames (LIST_TABS, OPEN, NAVIGATE, ...) are commands.
        try {
            ensurePort().postMessage(msg);
        } catch (e) {
            console.warn("Saidkick offscreen: port.postMessage failed:", e);
        }
    };

    socket.onclose = () => {
        console.log(`Saidkick offscreen: WS closed, retrying in ${RECONNECT_DELAY_MS}ms`);
        if (pingInterval) { clearInterval(pingInterval); pingInterval = null; }
        pushStatusToSW();
        setTimeout(connect, RECONNECT_DELAY_MS);
    };

    socket.onerror = (err) => {
        console.error("Saidkick offscreen: WS error", err);
    };
}

// Receive logs from the SW that need queuing across WS reconnects.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message && message.type === "log") {
        if (socket && socket.readyState === WebSocket.OPEN) {
            try { socket.send(JSON.stringify(message)); } catch (_) { /* drop */ }
        } else {
            logQueue.push(message);
            while (logQueue.length > LOG_QUEUE_MAX) logQueue.shift();
        }
    }
});

connect();
