let browserId = null;
let previousBrowserId = null;  // for "reconnected as new br-XXXX" popup hint
const attachedTabs = new Set();  // tabIds we've attached the debugger to
const RECONNECT_ALARM = "saidkick-reconnect-watchdog";
const OFFSCREEN_URL = "offscreen.html";

async function ensureOffscreen() {
    const has = await chrome.offscreen.hasDocument();
    if (has) return;
    await chrome.offscreen.createDocument({
        url: OFFSCREEN_URL,
        reasons: ["WORKERS"],
        justification: "Persistent WebSocket to localhost saidkick server outlives SW idle.",
    });
}

async function ensureDebuggerAttached(tabId) {
    const target = { tabId };
    await new Promise((resolve, reject) => {
        chrome.debugger.attach(target, "1.3", () => {
            const err = chrome.runtime.lastError;
            if (err && !err.message.includes("already attached")) {
                reject(err);
            } else {
                attachedTabs.add(tabId);
                resolve();
            }
        });
    });
    await new Promise((resolve, reject) =>
        chrome.debugger.sendCommand(target, "Page.enable", {}, () =>
            chrome.runtime.lastError ? reject(chrome.runtime.lastError) : resolve()
        )
    );
    await new Promise((resolve, reject) =>
        chrome.debugger.sendCommand(target, "Runtime.enable", {}, () =>
            chrome.runtime.lastError ? reject(chrome.runtime.lastError) : resolve()
        )
    );
}

// Resolve `by_role` (+ optional `by_text` for the accessible name) to a
// unique CSS selector for the DOM node, via CDP's Accessibility domain.
// Everything else in the locator stack lives in content.js and speaks CSS,
// so we do the AXTree resolution background-side and then rewrite the payload
// to use `css:` with the computed selector.
async function resolveByRole(tabId, locator) {
    await ensureDebuggerAttached(tabId);
    const target = { tabId };

    const tree = await new Promise((resolve, reject) => {
        chrome.debugger.sendCommand(target, "Accessibility.getFullAXTree", {}, (result) => {
            chrome.runtime.lastError ? reject(chrome.runtime.lastError) : resolve(result);
        });
    });
    const nodes = tree.nodes || [];
    const wantRole = (locator.by_role || "").toLowerCase();
    const nameNeedle = locator.by_text;

    const nameMatches = (name) => {
        if (nameNeedle == null) return true;
        if (locator.regex) {
            try { return new RegExp(nameNeedle).test(name); }
            catch (e) { return false; }
        }
        if (locator.exact) return name === nameNeedle;
        return (name || "").toLowerCase().includes(nameNeedle.toLowerCase());
    };

    const candidates = nodes.filter(n => {
        if (n.ignored) return false;
        const role = (n.role && n.role.value || "").toLowerCase();
        if (role !== wantRole) return false;
        const name = (n.name && n.name.value) || "";
        return nameMatches(name) && n.backendDOMNodeId != null;
    });

    if (candidates.length === 0) {
        throw new Error("element not found");
    }
    // If nth is provided, pick; otherwise require uniqueness.
    let picks;
    if (locator.nth != null) {
        const c = candidates[locator.nth];
        if (!c) throw new Error("element not found");
        picks = [c];
    } else if (candidates.length > 1) {
        throw new Error(`Ambiguous locator: found ${candidates.length} matches`);
    } else {
        picks = candidates;
    }

    const pick = picks[0];
    // Resolve the backend node into a Runtime RemoteObject so we can call a
    // function on it that computes a unique CSS path.
    const resolved = await new Promise((resolve, reject) => {
        chrome.debugger.sendCommand(target, "DOM.resolveNode",
            { backendNodeId: pick.backendDOMNodeId },
            (r) => chrome.runtime.lastError ? reject(chrome.runtime.lastError) : resolve(r)
        );
    });
    const objectId = resolved.object.objectId;

    const pathFn = `function() {
        const el = this;
        if (!(el instanceof Element)) return "";
        if (el.id) return "#" + CSS.escape(el.id);
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === 1 && cur !== document.body) {
            let part = cur.tagName.toLowerCase();
            const parent = cur.parentElement;
            if (parent) {
                const sibs = Array.from(parent.children).filter(s => s.tagName === cur.tagName);
                if (sibs.length > 1) part += ":nth-of-type(" + (sibs.indexOf(cur) + 1) + ")";
            }
            parts.unshift(part);
            cur = parent;
        }
        return "body > " + parts.join(" > ");
    }`;
    const call = await new Promise((resolve, reject) => {
        chrome.debugger.sendCommand(target, "Runtime.callFunctionOn",
            { objectId, functionDeclaration: pathFn, returnByValue: true },
            (r) => chrome.runtime.lastError ? reject(chrome.runtime.lastError) : resolve(r)
        );
    });
    const selector = call && call.result && call.result.value;
    if (!selector) throw new Error("could not compute selector for AXTree match");
    return selector;
}

