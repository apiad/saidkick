let socket = null;
let browserId = null;
const SERVER_URL = "ws://localhost:6992/ws";
const logQueue = [];

async function ensureDebuggerAttached(tabId) {
    const target = { tabId };
    await new Promise((resolve, reject) => {
        chrome.debugger.attach(target, "1.3", () => {
            const err = chrome.runtime.lastError;
            if (err && !err.message.includes("already attached")) {
                reject(err);
            } else {
                resolve();
            }
        });
    });
    await new Promise(r =>
        chrome.debugger.sendCommand(target, "Page.enable", {}, r)
    );
    await new Promise(r =>
        chrome.debugger.sendCommand(target, "Runtime.enable", {}, r)
    );
}

function waitForPageEvent(tabId, eventName, timeoutMs) {
    return new Promise((resolve, reject) => {
        let done = false;
        const handler = (source, method) => {
            if (done) return;
            if (source.tabId !== tabId) return;
            if (method !== eventName) return;
            done = true;
            clearTimeout(timer);
            chrome.debugger.onEvent.removeListener(handler);
            resolve();
        };
        const timer = setTimeout(() => {
            if (done) return;
            done = true;
            chrome.debugger.onEvent.removeListener(handler);
            reject(new Error(`navigation timeout after ${timeoutMs}ms`));
        }, timeoutMs);
        chrome.debugger.onEvent.addListener(handler);
    });
}

const PAGE_EVENT_FOR_WAIT = {
    dom: "Page.domContentLoaded",
    full: "Page.loadEventFired",
};

const MODIFIER_BITS = { alt: 1, ctrl: 2, meta: 4, shift: 8 };

function modifiersMask(mods) {
    return (mods || []).reduce((acc, m) => acc | (MODIFIER_BITS[m] || 0), 0);
}

const KEY_TO_CDP = {
    "Enter":      {keyCode: 13, code: "Enter",     text: "\r"},
    "Escape":     {keyCode: 27, code: "Escape"},
    "Tab":        {keyCode: 9,  code: "Tab",       text: "\t"},
    "Backspace":  {keyCode: 8,  code: "Backspace"},
    "ArrowUp":    {keyCode: 38, code: "ArrowUp"},
    "ArrowDown":  {keyCode: 40, code: "ArrowDown"},
    "ArrowLeft":  {keyCode: 37, code: "ArrowLeft"},
    "ArrowRight": {keyCode: 39, code: "ArrowRight"},
    "Home":       {keyCode: 36, code: "Home"},
    "End":        {keyCode: 35, code: "End"},
    "PageUp":     {keyCode: 33, code: "PageUp"},
    "PageDown":   {keyCode: 34, code: "PageDown"},
    "Delete":     {keyCode: 46, code: "Delete"},
    " ":          {keyCode: 32, code: "Space",     text: " "},
};

function cdpKeyParams(key) {
    const mapped = KEY_TO_CDP[key];
    if (mapped) return { ...mapped, key };
    if (key.length === 1) {
        return {
            keyCode: key.toUpperCase().charCodeAt(0),
            code: "Key" + key.toUpperCase(),
            text: key, key,
        };
    }
    return { code: key, key };
}

async function dispatchKey(tabId, key, modifiers) {
    const target = { tabId };
    const base = cdpKeyParams(key);
    const mods = modifiersMask(modifiers);
    await new Promise((resolve, reject) => {
        chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent",
            { type: "keyDown", ...base, modifiers: mods },
            () => chrome.runtime.lastError ? reject(chrome.runtime.lastError) : resolve()
        );
    });
    if (base.text) {
        await new Promise((resolve, reject) => {
            chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent",
                { type: "char", ...base, modifiers: mods },
                () => chrome.runtime.lastError ? reject(chrome.runtime.lastError) : resolve()
            );
        });
    }
    await new Promise((resolve, reject) => {
        chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent",
            { type: "keyUp", ...base, modifiers: mods },
            () => chrome.runtime.lastError ? reject(chrome.runtime.lastError) : resolve()
        );
    });
}

