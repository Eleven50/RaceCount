/*
  In-dashboard zone calibration.

  Flow: for each gate (Gate A -> Gate B -> Gate C), tap Point A then
  Point B on the gate's physical location in the top-down camera view,
  then confirm with the "Next gate" button before moving on. Internally
  gates are still tracked as left/straight/right (matching the rest of
  the system — dashboard, zones.py, counting), with Gate A/B/C used only
  as display labels here, per the physical gate naming: Gate A = left,
  Gate B = straight, Gate C = right.

  Uses Pointer Events, which report the same clientX/clientY for mouse,
  touch, and pen — works identically with a mouse on a monitor now and
  the touchscreen later, no separate code paths.

  Coordinates are converted from "wherever you tapped on screen" to
  "pixel position in the actual camera frame" (see screenToImageCoords),
  since the displayed image is very likely scaled/letterboxed relative
  to its native resolution (object-fit: contain).
*/

const GATE_SEQUENCE = ["left", "straight", "right"];
const GATE_DISPLAY = {
  left: { label: "Gate A", direction: "LEFT", cssClass: "gate-left" },
  straight: { label: "Gate B", direction: "STRAIGHT", cssClass: "gate-straight" },
  right: { label: "Gate C", direction: "RIGHT", cssClass: "gate-right" },
};
const POINT_LABELS = ["Point A", "Point B"];

const els = {
  prompt: document.getElementById("calPrompt"),
  progress: document.getElementById("calProgress"),
  imageWrap: document.getElementById("calImageWrap"),
  image: document.getElementById("calImage"),
  overlay: document.getElementById("calOverlay"),
  loading: document.getElementById("calLoading"),
  error: document.getElementById("calError"),
  errorDetail: document.getElementById("calErrorDetail"),
  retryBtn: document.getElementById("calRetryBtn"),
  undoBtn: document.getElementById("calUndoBtn"),
  retakeBtn: document.getElementById("calRetakeBtn"),
  restartBtn: document.getElementById("calRestartBtn"),
  nextBtn: document.getElementById("calNextBtn"),
  saveBtn: document.getElementById("calSaveBtn"),
  toast: document.getElementById("calToast"),
};

let gatePoints = { left: [], straight: [], right: [] }; // each up to 2 [x,y] pairs, image-pixel space
let confirmedGateCount = 0; // 0..3 — only advances when "Next gate" is explicitly pressed
let imageBox = null; // {offsetX, offsetY, renderedW, renderedH, naturalW, naturalH}
let gateColor = {};

function readGateColors() {
  const style = getComputedStyle(document.documentElement);
  gateColor = {
    left: style.getPropertyValue("--left").trim() || "#e8a33d",
    straight: style.getPropertyValue("--straight").trim() || "#4caf6d",
    right: style.getPropertyValue("--right").trim() || "#4a90d9",
  };
}

// ---------- Step sequencing ----------

function isAllConfirmed() {
  return confirmedGateCount >= GATE_SEQUENCE.length;
}

function activeGate() {
  return isAllConfirmed() ? null : GATE_SEQUENCE[confirmedGateCount];
}

function readyToConfirmActiveGate() {
  const gate = activeGate();
  return gate !== null && gatePoints[gate].length === 2;
}

function isLastGate() {
  return confirmedGateCount === GATE_SEQUENCE.length - 1;
}

function totalPointsTapped() {
  return GATE_SEQUENCE.reduce((sum, g) => sum + gatePoints[g].length, 0);
}

function updateUI() {
  els.progress.textContent = isAllConfirmed() ? "Review" : `${totalPointsTapped()} / 6`;

  if (isAllConfirmed()) {
    els.prompt.textContent = "All 3 gates marked — check they look right below, then save";
    els.nextBtn.style.display = "none";
    els.saveBtn.style.display = "";
  } else {
    const gate = activeGate();
    const info = GATE_DISPLAY[gate];
    const pointIdx = gatePoints[gate].length;

    if (pointIdx < 2) {
      els.prompt.innerHTML =
        `<span class="${info.cssClass}">${info.label} (${info.direction})</span> — tap ${POINT_LABELS[pointIdx]} on the gate`;
      els.nextBtn.style.display = "none";
    } else {
      els.prompt.innerHTML =
        `<span class="${info.cssClass}">${info.label} (${info.direction})</span> marked — tap ` +
        (isLastGate() ? "Finish to review &amp; save" : "Next gate to continue");
      els.nextBtn.textContent = isLastGate() ? "Finish" : "Next gate";
      els.nextBtn.style.display = "";
    }
    els.saveBtn.style.display = "none";
  }

  els.undoBtn.disabled = totalPointsTapped() === 0;
  els.restartBtn.disabled = totalPointsTapped() === 0;
}

