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

    // Mirror logs from the MAIN world.
    window.addEventListener('message', (event) => {
        if (event.source !== window) return;
        const message = event.data;
        if (message && message.type === 'saidkick-log') {
            try {
                chrome.runtime.sendMessage({ type: "log", ...message.detail });
            } catch (e) { /* context may be invalidated */ }
        }
    });

    function collectMatches(css, xpath) {
        if (css) return Array.from(document.querySelectorAll(css));
        if (xpath) {
            const result = document.evaluate(
                xpath, document, null,
                XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null
            );
            const nodes = [];
            for (let i = 0; i < result.snapshotLength; i++) {
                nodes.push(result.snapshotItem(i));
            }
            return nodes;
        }
        throw new Error("No selector provided");
    }

    async function waitForSelector(css, xpath, waitMs) {
        const start = Date.now();
        const POLL = 100;
        while (true) {
            const matches = collectMatches(css, xpath);
            if (matches.length === 1) return matches[0];
            if (matches.length > 1) {
                if (Date.now() - start >= waitMs) {
                    throw new Error(`Ambiguous selector: found ${matches.length} matches`);
                }
            } else if (Date.now() - start >= waitMs) {
                throw new Error("element not found");
            }
            await new Promise(r => setTimeout(r, POLL));
        }
    }

    async function waitForAnyMatches(css, xpath, waitMs) {
        const start = Date.now();
        const POLL = 100;
        while (true) {
            let matches;
            try { matches = collectMatches(css, xpath); }
            catch (e) { return []; }
            if (matches.length >= 1) return matches;
            if (Date.now() - start >= waitMs) return matches;
            await new Promise(r => setTimeout(r, POLL));
        }
    }

    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
        const { type, payload } = request;

        const handle = async () => {
            const waitMs = (payload && payload.wait_ms) || 0;

            if (type === "GET_DOM") {
                const { css, xpath, all } = payload || {};
                let matches;
                if (!css && !xpath) {
                    matches = [document.documentElement];
                } else if (all) {
                    matches = await waitForAnyMatches(css, xpath, waitMs);
                    if (matches.length === 0) throw new Error("element not found");
                } else {
                    matches = [await waitForSelector(css, xpath, waitMs)];
                }
                const output = all
                    ? matches.map(m => m.outerHTML).join("\n")
                    : matches[0].outerHTML;
                return { success: true, payload: output };
            }

            if (type === "CLICK") {
                const element = await waitForSelector(payload.css, payload.xpath, waitMs);
                element.click();
                element.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
                element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
                return { success: true, payload: "Clicked" };
            }

            if (type === "TYPE") {
                const element = await waitForSelector(payload.css, payload.xpath, waitMs);
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
                element.dispatchEvent(new Event("input", { bubbles: true }));
                element.dispatchEvent(new Event("change", { bubbles: true }));
                element.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true }));
                element.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true }));
                return { success: true, payload: "Typed" };
            }

            if (type === "SELECT") {
                const element = await waitForSelector(payload.css, payload.xpath, waitMs);
                if (element.tagName !== "SELECT") {
                    throw new Error("Element is not a <select>");
                }
                const val = payload.value;
                let found = false;
                for (const option of element.options) {
                    if (option.value === val || option.text === val) {
                        element.value = option.value;
                        found = true;
                        break;
                    }
                }
                if (!found) throw new Error(`option not found: ${val}`);
                element.dispatchEvent(new Event("change", { bubbles: true }));
                return { success: true, payload: "Selected" };
            }

            if (type === "GET_TEXT") {
                const { css } = payload || {};
                const element = css
                    ? await waitForSelector(css, null, waitMs)
                    : document.body;
                return { success: true, payload: element.innerText || "" };
            }

            throw new Error(`unknown command: ${type}`);
        };

        handle().then(
            result => sendResponse(result),
            err => sendResponse({ success: false, payload: err.message })
        );
        return true;  // async sendResponse
    });
})();