// Tabs opened BEFORE the extension was installed/reloaded have no content
// script yet. sendToContentScript attempts sendMessage; on the classic
// "Receiving end does not exist" error it injects content.js (and main_world.js
// for console mirroring) via chrome.scripting, then retries once.
function sendToContentScript(tabId, msg, requestId, retried = false) {
    let settled = false;
    const reply = (success, payload) => {
        if (settled) return;
        settled = true;
        socket.send(JSON.stringify({
            type: "RESPONSE", id: requestId, success, payload,
        }));
    };

    try {
        chrome.tabs.sendMessage(tabId, msg, (response) => {
            const err = chrome.runtime.lastError;
            if (err) {
                const needsInject = !retried && /Receiving end does not exist|Could not establish connection/.test(err.message || "");
                if (!needsInject) {
                    reply(false, err.message);
                    return;
                }
                // Inject content script + main_world and retry once.
                chrome.scripting.executeScript(
                    { target: { tabId }, files: ["content.js"] },
                    () => {
                        const injErr = chrome.runtime.lastError;
                        if (injErr) {
                            reply(false, `inject content.js failed: ${injErr.message}`);
                            return;
                        }
                        chrome.scripting.executeScript(
                            { target: { tabId }, files: ["main_world.js"], world: "MAIN" },
                            () => {
                                // main_world injection failure is non-fatal; log and keep going.
                                if (chrome.runtime.lastError) {
                                    console.warn("Saidkick: main_world inject:", chrome.runtime.lastError.message);
                                }
                                sendToContentScript(tabId, msg, requestId, true);
                            }
                        );
                    }
                );
                return;
            }
            if (!response) {
                reply(false, "content script returned no response");
                return;
            }
            reply(response.success, response.payload);
        });
    } catch (err) {
        reply(false, err.toString());
    }
}

// Guarded send helper — checks socket state before every outbound frame so
// a racing socket-close doesn't throw synchronously in the middle of a
// response dispatch.
function sendResponse(id, success, payload) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
        console.warn("Saidkick: dropping response for", id, "— socket not open");
        return;
    }
    try {
        socket.send(JSON.stringify({ type: "RESPONSE", id, success, payload }));
    } catch (err) {
        console.warn("Saidkick: socket.send threw for", id, ":", err);
    }
}

