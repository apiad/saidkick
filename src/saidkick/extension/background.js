let socket = null;
let browserId = null;
const SERVER_URL = "ws://localhost:6992/ws";
const logQueue = [];

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
        const message = JSON.parse(event.data);
        const { type, id, payload } = message;

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

        // All remaining commands target a specific tab_id supplied in payload.
        const tabId = payload?.tab_id;
        if (typeof tabId !== "number") {
            socket.send(JSON.stringify({
                type: "RESPONSE", id, success: false, payload: "tab_id required",
            }));
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

        const debugTarget = { tabId: tab.id };

        if (["GET_DOM", "CLICK", "TYPE", "SELECT"].includes(type)) {
            try {
                chrome.tabs.sendMessage(tab.id, { type, payload }, (response) => {
                    if (chrome.runtime.lastError) {
                        socket.send(JSON.stringify({
                            type: "RESPONSE", id, success: false,
                            payload: chrome.runtime.lastError.message,
                        }));
                    } else {
                        socket.send(JSON.stringify({
                            type: "RESPONSE", id,
                            success: response.success, payload: response.payload,
                        }));
                    }
                });
            } catch (err) {
                socket.send(JSON.stringify({
                    type: "RESPONSE", id, success: false, payload: err.toString(),
                }));
            }
        } else if (type === "EXECUTE") {
            try {
                await new Promise((resolve, reject) => {
                    chrome.debugger.attach(debugTarget, "1.3", () => {
                        if (chrome.runtime.lastError) {
                            if (chrome.runtime.lastError.message.includes("already attached")) {
                                resolve();
                            } else {
                                reject(chrome.runtime.lastError);
                            }
                        } else {
                            resolve();
                        }
                    });
                });
                await new Promise(resolve =>
                    chrome.debugger.sendCommand(debugTarget, "Runtime.enable", {}, resolve)
                );
                chrome.debugger.sendCommand(
                    debugTarget, "Runtime.evaluate",
                    { expression: payload.code, returnByValue: true },
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
                    type: "RESPONSE", id, success: false, payload: error.toString(),
                }));
            }
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

// Receive logs from content script
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    console.log("Saidkick: Received message from content script:", message.type);
    if (message.type === "log") {
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify(message));
        } else {
            logQueue.push(message);
        }
    }
});

connect();