// Detach when a debugged tab is closed so we don't leak state and so the
// user doesn't see "Saidkick started debugging this browser" banners
// accumulate across tabs that no longer exist.
chrome.tabs.onRemoved.addListener((tabId) => {
    if (!attachedTabs.has(tabId)) return;
    attachedTabs.delete(tabId);
    try {
        chrome.debugger.detach({ tabId }, () => {
            // Ignore chrome.runtime.lastError — the tab is gone; the
            // detach would have happened automatically.
            void chrome.runtime.lastError;
        });
    } catch (_) { /* ignore */ }
});

const VALID_WAIT_MODES = ["dom", "full"];

// Wait for a tab's navigation to reach the requested readiness. Uses
// chrome.tabs.onUpdated (the `tabs` permission) plus a document.readyState poll
// via chrome.scripting (the `scripting` permission) for "dom" — the status API
// only surfaces "loading"/"complete", not domContentLoaded. This replaces the
// old chrome.debugger Page-event approach, whose CDP events were unreliable
// across a tabs.update navigation (the page execution context is replaced, so
// Page.domContentLoaded / Page.loadEventFired were frequently missed → the
// 15s "navigation timeout" even on instant localhost pages).
//
// Call this BEFORE issuing chrome.tabs.update so the onUpdated listener is
// registered in time to catch the "loading" event for the new URL.
function waitForTabLoad(tabId, mode, timeoutMs) {
    return new Promise((resolve, reject) => {
        let done = false;
        let poll = null;
        const finish = (err) => {
            if (done) return;
            done = true;
            clearTimeout(timer);
            if (poll) clearInterval(poll);
            chrome.tabs.onUpdated.removeListener(onUpdated);
            err ? reject(err) : resolve();
        };
        const timer = setTimeout(
            () => finish(new Error(`navigation timeout after ${timeoutMs}ms`)),
            timeoutMs
        );
        const startDomPoll = () => {
            if (poll) return;
            poll = setInterval(async () => {
                try {
                    const res = await chrome.scripting.executeScript({
                        target: { tabId },
                        func: () => document.readyState,
                    });
                    const rs = res && res[0] && res[0].result;
                    if (rs === "interactive" || rs === "complete") finish();
                } catch (_) {
                    // Page not injectable yet (mid-navigation); keep polling.
                }
            }, 100);
        };
        const onUpdated = (id, changeInfo) => {
            if (id !== tabId) return;
            if (changeInfo.status === "complete") return finish();
            if (mode === "dom" && changeInfo.status === "loading") startDomPoll();
        };
        chrome.tabs.onUpdated.addListener(onUpdated);
    });
}

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
function sendToContentScript(tabId, msg, respond, retried = false) {
    let settled = false;
    const reply = (success, payload) => {
        if (settled) return;
        settled = true;
        respond(success, payload);
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
                                sendToContentScript(tabId, msg, respond, true);
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

async function dispatchCommand(message, respond) {
    const { type, id, payload } = message;
    try {
        if (type === "LIST_TABS") {
            try {
                const rawTabs = await chrome.tabs.query({});
                const tabs = rawTabs
                    .filter(t => t.url && !t.url.startsWith("chrome://")
                        && !t.url.startsWith("chrome-extension://")
                        && !t.url.startsWith("devtools://"))
                    .map(t => ({
                        id: t.id, url: t.url, title: t.title,
                        active: t.active, windowId: t.windowId,
                    }));
                respond(true, tabs);
            } catch (err) {
                respond(false, err.toString());
            }
            return;
        }

        if (type === "OPEN") {
            const { url, wait: waitMode, timeout_ms, activate } = payload || {};
            try {
                const needWait = waitMode && waitMode !== "none";
                const initialUrl = needWait ? "about:blank" : url;
                const created = await chrome.tabs.create({
                    url: initialUrl, active: Boolean(activate),
                });
                const newTabId = created.id;
                if (needWait) {
                    if (!VALID_WAIT_MODES.includes(waitMode)) {
                        throw new Error(`invalid wait mode: ${waitMode}`);
                    }
                    // Register the listener before navigating so we catch the
                    // "loading" event for the new URL.
                    const waitPromise = waitForTabLoad(newTabId, waitMode, timeout_ms || 15000);
                    await chrome.tabs.update(newTabId, { url });
                    await waitPromise;
                }
                const finalTab = await chrome.tabs.get(newTabId);
                respond(true, { tab_id: newTabId, url: finalTab.url });
            } catch (err) {
                respond(false, `tab create failed: ${err.message || err}`);
            }
            return;
        }

        const tabId = payload?.tab_id;
        if (typeof tabId !== "number") {
            respond(false, "tab_id required");
            return;
        }

        if (type === "CLOSE") {
            try {
                // chrome.tabs.onRemoved (above) detaches the debugger and cleans
                // up attachedTabs automatically once the tab is gone.
                await chrome.tabs.remove(tabId);
                respond(true, { closed: tabId });
            } catch (err) {
                respond(false, err.message || String(err));
            }
            return;
        }

        if (type === "NAVIGATE") {
            const { url, wait: waitMode, timeout_ms } = payload || {};
            try {
                const needWait = waitMode && waitMode !== "none";
                if (needWait) {
                    if (!VALID_WAIT_MODES.includes(waitMode)) {
                        throw new Error(`invalid wait mode: ${waitMode}`);
                    }
                    const waitPromise = waitForTabLoad(tabId, waitMode, timeout_ms || 15000);
                    await chrome.tabs.update(tabId, { url });
                    await waitPromise;
                } else {
                    await chrome.tabs.update(tabId, { url });
                }
                const finalTab = await chrome.tabs.get(tabId);
                respond(true, { url: finalTab.url });
            } catch (err) {
                respond(false, err.message || String(err));
            }
            return;
        }

        let tab;
        try {
            tab = await chrome.tabs.get(tabId);
        } catch (err) {
            respond(false, `tab not found: ${tabId}`);
            return;
        }

        if (["GET_DOM", "CLICK", "TYPE", "SELECT", "GET_TEXT", "FIND", "SCROLL", "HIGHLIGHT",
             "SET_MIRROR", "GET_MIRROR"].includes(type)) {
            let forwardPayload = payload;
            if (payload && payload.by_role) {
                try {
                    const selector = await resolveByRole(tab.id, payload);
                    forwardPayload = { ...payload, css: selector, by_role: null, by_text: null };
                } catch (err) {
                    respond(false, err.message || String(err));
                    return;
                }
            }
            sendToContentScript(tab.id, { type, payload: forwardPayload }, respond);
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
                        respond(false, resp?.payload || "focus failed");
                        return;
                    }
                }
                await dispatchKey(tab.id, payload.key, payload.modifiers || []);
                respond(true, { pressed: payload.key });
            } catch (err) {
                respond(false, err.message || String(err));
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
                        respond(false, resp?.payload || "resolve rect failed");
                        return;
                    }
                    const r = resp.payload;
                    clip = { x: r.x, y: r.y, width: r.width, height: r.height, scale: 1 };
                }
                const cdpParams = { format: "png" };
                if (clip) cdpParams.clip = clip;
                if (payload.full_page) {
                    cdpParams.captureBeyondViewport = true;
                    const cap = payload.max_height_px || 10000;
                    if (!clip) {
                        cdpParams.clip = {
                            x: 0, y: 0,
                            width: document.documentElement?.scrollWidth || 1920,
                            height: Math.min(
                                document.documentElement?.scrollHeight || cap, cap
                            ),
                            scale: 1,
                        };
                    }
                }
                const shot = await new Promise((resolve, reject) => {
                    chrome.debugger.sendCommand(
                        { tabId: tab.id }, "Page.captureScreenshot", cdpParams,
                        (result) => chrome.runtime.lastError
                            ? reject(chrome.runtime.lastError) : resolve(result),
                    );
                });
                respond(true, {
                    png_base64: shot.data,
                    width: clip ? clip.width : 0,
                    height: clip ? clip.height : 0,
                });
            } catch (err) {
                respond(false, err.message || String(err));
            }
        } else if (type === "EXECUTE") {
            try {
                await ensureDebuggerAttached(tab.id);
                const debugTarget = { tabId: tab.id };
                // Expose caller args to the user code as a named `args` array
                // (a rest parameter — the `arguments` object is unreliable at
                // the top level of a CDP Runtime.evaluate). JSON is valid JS, so
                // inlining the serialized array is injection-safe. No args →
                // (...[]) → args === [], equivalent to the old zero-arg IIFE.
                const argsJson = JSON.stringify(payload.args || []);
                const wrappedCode =
                    `(async function (...args) {\n${payload.code}\n})(...${argsJson})`;
                chrome.debugger.sendCommand(
                    debugTarget, "Runtime.evaluate",
                    { expression: wrappedCode, returnByValue: true, awaitPromise: true },
                    (result) => {
                        if (chrome.runtime.lastError) {
                            respond(false, chrome.runtime.lastError.message);
                        } else if (result.exceptionDetails) {
                            respond(false, result.exceptionDetails.exception.description);
                        } else {
                            respond(true, result.result.value);
                        }
                    }
                );
            } catch (error) {
                respond(false, error.message || String(error));
            }
        }
    } catch (err) {
        respond(false, `extension uncaught: ${err.message || err}`);
    }
}

