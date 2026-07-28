/*
  Active screen. Polls /api/session/status (the single source of truth
  for calibration state + active mob + whether a session is running) to
  drive both the mob banner and the Start/End Session button states —
  the server enforces the same two conditions independently on
  /api/session/start, so this is a UX convenience, not the real gate.
*/

const STATUS_POLL_MS = 2000;
const COUNTS_POLL_MS = 700;
const SESSION_POLL_MS = 1500;

const els = {
  videoPanel: document.getElementById("videoPanel"),
  statusDot: document.getElementById("statusDot"),
  statusText: document.getElementById("statusText"),
  mobBanner: document.getElementById("mobBanner"),
  countLeft: document.getElementById("countLeft"),
  countStraight: document.getElementById("countStraight"),
  countRight: document.getElementById("countRight"),
  labelLeft: document.getElementById("labelLeft"),
  labelStraight: document.getElementById("labelStraight"),
  labelRight: document.getElementById("labelRight"),
  startBtn: document.getElementById("startSessionBtn"),
  endBtn: document.getElementById("endSessionBtn"),
  blockedReason: document.getElementById("blockedReason"),
  toast: document.getElementById("activeToast"),
  headerBackLink: document.getElementById("headerBackLink"),
};

let lastCounts = { left: 0, straight: 0, right: 0 };
let currentMob = null;
let sessionActive = false;

function showToast(message) {
  els.toast.textContent = message;
  els.toast.style.display = "";
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => {
    els.toast.style.display = "none";
  }, 3000);
}

function flashCircle(el) {
  el.classList.add("just-counted");
  setTimeout(() => el.classList.remove("just-counted"), 220);
}

// ---------- Counts ----------

async function pollCounts() {
  try {
    const res = await fetch("/api/counts");
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();
    const counts = data.counts || {};

    for (const [dir, el] of [["left", els.countLeft], ["straight", els.countStraight], ["right", els.countRight]]) {
      const value = counts[dir] ?? 0;
      if (value !== lastCounts[dir]) flashCircle(el);
      el.textContent = value;
    }
    lastCounts = counts;
  } catch (err) {
    console.error("counts poll failed", err);
  }
}

// ---------- Camera status ----------

async function pollStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();
    els.statusDot.classList.toggle("connected", !!data.camera_connected);
    els.statusText.textContent = data.camera_connected ? `Live · ${data.fps ?? 0} fps` : "Camera disconnected";
    els.videoPanel.classList.toggle("offline", !data.camera_connected);
  } catch (err) {
    els.statusDot.classList.remove("connected");
    els.statusText.textContent = "Backend unreachable";
  }
}

// ---------- Session status: drives mob banner + button gating ----------

function updateMobBanner() {
  if (currentMob) {
    els.mobBanner.classList.add("has-mob");
    els.mobBanner.classList.remove("no-mob");
    els.mobBanner.querySelector(".active-mob-banner-label").textContent = currentMob.name;
    els.labelLeft.textContent = currentMob.gate_labels.left;
    els.labelStraight.textContent = currentMob.gate_labels.straight;
    els.labelRight.textContent = currentMob.gate_labels.right;
  } else {
    els.mobBanner.classList.remove("has-mob");
    els.mobBanner.classList.add("no-mob");
    els.mobBanner.querySelector(".active-mob-banner-label").textContent = "No mob selected";
    els.labelLeft.textContent = "Left";
    els.labelStraight.textContent = "Straight";
    els.labelRight.textContent = "Right";
  }
}

function updateButtons(calibrated) {
  if (sessionActive) {
    els.startBtn.disabled = true;
    els.startBtn.textContent = "Session running…";
    els.endBtn.disabled = false;
    els.blockedReason.textContent = "";
    setHeaderBackEnabled(false);
    return;
  }

  els.endBtn.disabled = true;
  els.startBtn.textContent = "Start Session";
  setHeaderBackEnabled(true);

  if (!calibrated) {
    els.startBtn.disabled = true;
    els.blockedReason.textContent = "Calibrate the gates before starting a session.";
  } else if (!currentMob) {
    els.startBtn.disabled = true;
    els.blockedReason.textContent = "Select a mob on the Start screen before starting a session.";
  } else {
    els.startBtn.disabled = false;
    els.blockedReason.textContent = "";
  }
}

function setHeaderBackEnabled(enabled) {
  // Navigating straight Home mid-session would leave detection running
  // in the background with no way back to it short of typing the URL —
  // End Session is the only way out of an active session, same as
  // Start/End themselves. Once no session is running, this is exactly
  // the plain "just let me leave" escape hatch that was otherwise
  // missing before a session's ever been started.
  if (!els.headerBackLink) return;
  els.headerBackLink.classList.toggle("disabled", !enabled);
  els.headerBackLink.title = enabled ? "Back to Home" : "End the session first";
}

async function pollSession() {
  try {
    const res = await fetch("/api/session/status");
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();
    sessionActive = !!data.active;
    currentMob = data.mob || null;
    updateMobBanner();
    updateButtons(!!data.calibrated);
  } catch (err) {
    console.error("session status poll failed", err);
  }
}

// ---------- Start / End Session actions ----------

els.startBtn.addEventListener("click", async () => {
  els.startBtn.disabled = true;
  try {
    const res = await fetch("/api/session/start", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    sessionActive = true;
    updateButtons(true);
    showToast("Session started.");
  } catch (err) {
    console.error("start session failed", err);
    showToast(err.message || "Could not start session.");
    pollSession(); // resync real state/reason from the server
  }
});

els.endBtn.addEventListener("click", async () => {
  els.endBtn.disabled = true;
  try {
    const res = await fetch("/api/session/end", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    showToast("Session ended.");
    const destination = data.session_record_id ? `/session-stats/${data.session_record_id}` : "/";
    setTimeout(() => {
      window.location.href = destination;
    }, 600);
  } catch (err) {
    console.error("end session failed", err);
    showToast(err.message || "Could not end session.");
    pollSession();
  }
});

pollCounts();
pollStatus();
pollSession();
setInterval(pollCounts, COUNTS_POLL_MS);
setInterval(pollStatus, STATUS_POLL_MS);
setInterval(pollSession, SESSION_POLL_MS);
