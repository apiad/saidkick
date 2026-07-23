// Cockpit client: render the screencast onto a canvas, and forward input
// back into the page while the human holds control.
//
// Frames are decoded with createImageBitmap so decoding happens off the main
// thread, then drawn to a 2D canvas. Deliberately not WebGL: the frames are
// JPEG, the browser's decoder is native, and the 2D path is already
// GPU-composited, so a shader pipeline would add complexity for no gain.

(function () {
  const session = document.querySelector(".session");
  if (!session) return;

  const canvas = document.getElementById("screen");
  const ctx2d = canvas.getContext("2d");
  const picker = document.getElementById("tab-picker");
  const takeBtn = document.getElementById("take");
  const releaseBtn = document.getElementById("release");
  const pasteBtn = document.getElementById("paste");
  const pasteText = document.getElementById("paste-text");
  const stateEl = document.getElementById("state");
  const hint = document.getElementById("hint");

  let viewSocket = null;
  let controlSocket = null;
  let lastMetadata = {};
  let holding = false;

  const wsUrl = (path) =>
    (location.protocol === "https:" ? "wss://" : "ws://") + location.host + path;

  function setHolding(next) {
    holding = next;
    releaseBtn.disabled = !next;
    takeBtn.disabled = next;
    pasteBtn.disabled = !next;
    pasteText.disabled = !next;
    stateEl.textContent = next ? "human" : "agent";
    stateEl.className = "badge badge-" + (next ? "human" : "agent");
    hint.textContent = next
      ? "You are driving. Click and type into the frame; release when you are done."
      : "Watching. Click “Take over” to drive.";
    if (next) canvas.focus();
  }

  function connect(tabId) {
    if (viewSocket) viewSocket.close();
    if (controlSocket) controlSocket.close();

    viewSocket = new WebSocket(wsUrl("/ws/view/" + tabId));
    viewSocket.onmessage = async (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type !== "frame") return;
      lastMetadata = msg.metadata || {};
      const blob = await fetch("data:image/jpeg;base64," + msg.data).then((r) => r.blob());
      const bitmap = await createImageBitmap(blob);
      // Size the canvas from the device metrics so the coordinate transform
      // used for takeover is established here rather than retrofitted.
      if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
      }
      ctx2d.drawImage(bitmap, 0, 0);
      bitmap.close();
    };

    controlSocket = new WebSocket(wsUrl("/ws/control/" + tabId));
    controlSocket.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.state) setHolding(msg.state === "human");
    };
    controlSocket.onclose = () => setHolding(false);
  }

  function send(msg) {
    if (!holding || !controlSocket || controlSocket.readyState !== WebSocket.OPEN) return;
    controlSocket.send(JSON.stringify(Object.assign({ metadata: lastMetadata }, msg)));
  }

  function point(event) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * canvas.width,
      y: ((event.clientY - rect.top) / rect.height) * canvas.height,
      canvas: { width: canvas.width, height: canvas.height },
    };
  }

  const modifiersOf = (e) =>
    ["alt", "ctrl", "meta", "shift"].filter(
      (m) => e[m === "ctrl" ? "ctrlKey" : m === "meta" ? "metaKey" : m + "Key"]
    );

  canvas.addEventListener("mousemove", (e) => send({ type: "mousemove", ...point(e) }));
  canvas.addEventListener("mousedown", (e) =>
    send({ type: "mousedown", button: "left", clickCount: 1, ...point(e) })
  );
  canvas.addEventListener("mouseup", (e) =>
    send({ type: "mouseup", button: "left", clickCount: 1, ...point(e) })
  );
  canvas.addEventListener("wheel", (e) => {
    if (!holding) return;
    e.preventDefault();
    send({ type: "wheel", deltaX: e.deltaX, deltaY: e.deltaY, ...point(e) });
  });
  canvas.addEventListener("keydown", (e) => {
    if (!holding) return;
    e.preventDefault();
    send({ type: "keydown", key: e.key, code: e.code, modifiers: modifiersOf(e) });
  });
  canvas.addEventListener("keyup", (e) => {
    if (!holding) return;
    e.preventDefault();
    send({ type: "keyup", key: e.key, code: e.code, modifiers: modifiersOf(e) });
  });

  takeBtn.addEventListener("click", () =>
    controlSocket && controlSocket.send(JSON.stringify({ type: "take" }))
  );
  releaseBtn.addEventListener("click", () =>
    controlSocket && controlSocket.send(JSON.stringify({ type: "release", note: null }))
  );
  // insertText, not thirty synthesised keystrokes — this is the 2FA path.
  pasteBtn.addEventListener("click", () => {
    send({ type: "paste", text: pasteText.value });
    pasteText.value = "";
  });

  picker.addEventListener("change", () => connect(picker.value));
  if (picker.value) connect(picker.value);
  setHolding(false);
})();