// ---------- Coordinate transform ----------

function layoutOverlay() {
  const wrapRect = els.imageWrap.getBoundingClientRect();
  const naturalW = els.image.naturalWidth;
  const naturalH = els.image.naturalHeight;
  if (!naturalW || !naturalH) return;

  const wrapAspect = wrapRect.width / wrapRect.height;
  const naturalAspect = naturalW / naturalH;

  let renderedW, renderedH, offsetX, offsetY;
  if (naturalAspect > wrapAspect) {
    renderedW = wrapRect.width;
    renderedH = wrapRect.width / naturalAspect;
    offsetX = 0;
    offsetY = (wrapRect.height - renderedH) / 2;
  } else {
    renderedH = wrapRect.height;
    renderedW = wrapRect.height * naturalAspect;
    offsetY = 0;
    offsetX = (wrapRect.width - renderedW) / 2;
  }

  els.overlay.style.left = `${offsetX}px`;
  els.overlay.style.top = `${offsetY}px`;
  els.overlay.style.width = `${renderedW}px`;
  els.overlay.style.height = `${renderedH}px`;
  els.overlay.setAttribute("viewBox", `0 0 ${naturalW} ${naturalH}`);

  imageBox = { offsetX, offsetY, renderedW, renderedH, naturalW, naturalH };
  redraw();
}

function screenToImageCoords(clientX, clientY) {
  const wrapRect = els.imageWrap.getBoundingClientRect();
  const localX = clientX - wrapRect.left - imageBox.offsetX;
  const localY = clientY - wrapRect.top - imageBox.offsetY;

  if (localX < 0 || localY < 0 || localX > imageBox.renderedW || localY > imageBox.renderedH) {
    return null; // tapped in the letterbox padding, not on the image itself
  }

  return {
    x: Math.round((localX / imageBox.renderedW) * imageBox.naturalW),
    y: Math.round((localY / imageBox.renderedH) * imageBox.naturalH),
  };
}

// ---------- Drawing ----------

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function redraw() {
  if (!imageBox) return;
  els.overlay.innerHTML = "";

  GATE_SEQUENCE.forEach((gate, gateIdx) => {
    const points = gatePoints[gate];
    const color = gateColor[gate];
    const isConfirmedOrActive = gateIdx <= confirmedGateCount;
    if (!isConfirmedOrActive || points.length === 0) return;

    if (points.length === 2) {
      els.overlay.appendChild(
        svgEl("line", {
          x1: points[0][0], y1: points[0][1], x2: points[1][0], y2: points[1][1],
          stroke: color, "stroke-width": "5", "stroke-linecap": "round",
        })
      );
    }

    points.forEach((p, i) => {
      els.overlay.appendChild(svgEl("circle", { cx: p[0], cy: p[1], r: 11, fill: color, stroke: "#0b0d0f", "stroke-width": "2" }));
      const label = svgEl("text", {
        x: p[0] + 17, y: p[1] + 6, fill: "#fff", stroke: "#0b0d0f", "stroke-width": "3.5",
        "paint-order": "stroke", "font-size": "22", "font-weight": "700", "font-family": "sans-serif",
      });
      label.textContent = i === 0 ? "A" : "B";
      els.overlay.appendChild(label);
    });
  });
}

// ---------- Actions ----------

function addPoint(coords) {
  if (isAllConfirmed()) return;
  const gate = activeGate();
  if (gatePoints[gate].length >= 2) return; // waiting on Next gate confirmation, ignore further taps
  gatePoints[gate].push([coords.x, coords.y]);
  redraw();
  updateUI();
}