// Cached connection status pushed by offscreen via the Port. The popup
// reads this from SW (via chrome.runtime.sendMessage GET_STATUS); SW
// returns the cache so popup open is instant.
let cachedStatus = { connected: false, connecting: false };

let activePort = null;

chrome.runtime.onConnect.addListener((p) => {
    if (p.name !== "saidkick-cmd") return;
    activePort = p;
    p.onMessage.addListener(async (msg) => {
        if (!msg || typeof msg !== "object") return;

        // Status push from offscreen: cache and short-circuit.
        if (msg.type === "STATUS") {
            cachedStatus = {
                connected: !!msg.connected,
                connecting: !!msg.connecting,
            };
            return;
        }

        // HELLO from server (forwarded by offscreen). Cache the browser_id
        // for popup display and "reconnected as new br-XXXX" hint.
        if (msg.type === "HELLO") {
            previousBrowserId = browserId;
            browserId = msg.browser_id;
            if (previousBrowserId && previousBrowserId !== browserId) {
                console.log(`Saidkick: reconnected as ${browserId} (was ${previousBrowserId})`);
            } else {
                console.log(`Saidkick: connected as ${browserId}`);
            }
            return;
        }

        // All other frames are commands. Dispatch via the existing
        // dispatchCommand function from Task 1; the respond callback
        // sends RESPONSE frames back through the Port (which offscreen
        // forwards to the WS).
        const respond = (success, payload) => {
            try {
                p.postMessage({ type: "RESPONSE", id: msg.id, success, payload });
            } catch (e) {
                console.warn("Saidkick SW: port.postMessage failed:", e);
            }
        };
        await dispatchCommand(msg, respond);
    });
    p.onDisconnect.addListener(() => {
        if (activePort === p) activePort = null;
    });
});

