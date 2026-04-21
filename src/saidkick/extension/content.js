(function() {
    console.log("Saidkick: Content script (isolated world) initializing");
    try {
        chrome.runtime.sendMessage({
            type: "log", level: "log",
            data: "Saidkick: Content script connected",
            timestamp: new Date().toISOString(),
            url: window.location.href,
        });
    } catch (e) {}

    window.addEventListener('message', (event) => {
        if (event.source !== window) return;
        const msg = event.data;
        if (msg && msg.type === 'saidkick-log') {
            try { chrome.runtime.sendMessage({ type: "log", ...msg.detail }); }
            catch (e) {}
        }
    });

    function resolveRoot(locator) {
        if (!locator.within_css) return document;
        const root = document.querySelector(locator.within_css);
        if (!root) throw new Error(`within-css matched no element: ${locator.within_css}`);
        return root;
    }

    function matchesPredicate(locator) {
        const val = locator.by_text ?? locator.by_label ?? locator.by_placeholder;
        if (val == null) return null;
        let test;
        if (locator.regex) {
            let re;
            try { re = new RegExp(val); }
            catch (e) { throw new Error(`invalid regex: ${e.message}`); }
            test = s => re.test(s);
        } else if (locator.exact) {
            test = s => s === val;
        } else {
            const needle = val.toLowerCase();
            test = s => s.toLowerCase().includes(needle);
        }
        const getText = el => {
            if (locator.by_text != null) {
                return (el.textContent || el.innerText || "").trim();
            }
            if (locator.by_label != null) {
                const aria = el.getAttribute("aria-label");
                if (aria) return aria;
                const labelledby = el.getAttribute("aria-labelledby");
                if (labelledby) {
                    const parts = labelledby.split(/\s+/)
                        .map(id => document.getElementById(id)?.textContent || "")
                        .join(" ").trim();
                    if (parts) return parts;
                }
                if (el.id) {
                    const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                    if (label) return (label.textContent || "").trim();
                }
                return "";
            }
            if (locator.by_placeholder != null) {
                return el.getAttribute("placeholder") || "";
            }
            return "";
        };
        return el => test(getText(el));
    }

    function collectLocator(locator) {
        const root = resolveRoot(locator);
        let matches;
        if (locator.css) {
            matches = Array.from(root.querySelectorAll(locator.css));
        } else if (locator.xpath) {
            const result = document.evaluate(
                locator.xpath, root, null,
                XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null
            );
            matches = [];
            for (let i = 0; i < result.snapshotLength; i++) {
                matches.push(result.snapshotItem(i));
            }
        } else {
            const pred = matchesPredicate(locator);
            if (!pred) throw new Error("No selector provided");
            matches = Array.from(root.querySelectorAll("*")).filter(pred);
        }
        if (locator.nth != null) {
            const el = matches[locator.nth];
            return el ? [el] : [];
        }
        return matches;
    }

    async function waitForLocator(locator, waitMs) {
        const start = Date.now();
        const POLL = 100;
        while (true) {
            const matches = collectLocator(locator);
            if (matches.length === 1) return matches[0];
            if (matches.length > 1) {
                if (Date.now() - start >= waitMs) {
                    throw new Error(`Ambiguous locator: found ${matches.length} matches`);
                }
            } else if (Date.now() - start >= waitMs) {
                throw new Error("element not found");
            }
            await new Promise(r => setTimeout(r, POLL));
        }
    }

    async function waitForAnyLocator(locator, waitMs) {
        const start = Date.now();
        const POLL = 100;
        while (true) {
            let matches;
            try { matches = collectLocator(locator); }
            catch (e) { return []; }
            if (matches.length >= 1) return matches;
            if (Date.now() - start >= waitMs) return matches;
            await new Promise(r => setTimeout(r, POLL));
        }
    }

    function uniqueSelector(el) {
        if (!(el instanceof Element)) return "";
        if (el.id) return `#${CSS.escape(el.id)}`;
        const parts = [];
        while (el && el.nodeType === 1 && el !== document.body) {
            let part = el.tagName.toLowerCase();
            const parent = el.parentElement;
            if (parent) {
                const siblings = Array.from(parent.children).filter(s => s.tagName === el.tagName);
                if (siblings.length > 1) {
                    part += `:nth-of-type(${siblings.indexOf(el) + 1})`;
                }
            }
            parts.unshift(part);
            el = parent;
        }
        return "body > " + parts.join(" > ");
    }

    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
        const { type, payload } = request;

        const handle = async () => {
            const waitMs = (payload && payload.wait_ms) || 0;

            if (type === "GET_DOM") {
                const { all } = payload || {};
                let matches;
                if (!payload.css && !payload.xpath && !payload.by_text
                    && !payload.by_label && !payload.by_placeholder
                    && !payload.within_css) {
                    matches = [document.documentElement];
                } else if (all) {
                    matches = await waitForAnyLocator(payload, waitMs);
                    if (matches.length === 0) throw new Error("element not found");
                } else {
                    matches = [await waitForLocator(payload, waitMs)];
                }
                const output = all
                    ? matches.map(m => m.outerHTML).join("\n")
                    : matches[0].outerHTML;
                return { success: true, payload: output };
            }

            if (type === "CLICK") {
                const element = await waitForLocator(payload, waitMs);
                element.click();
                element.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
                element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
                return { success: true, payload: "Clicked" };
            }

            if (type === "TYPE") {
                const element = await waitForLocator(payload, waitMs);
                element.focus();
                if (element.isContentEditable) {
                    if (payload.clear) {
                        const range = document.createRange();
                        range.selectNodeContents(element);
                        const sel = window.getSelection();
                        sel.removeAllRanges();
                        sel.addRange(range);
                        document.execCommand("delete");
                    }
                    document.execCommand("insertText", false, payload.text);
                    element.dispatchEvent(new Event("input", { bubbles: true }));
                    element.dispatchEvent(new Event("change", { bubbles: true }));
                    return { success: true, payload: "Typed" };
                }
                if (payload.clear) {
                    if (element.tagName === "INPUT" || element.tagName === "TEXTAREA") {
                        element.value = "";
                    }
                }
                if (element.tagName === "INPUT" || element.tagName === "TEXTAREA") {
                    element.value += payload.text;
                }
                element.dispatchEvent(new Event("input", { bubbles: true }));
                element.dispatchEvent(new Event("change", { bubbles: true }));
                element.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true }));
                element.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true }));
                return { success: true, payload: "Typed" };
            }

            if (type === "SELECT") {
                const element = await waitForLocator(payload, waitMs);
                if (element.tagName !== "SELECT") {
                    throw new Error("Element is not a <select>");
                }
                const val = payload.value;
                let found = false;
                for (const opt of element.options) {
                    if (opt.value === val || opt.text === val) {
                        element.value = opt.value;
                        found = true;
                        break;
                    }
                }
                if (!found) throw new Error(`option not found: ${val}`);
                element.dispatchEvent(new Event("change", { bubbles: true }));
                return { success: true, payload: "Selected" };
            }

            if (type === "GET_TEXT") {
                const hasLocator = payload.css || payload.xpath || payload.by_text
                    || payload.by_label || payload.by_placeholder;
                const element = hasLocator
                    ? await waitForLocator(payload, waitMs)
                    : document.body;
                return { success: true, payload: element.innerText || "" };
            }

            if (type === "FIND") {
                const matches = await waitForAnyLocator(payload, waitMs);
                const out = matches.slice(0, 50).map(el => {
                    const rect = el.getBoundingClientRect();
                    return {
                        selector: uniqueSelector(el),
                        tag: el.tagName,
                        role: el.getAttribute("role") || null,
                        name: el.getAttribute("aria-label") || (el.textContent || "").trim().slice(0, 80),
                        text: (el.textContent || "").trim().slice(0, 200),
                        rect: {x: Math.round(rect.x), y: Math.round(rect.y),
                               w: Math.round(rect.width), h: Math.round(rect.height)},
                        visible: !!el.offsetParent && rect.width > 0 && rect.height > 0,
                    };
                });
                return { success: true, payload: out };
            }

            if (type === "FOCUS") {
                const el = await waitForLocator(payload, waitMs);
                el.focus();
                return { success: true, payload: "Focused" };
            }

            if (type === "RESOLVE_RECT") {
                const el = await waitForLocator(payload, waitMs);
                const rect = el.getBoundingClientRect();
                return { success: true, payload: {
                    x: Math.round(rect.x), y: Math.round(rect.y),
                    width: Math.round(rect.width), height: Math.round(rect.height),
                }};
            }

            throw new Error(`unknown command: ${type}`);
        };

        handle().then(
            result => sendResponse(result),
            err => sendResponse({ success: false, payload: err.message }),
        );
        return true;
    });
})();
