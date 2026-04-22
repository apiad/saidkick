(function() {
    // Guard against double-registration when the manifest content_scripts
    // entry and a programmatic chrome.scripting.executeScript both inject
    // this file on the same tab (race on fresh-load / extension-reload).
    if (window.__saidkickContentInstalled) return;
    window.__saidkickContentInstalled = true;

    console.log("Saidkick: Content script (isolated world) initializing");
    try {
        chrome.runtime.sendMessage({
            type: "log", level: "log",
            data: "Saidkick: Content script connected",
            timestamp: new Date().toISOString(),
            url: window.location.href,
        });
    } catch (e) {}

    // Console mirroring is OPT-IN per tab as of 0.5.0 — main_world.js still
    // wraps console.log, but we only forward the postMessages to background
    // (and thence to the server) when this tab is explicitly mirrored.
    // Background toggles this via a SET_MIRROR message.
    let mirroring = false;

    window.addEventListener('message', (event) => {
        if (event.source !== window) return;
        const msg = event.data;
        if (msg && msg.type === 'saidkick-log') {
            if (!mirroring) return;
            try { chrome.runtime.sendMessage({ type: "log", ...msg.detail }); }
            catch (e) {}
        }
    });

    // Tracks active HIGHLIGHT state per element: {prev, activeCount}. A
    // WeakMap lets the GC reclaim state if the element is removed from the
    // DOM before its highlight expires.
    const highlightState = new WeakMap();

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

    // Walk every element under `root`, optionally descending into shadow roots.
    function* walkDeep(root, pierce) {
        if (root.nodeType === 1) yield root;
        if (root.querySelectorAll) {
            for (const el of root.querySelectorAll("*")) {
                yield el;
                if (pierce && el.shadowRoot) {
                    for (const inner of walkDeep(el.shadowRoot, pierce)) yield inner;
                }
            }
        }
    }

    function collectLocator(locator) {
        const root = resolveRoot(locator);
        const pierce = Boolean(locator.pierce_shadow);
        let matches;
        if (locator.css) {
            if (pierce) {
                // CSS selectors don't natively pierce shadow — run the query on
                // each shadow root separately.
                matches = [];
                matches.push(...root.querySelectorAll(locator.css));
                for (const el of walkDeep(root, true)) {
                    if (el.shadowRoot) {
                        matches.push(...el.shadowRoot.querySelectorAll(locator.css));
                    }
                }
            } else {
                matches = Array.from(root.querySelectorAll(locator.css));
            }
        } else if (locator.xpath) {
            // XPath doesn't meaningfully pierce either; just run at document-root.
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
            const all = pierce
                ? Array.from(walkDeep(root, true))
                : Array.from(root.querySelectorAll("*"));
            matches = all.filter(pred);
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
                if (element.tagName === "INPUT" || element.tagName === "TEXTAREA") {
                    // React / Preact / Vue2 / Svelte track the value via a
                    // prototype-level setter. Direct `.value =` bypasses that,
                    // so the framework's state stays stale and re-renders wipe
                    // the text. Use the native descriptor setter to route the
                    // assignment through the framework's hooks.
                    const proto = element.tagName === "TEXTAREA"
                        ? HTMLTextAreaElement.prototype
                        : HTMLInputElement.prototype;
                    const valueSetter = Object.getOwnPropertyDescriptor(proto, "value").set;
                    const nextValue = payload.clear ? payload.text : (element.value + payload.text);
                    valueSetter.call(element, nextValue);
                } else {
                    // Fallback for non-input elements we've been asked to type into
                    // (unusual — usually rejected by the caller).
                    element.textContent = (payload.clear ? "" : element.textContent) + payload.text;
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

            if (type === "SET_MIRROR") {
                mirroring = Boolean(payload && payload.enabled);
                return { success: true, payload: { mirroring } };
            }

            if (type === "GET_MIRROR") {
                return { success: true, payload: { mirroring } };
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

            if (type === "SCROLL") {
                const el = await waitForLocator(payload, waitMs);
                const block = payload.block || "center";
                const behavior = payload.behavior || "auto";
                el.scrollIntoView({ block, inline: "nearest", behavior });
                // For smooth scroll, wait for it to finish so the returned rect is accurate.
                if (behavior === "smooth") {
                    await new Promise(r => setTimeout(r, 400));
                } else {
                    await new Promise(r => setTimeout(r, 50));
                }
                const rect = el.getBoundingClientRect();
                return { success: true, payload: {
                    scrolled_to: {
                        x: Math.round(rect.x), y: Math.round(rect.y),
                        width: Math.round(rect.width), height: Math.round(rect.height),
                    }
                }};
            }

            if (type === "HIGHLIGHT") {
                const el = await waitForLocator(payload, waitMs);
                const color = payload.color || "#ff3b30";
                const duration = typeof payload.duration_ms === "number" ? payload.duration_ms : 2000;
                // Back-to-back highlights on the same element previously polluted
                // `prev` — the second call captured the red outline as "original."
                // Now: prev is captured on the FIRST highlight of an element and
                // kept in a WeakMap; subsequent highlights reuse it. A refcount
                // ensures we only restore when the LAST active highlight expires.
                let entry = highlightState.get(el);
                if (!entry) {
                    entry = {
                        prev: {
                            outline: el.style.outline,
                            outlineOffset: el.style.outlineOffset,
                            boxShadow: el.style.boxShadow,
                            transition: el.style.transition,
                        },
                        activeCount: 0,
                    };
                    highlightState.set(el, entry);
                }
                entry.activeCount += 1;
                el.style.outline = `3px solid ${color}`;
                el.style.outlineOffset = "3px";
                el.style.boxShadow = `0 0 0 6px ${color}33`;
                el.style.transition = "outline 120ms ease, box-shadow 120ms ease";
                if (duration > 0) {
                    setTimeout(() => {
                        entry.activeCount -= 1;
                        if (entry.activeCount <= 0) {
                            el.style.outline = entry.prev.outline;
                            el.style.outlineOffset = entry.prev.outlineOffset;
                            el.style.boxShadow = entry.prev.boxShadow;
                            el.style.transition = entry.prev.transition;
                            highlightState.delete(el);
                        }
                    }, duration);
                }
                const rect = el.getBoundingClientRect();
                return { success: true, payload: {
                    highlighted: {
                        x: Math.round(rect.x), y: Math.round(rect.y),
                        width: Math.round(rect.width), height: Math.round(rect.height),
                    },
                    duration_ms: duration,
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
