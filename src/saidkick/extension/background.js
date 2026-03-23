let socket = null;
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

        // Get all tabs
        const tabs = await chrome.tabs.query({});
        let tab = tabs.find(t => t.active && t.url && !t.url.startsWith('chrome://'));
        if (!tab) {
            tab = tabs.find(t => t.url && !t.url.startsWith('chrome://') && (t.url.includes('localhost:8000') || t.url.includes('localhost:8088')));
        }
        if (!tab) {
            tab = tabs.find(t => t.url && !t.url.startsWith('chrome://'));
        }

        if (!tab) {
            socket.send(JSON.stringify({
                type: "RESPONSE",
                id: id,
                success: false,
                payload: "No scriptable tab found"
            }));
            return;
        }

        const debugTarget = { tabId: tab.id };

        if (["GET_DOM", "CLICK", "TYPE", "SELECT"].includes(type)) {
            try {
                chrome.tabs.sendMessage(tab.id, { type, payload }, (response) => {
                    if (chrome.runtime.lastError) {
                        socket.send(JSON.stringify({
                            type: "RESPONSE",
                            id: id,
                            success: false,
                            payload: chrome.runtime.lastError.message
                        }));
                    } else {
                        socket.send(JSON.stringify({
                            type: "RESPONSE",
                            id: id,
                            success: response.success,
                            payload: response.payload
                        }));
                    }
                });
            } catch (err) {
                socket.send(JSON.stringify({
                    type: "RESPONSE",
                    id: id,
                    success: false,
                    payload: err.toString()
                }));
            }
        } else if (type === "EXECUTE") {
            try {
                // Use chrome.debugger to bypass CSP
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

                // Enable Runtime
                await new Promise(resolve => chrome.debugger.sendCommand(debugTarget, "Runtime.enable", {}, resolve));

                chrome.debugger.sendCommand(debugTarget, "Runtime.evaluate", {
                    expression: payload,
                    returnByValue: true
                }, (result) => {
                    if (chrome.runtime.lastError) {
                        socket.send(JSON.stringify({
                            type: "RESPONSE",
                            id: id,
                            success: false,
                            payload: chrome.runtime.lastError.message
                        }));
                    } else if (result.exceptionDetails) {
                        socket.send(JSON.stringify({
                            type: "RESPONSE",
                            id: id,
                            success: false,
                            payload: result.exceptionDetails.exception.description
                        }));
                    } else {
                        socket.send(JSON.stringify({
                            type: "RESPONSE",
                            id: id,
                            success: true,
                            payload: result.result.value
                        }));
                    }
                    // Keep attached for future commands or detach if desired
                    // For now, we stay attached
                });
            } catch (error) {
                socket.send(JSON.stringify({
                    type: "RESPONSE",
                    id: id,
                    success: false,
                    payload: error.toString()
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

async function checkInitialTabs() {
    const tabs = await chrome.tabs.query({url: ["*://localhost:8000/*", "*://localhost:8088/*"]});
    for (const tab of tabs) {
        chrome.tabs.sendMessage(tab.id, { type: "PING" }, (response) => {
            if (chrome.runtime.lastError) {
                // Content script might not be there, try to inject or just log
                console.log("Saidkick: Localhost tab found but no content script responding");
            }
        });
    }
}

connect();
checkInitialTabs();