function connect() {
    socket = new WebSocket(SERVER_URL);

    socket.onopen = () => {
        const logMsg = {
            type: "log",
            level: "log",
            data: "Saidkick: Background script connected to server",
            timestamp: new Date().toISOString(),
            url: "background"
        };
        socket.send(JSON.stringify(logMsg));
        console.log(logMsg.data);

        // Send queued logs
        while (logQueue.length > 0) {
            socket.send(JSON.stringify(logQueue.shift()));
        }
    };

    socket.onmessage = async (event) => {
        // Binary frames should not occur with our server, but guard so we
        // don't JSON.parse a Blob.
        if (typeof event.data !== "string") return;

        let message;
        try {
            message = JSON.parse(event.data);
        } catch (e) {
            console.warn("Saidkick: malformed WS frame, ignoring:", e.message);
            return;
        }

        const { type, id, payload } = message;

        try {
        if (type === "HELLO") {
            browserId = message.browser_id;
            console.log(`Saidkick: connected as ${browserId}`);
            return;
        }

        if (type === "LIST_TABS") {
            try {
                const rawTabs = await chrome.tabs.query({});
                const tabs = rawTabs
                    .filter(t => t.url && !t.url.startsWith("chrome://")
                        && !t.url.startsWith("chrome-extension://")
                        && !t.url.startsWith("devtools://"))
                    .map(t => ({
                        id: t.id,
                        url: t.url,
                        title: t.title,
                        active: t.active,
                        windowId: t.windowId,
                    }));
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: true, payload: tabs,
                }));
            } catch (err) {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: false, payload: err.toString(),
                }));
            }
            return;
        }

        if (type === "OPEN") {
            const { url, wait: waitMode, timeout_ms, activate } = payload || {};
            try {
                // If we need to wait for a load event, open a blank tab first so we
                // can attach the debugger + subscribe to Page events BEFORE the
                // navigation to the real URL starts. Otherwise we race the event.
                const needWait = waitMode && waitMode !== "none";
                const initialUrl = needWait ? "about:blank" : url;
                const created = await chrome.tabs.create({
                    url: initialUrl, active: Boolean(activate),
                });
                const newTabId = created.id;
                if (needWait) {
                    await ensureDebuggerAttached(newTabId);
                    const ev = PAGE_EVENT_FOR_WAIT[waitMode];
                    if (!ev) throw new Error(`invalid wait mode: ${waitMode}`);
                    // Kick off the real navigation; our listener is already armed.
                    const waitPromise = waitForPageEvent(newTabId, ev, timeout_ms || 15000);
                    await chrome.tabs.update(newTabId, { url });
                    await waitPromise;
                }
                const finalTab = await chrome.tabs.get(newTabId);
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: true,
                    payload: { tab_id: newTabId, url: finalTab.url },
                }));
            } catch (err) {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: false,
                    payload: `tab create failed: ${err.message || err}`,
                }));
            }
            return;
        }

        // All remaining commands target a specific tab_id supplied in payload.
        const tabId = payload?.tab_id;
        if (typeof tabId !== "number") {
            socket.send(JSON.stringify({
                type: "RESPONSE", id, success: false, payload: "tab_id required",
            }));
            return;
        }

        if (type === "NAVIGATE") {
            const { url, wait: waitMode, timeout_ms } = payload || {};
            try {
                const needWait = waitMode && waitMode !== "none";
                if (needWait) {
                    await ensureDebuggerAttached(tabId);
                    const ev = PAGE_EVENT_FOR_WAIT[waitMode];
                    if (!ev) throw new Error(`invalid wait mode: ${waitMode}`);
                    // Arm the listener BEFORE firing the navigation to avoid a race
                    // where the page loads fast enough that Page.domContentLoaded
                    // fires before our listener is registered.
                    const waitPromise = waitForPageEvent(tabId, ev, timeout_ms || 15000);
                    await chrome.tabs.update(tabId, { url });
                    await waitPromise;
                } else {
                    await chrome.tabs.update(tabId, { url });
                }
                const finalTab = await chrome.tabs.get(tabId);
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: true,
                    payload: { url: finalTab.url },
                }));
            } catch (err) {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: false,
                    payload: err.message || String(err),
                }));
            }
            return;
        }

        let tab;
        try {
            tab = await chrome.tabs.get(tabId);
        } catch (err) {
            socket.send(JSON.stringify({
                type: "RESPONSE", id, success: false,
                payload: `tab not found: ${tabId}`,
            }));
            return;
        }

        if (["GET_DOM", "CLICK", "TYPE", "SELECT", "GET_TEXT", "FIND", "SCROLL", "HIGHLIGHT"].includes(type)) {
            sendToContentScript(tab.id, { type, payload }, id);
        } else if (type === "PRESS") {
            try {
                await ensureDebuggerAttached(tab.id);
                const hasLocator = payload.css || payload.xpath || payload.by_text
                    || payload.by_label || payload.by_placeholder;
                if (hasLocator) {
                    const resp = await new Promise(resolve => {
                        chrome.tabs.sendMessage(tab.id, { type: "FOCUS", payload }, resolve);
                    });
                    if (!resp || !resp.success) {
                        socket.send(JSON.stringify({
                            type: "RESPONSE", id, success: false,
                            payload: resp?.payload || "focus failed",
                        }));
                        return;
                    }
                }
                await dispatchKey(tab.id, payload.key, payload.modifiers || []);
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: true,
                    payload: { pressed: payload.key },
                }));
            } catch (err) {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: false,
                    payload: err.message || String(err),
                }));
            }
        } else if (type === "SCREENSHOT") {
            try {
                await ensureDebuggerAttached(tab.id);
                let clip = null;
                const hasLocator = payload.css || payload.xpath || payload.by_text
                    || payload.by_label || payload.by_placeholder;
                if (hasLocator) {
                    const resp = await new Promise(resolve => {
                        chrome.tabs.sendMessage(tab.id, { type: "RESOLVE_RECT", payload }, resolve);
                    });
                    if (!resp || !resp.success) {
                        socket.send(JSON.stringify({
                            type: "RESPONSE", id, success: false,
                            payload: resp?.payload || "resolve rect failed",
                        }));
                        return;
                    }
                    const r = resp.payload;
                    clip = { x: r.x, y: r.y, width: r.width, height: r.height, scale: 1 };
                }
                const cdpParams = { format: "png" };
                if (clip) cdpParams.clip = clip;
                if (payload.full_page) cdpParams.captureBeyondViewport = true;
                const shot = await new Promise((resolve, reject) => {
                    chrome.debugger.sendCommand(
                        { tabId: tab.id }, "Page.captureScreenshot", cdpParams,
                        (result) => chrome.runtime.lastError
                            ? reject(chrome.runtime.lastError) : resolve(result),
                    );
                });
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: true,
                    payload: {
                        png_base64: shot.data,
                        width: clip ? clip.width : 0,
                        height: clip ? clip.height : 0,
                    },
                }));
            } catch (err) {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: false,
                    payload: err.message || String(err),
                }));
            }
        } else if (type === "EXECUTE") {
            try {
                await ensureDebuggerAttached(tab.id);
                const debugTarget = { tabId: tab.id };
                // IIFE-wrap so user `const`/`let` don't collide between calls,
                // and so top-level `await` works. Callers must `return` their value.
                const wrappedCode = `(async () => {\n${payload.code}\n})()`;
                chrome.debugger.sendCommand(
                    debugTarget, "Runtime.evaluate",
                    { expression: wrappedCode, returnByValue: true, awaitPromise: true },
                    (result) => {
                        if (chrome.runtime.lastError) {
                            socket.send(JSON.stringify({
                                type: "RESPONSE", id, success: false,
                                payload: chrome.runtime.lastError.message,
                            }));
                        } else if (result.exceptionDetails) {
                            socket.send(JSON.stringify({
                                type: "RESPONSE", id, success: false,
                                payload: result.exceptionDetails.exception.description,
                            }));
                        } else {
                            socket.send(JSON.stringify({
                                type: "RESPONSE", id, success: true,
                                payload: result.result.value,
                            }));
                        }
                    }
                );
            } catch (error) {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: false,
                    payload: error.message || String(error),
                }));
            }
        }
        } catch (err) {
            // Last-ditch safety net: any uncaught failure in a branch body
            // sends a RESPONSE so the server doesn't 504 in silence.
            sendResponse(id, false, `extension uncaught: ${err.message || err}`);
        }
    };

    socket.onclose = () => {
        console.log("Saidkick: Connection closed, retrying in 5s...");
        setTimeout(connect, 5000);
    };

    socket.onerror = (error) => {
        console.error("Saidkick: WebSocket error", error);
    };
}

function getStatus() {
    const state = socket ? socket.readyState : WebSocket.CLOSED;
    return {
        connected: state === WebSocket.OPEN,
        connecting: state === WebSocket.CONNECTING,
        browserId: browserId,
        serverUrl: SERVER_URL,
    };
}

function forceReconnect() {
    browserId = null;
    if (socket) {
        try {
            socket.close();
        } catch (_) { /* ignore */ }
    }
    // socket.onclose will auto-retry in 5s; trigger immediately instead.
    setTimeout(connect, 50);
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "log") {
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify(message));
        } else {
            logQueue.push(message);
        }
        return;
    }
    if (message.type === "GET_STATUS") {
        sendResponse(getStatus());
        return;
    }
    if (message.type === "RECONNECT") {
        forceReconnect();
        sendResponse({ ok: true });
        return;
    }
});

connect();
