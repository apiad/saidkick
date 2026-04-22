(function() {
    // Guard against double-installation. main_world.js runs in the page's
    // MAIN world; if injected twice (manifest + chrome.scripting race) we'd
    // wrap the already-wrapped console, producing compound handlers.
    if (window.__saidkickMainWorldInstalled) return;
    window.__saidkickMainWorldInstalled = true;

    const originalConsole = {
        log: console.log,
        warn: console.warn,
        error: console.error
    };

    function safeStringify(obj) {
        try {
            return JSON.stringify(obj);
        } catch (e) {
            return String(obj);
        }
    }

    function mirrorLog(level, args) {
        window.postMessage({
            type: 'saidkick-log',
            detail: {
                level: level,
                data: Array.from(args).map(safeStringify).join(" "),
                timestamp: new Date().toISOString(),
                url: window.location.href
            }
        }, "*");
    }

    console.log = function() {
        mirrorLog("log", arguments);
        originalConsole.log.apply(console, arguments);
    };

    console.warn = function() {
        mirrorLog("warn", arguments);
        originalConsole.warn.apply(console, arguments);
    };

    console.error = function() {
        mirrorLog("error", arguments);
        originalConsole.error.apply(console, arguments);
    };
    
    console.info("Saidkick: Main world console overrides active");
})();
