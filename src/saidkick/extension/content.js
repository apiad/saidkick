(function() {
    console.log("Saidkick: Content script (isolated world) initializing");
    try {
        chrome.runtime.sendMessage({
            type: "log",
            level: "log",
            data: "Saidkick: Content script connected",
            timestamp: new Date().toISOString(),
            url: window.location.href
        });
    } catch (e) {}

    // Listen for log messages from the MAIN world (main_world.js)
    window.addEventListener('message', (event) => {
        // Only accept messages from the same window
        if (event.source !== window) return;

        const message = event.data;
        if (message && message.type === 'saidkick-log') {
            try {
                chrome.runtime.sendMessage({
                    type: "log",
                    ...message.detail
                });
            } catch (e) {
                // Context might be invalidated
            }
        }
    });

    // Handle commands from background script
    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
        const { type, payload } = request;

        const findElement = (css, xpath) => {
            let elements = [];
            if (css) {
                elements = Array.from(document.querySelectorAll(css));
            } else if (xpath) {
                const result = document.evaluate(
                    xpath,
                    document,
                    null,
                    XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,
                    null
                );
                for (let i = 0; i < result.snapshotLength; i++) {
                    elements.push(result.snapshotItem(i));
                }
            } else {
                throw new Error("No selector provided");
            }

            if (elements.length === 0) {
                throw new Error("Element not found");
            }
            if (elements.length > 1) {
                throw new Error(
                    `Ambiguous selector: found ${elements.length} matches`
                );
            }
            return elements[0];
        };

        if (type === "GET_DOM") {
            const { css, xpath, all } = payload || {};
            let matches = [];
            try {
                if (css) {
                    matches = Array.from(document.querySelectorAll(css));
                } else if (xpath) {
                    const result = document.evaluate(
                        xpath,
                        document,
                        null,
                        XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,
                        null
                    );
                    for (let i = 0; i < result.snapshotLength; i++) {
                        matches.push(result.snapshotItem(i));
                    }
                } else {
                    matches = [document.documentElement];
                }

                const output = all
                    ? matches.map((m) => m.outerHTML).join("\n")
                    : matches[0]?.outerHTML || "";
                sendResponse({ success: true, payload: output });
            } catch (e) {
                sendResponse({ success: false, payload: "Error: " + e.message });
            }
        } else if (type === "CLICK") {
            try {
                const element = findElement(payload.css, payload.xpath);
                element.click();
                // Dispatch synthetic events for frameworks
                element.dispatchEvent(
                    new MouseEvent("mousedown", { bubbles: true })
                );
                element.dispatchEvent(
                    new MouseEvent("mouseup", { bubbles: true })
                );
                sendResponse({ success: true, payload: "Clicked" });
            } catch (e) {
                sendResponse({ success: false, payload: e.message });
            }
        } else if (type === "TYPE") {
            try {
                const element = findElement(payload.css, payload.xpath);
                element.focus();
                if (payload.clear) {
                    if (element.tagName === "INPUT" || element.tagName === "TEXTAREA") {
                        element.value = "";
                    } else if (element.isContentEditable) {
                        element.innerText = "";
                    }
                }

                const text = payload.text;
                if (element.tagName === "INPUT" || element.tagName === "TEXTAREA") {
                    element.value += text;
                } else if (element.isContentEditable) {
                    element.innerText += text;
                }

                // Dispatch synthetic events
                element.dispatchEvent(new Event("input", { bubbles: true }));
                element.dispatchEvent(new Event("change", { bubbles: true }));
                element.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true }));
                element.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true }));

                sendResponse({ success: true, payload: "Typed" });
            } catch (e) {
                sendResponse({ success: false, payload: e.message });
            }
        } else if (type === "SELECT") {
            try {
                const element = findElement(payload.css, payload.xpath);
                if (element.tagName !== "SELECT") {
                    throw new Error("Element is not a <select>");
                }

                const val = payload.value;
                let found = false;

                // Try by value
                for (const option of element.options) {
                    if (option.value === val || option.text === val) {
                        element.value = option.value;
                        found = true;
                        break;
                    }
                }

                if (!found) {
                    throw new Error(`Option not found: ${val}`);
                }

                element.dispatchEvent(new Event("change", { bubbles: true }));
                sendResponse({ success: true, payload: "Selected" });
            } catch (e) {
                sendResponse({ success: false, payload: e.message });
            }
        }
        return true;
    });
})();