// Popup status query — answer from cache so opening the popup is instant.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type === "GET_STATUS") {
        sendResponse({
            connected: cachedStatus.connected,
            connecting: cachedStatus.connecting,
            browserId,
            previousBrowserId: (previousBrowserId && previousBrowserId !== browserId) ? previousBrowserId : null,
            serverUrl: "ws://localhost:6992/ws",
        });
        return;
    }
    if (message?.type === "RECONNECT") {
        // Force a fresh offscreen → fresh WS → fresh browser_id.
        forceReconnect();
        sendResponse({ ok: true });
        return;
    }
    if (message?.type === "log") {
        // SW-originated log frame — forward to offscreen so it can ride
        // the WS (or be queued if WS is reconnecting).
        if (activePort) {
            try { activePort.postMessage(message); } catch (_) { /* drop */ }
        }
        return;
    }
});

async function forceReconnect() {
    browserId = null;
    if (await chrome.offscreen.hasDocument()) {
        try { await chrome.offscreen.closeDocument(); } catch (_) { /* ignore */ }
    }
    await ensureOffscreen();
}

// Belt-and-braces: alarms survive SW death and let us recreate the
// offscreen document if it was evicted (rare, but possible under
// memory pressure or Chrome version-specific quirks).
chrome.alarms.create(RECONNECT_ALARM, { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm.name !== RECONNECT_ALARM) return;
    await ensureOffscreen();
});

// Boot the offscreen document at SW spawn (extension load, Chrome
// restart, SW respawn after death).
ensureOffscreen().catch((err) => {
    console.error("Saidkick: ensureOffscreen failed:", err);
});
