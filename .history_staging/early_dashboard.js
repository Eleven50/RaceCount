/*
  Dashboard client logic. Deliberately plain JS, no build step, no
  framework — this only ever needs to run in the kiosk's own Chromium
  instance. Two polling loops (counts, status) rather than
  WebSockets/SSE: for a single local client, polling is simpler to
  reason about and debug, and the interval is short enough that the
  extra latency is imperceptible.
*/

const COUNTS_POLL_MS = 700;
const STATUS_POLL_MS = 2000;
const RESET_ARM_TIMEOUT_MS = 4000;

const el = {
  left: document.getElementById("countLeft"),
  straight: document.getElementById("countStraight"),
  right: document.getElementById("countRight"),
  total: document.getElementById("countTotal"),
  sessionStart: document.getElementById("sessionStart"),
  statusDot: document.getElementById("statusDot"),
  statusText: document.getElementById("statusText"),
  videoPanel: document.getElementById("videoPanel"),
  resetButton: document.getElementById("resetButton"),
};

let lastCounts = { left: 0, straight: 0, right: 0 };

function flashTile(direction) {
  const tile = document.querySelector(`.tile[data-dir="${direction}"]`);
  if (!tile) return;
  tile.classList.add("just-counted");
  setTimeout(() => tile.classList.remove("just-counted"), 220);
}

function formatSessionStart(unixSeconds) {
  if (!unixSeconds) return "—";
  const d = new Date(unixSeconds * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

async function pollCounts() {
  try {
    const res = await fetch("/api/counts");
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();
    const counts = data.counts || {};

    for (const dir of ["left", "straight", "right"]) {
      const value = counts[dir] ?? 0;
      if (value !== lastCounts[dir]) {
        flashTile(dir);
      }
      el[dir].textContent = value;
    }
    el.total.textContent = data.total ?? 0;
    el.sessionStart.textContent = formatSessionStart(data.session_start);
    lastCounts = counts;
  } catch (err) {
    // Counts endpoint failing means the backend itself is down, not just
    // the camera — leave the last known numbers on screen rather than
    // blanking them, and let the status poll surface the problem.
    console.error("counts poll failed", err);
  }
}

async function pollStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();

    el.statusDot.classList.toggle("connected", !!data.camera_connected);
    el.statusText.textContent = data.camera_connected
      ? `Live · ${data.fps ?? 0} fps`
      : "Camera disconnected";
    el.videoPanel.classList.toggle("offline", !data.camera_connected);
  } catch (err) {
    el.statusDot.classList.remove("connected");
    el.statusText.textContent = "Backend unreachable";
    console.error("status poll failed", err);
  }
}

// --- Reset button: requires two taps (arm, then confirm) so a stray
// touch mid-drafting can't silently wipe the day's counts. ---
let resetArmed = false;
let resetArmTimer = null;

function disarmReset() {
  resetArmed = false;
  el.resetButton.classList.remove("armed");
  el.resetButton.textContent = "Reset counts";
  if (resetArmTimer) {
    clearTimeout(resetArmTimer);
    resetArmTimer = null;
  }
}

el.resetButton.addEventListener("click", async () => {
  if (!resetArmed) {
    resetArmed = true;
    el.resetButton.classList.add("armed");
    el.resetButton.textContent = "Tap again to confirm";
    resetArmTimer = setTimeout(disarmReset, RESET_ARM_TIMEOUT_MS);
    return;
  }

  disarmReset();
  try {
    const res = await fetch("/api/reset", { method: "POST" });
    if (!res.ok) throw new Error(`status ${res.status}`);
    await pollCounts();
  } catch (err) {
    console.error("reset failed", err);
  }
});

pollCounts();
pollStatus();
setInterval(pollCounts, COUNTS_POLL_MS);
setInterval(pollStatus, STATUS_POLL_MS);