function confirmActiveGate() {
  if (!readyToConfirmActiveGate()) return;
  confirmedGateCount += 1;
  redraw();
  updateUI();
}

function undoLastPoint() {
  if (!isAllConfirmed()) {
    const gate = activeGate();
    if (gatePoints[gate].length > 0) {
      gatePoints[gate].pop();
      redraw();
      updateUI();
      return;
    }
  }
  if (confirmedGateCount > 0) {
    confirmedGateCount -= 1;
    const gate = GATE_SEQUENCE[confirmedGateCount];
    gatePoints[gate].pop();
    redraw();
    updateUI();
  }
}

function restartAll() {
  gatePoints = { left: [], straight: [], right: [] };
  confirmedGateCount = 0;
  redraw();
  updateUI();
}

function showToast(message, kind) {
  els.toast.textContent = message;
  els.toast.className = `cal-toast ${kind}`;
  els.toast.style.display = "";
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => {
    els.toast.style.display = "none";
  }, 4000);
}

async function loadSnapshot() {
  els.loading.style.display = "";
  els.error.style.display = "none";
  els.image.style.display = "none";

  try {
    const res = await fetch(`/api/calibrate/snapshot?t=${Date.now()}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    els.image.src = url;
    await new Promise((resolve, reject) => {
      els.image.onload = resolve;
      els.image.onerror = reject;
    });
    els.loading.style.display = "none";
    els.image.style.display = "";
    layoutOverlay();
  } catch (err) {
    console.error("snapshot load failed", err);
    els.loading.style.display = "none";
    els.errorDetail.textContent = err.message || "Could not reach the backend.";
    els.error.style.display = "";
  }
}

async function retakeSnapshot() {
  restartAll(); // old points won't line up with a new frame — start clean
  await loadSnapshot();
}

async function loadExisting() {
  try {
    const res = await fetch("/api/calibrate/existing");
    if (!res.ok) return;
    const data = await res.json();
    if (data.gate_points && GATE_SEQUENCE.every((g) => (data.gate_points[g] || []).length === 2)) {
      gatePoints = {
        left: data.gate_points.left.map((p) => [...p]),
        straight: data.gate_points.straight.map((p) => [...p]),
        right: data.gate_points.right.map((p) => [...p]),
      };
      confirmedGateCount = GATE_SEQUENCE.length;
      showToast("Loaded your last calibration — Save to keep it, or Start over to redo it.", "success");
    } else {
      showToast("Tap Point A then Point B for each gate. Gate A = Left · Gate B = Straight · Gate C = Right.", "success");
    }
  } catch (err) {
    console.debug("no existing calibration to load", err);
  } finally {
    redraw();
    updateUI();
  }
}

async function saveZones() {
  if (!isAllConfirmed()) return;
  els.saveBtn.disabled = true;
  els.saveBtn.textContent = "Saving…";
  try {
    const res = await fetch("/api/calibrate/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gate_points: gatePoints }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    showToast("Zones saved — applied live, no restart needed.", "success");
    setTimeout(() => {
      window.location.href = "/";
    }, 1200);
  } catch (err) {
    console.error("save failed", err);
    showToast(`Could not save: ${err.message}`, "error");
    els.saveBtn.disabled = false;
    els.saveBtn.textContent = "Save zones";
  }
}

// ---------- Wiring ----------

els.imageWrap.addEventListener("pointerdown", (event) => {
  if (event.target.closest(".cal-top-bar, .cal-bottom-bar")) return; // taps on overlay UI shouldn't add points
  if (isAllConfirmed()) return; // reviewing — Undo first to make changes
  if (els.image.style.display === "none") return; // no image loaded yet
  const coords = screenToImageCoords(event.clientX, event.clientY);
  if (!coords) return;
  addPoint(coords);
});

els.undoBtn.addEventListener("click", undoLastPoint);
els.restartBtn.addEventListener("click", restartAll);
els.retakeBtn.addEventListener("click", retakeSnapshot);
els.retryBtn.addEventListener("click", loadSnapshot);
els.nextBtn.addEventListener("click", confirmActiveGate);
els.saveBtn.addEventListener("click", saveZones);
window.addEventListener("resize", layoutOverlay);

readGateColors();
updateUI();
loadSnapshot().then(loadExisting);
