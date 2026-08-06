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
// Below this, total/elapsed is too noisy to be a meaningful rate (3 sheep
// in the first 5 seconds isn't "2160/hr") -- show nothing until there's
// enough elapsed time for the number to actually mean something.
const MIN_RATE_SECONDS = 30;

const els = {
  videoPanel: document.getElementById("videoPanel"),
  statusDot: document.getElementById("statusDot"),
  statusText: document.getElementById("statusText"),
  mobBanner: document.getElementById("mobBanner"),
  activeCounts: document.getElementById("activeCounts"),
  rate: document.getElementById("activeRate"),
  rateValue: document.getElementById("activeRateValue"),
  startBtn: document.getElementById("startSessionBtn"),
  endBtn: document.getElementById("endSessionBtn"),
  blockedReason: document.getElementById("blockedReason"),
  toast: document.getElementById("activeToast"),
  headerBackLink: document.getElementById("headerBackLink"),
  mismatchOverlay: document.getElementById("mismatchOverlay"),
  mismatchDetail: document.getElementById("mismatchDetail"),
  mismatchRecalBtn: document.getElementById("mismatchRecalBtn"),
  mismatchDismissBtn: document.getElementById("mismatchDismissBtn"),
};

// Whichever gates the server actually rendered for this page load (the
// active mob's own gates, or all 3 as a placeholder before any mob is
// selected -- see server.py's /active route). The DOM only contains
// rows for these gates now, not always all 3 with some hidden, so
// every lookup below is built from this rather than hardcoding
// left/straight/right and assuming all three exist.
const ACTIVE_GATES = JSON.parse(els.activeCounts.dataset.activeGates || '["left","straight","right"]');
const GATE_ROW_ELS = Object.fromEntries(ACTIVE_GATES.map((gate) => [gate, {
  row: document.querySelector(`.active-count-row[data-dir="${gate}"]`),
  circle: document.getElementById(`count${gate.charAt(0).toUpperCase()}${gate.slice(1)}`),
  label: document.getElementById(`label${gate.charAt(0).toUpperCase()}${gate.slice(1)}`),
}]));

let lastCounts = Object.fromEntries(ACTIVE_GATES.map((g) => [g, 0]));
let currentMob = null;
let sessionActive = false;
let sessionStartedAt = null;

function updateRateDisplay(totalCount) {
  if (!sessionActive || !sessionStartedAt) {
    els.rate.style.display = "none";
    return;
  }
  const elapsedSeconds = Date.now() / 1000 - sessionStartedAt;
  if (elapsedSeconds < MIN_RATE_SECONDS) {
    els.rate.style.display = "none";
    return;
  }
  const perHour = Math.round(totalCount / (elapsedSeconds / 3600));
  els.rateValue.textContent = perHour.toLocaleString();
  els.rate.style.display = "";
}


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

    let total = 0;
    for (const gate of ACTIVE_GATES) {
      const value = counts[gate] ?? 0;
      total += value;
      if (!GATE_ROW_ELS[gate]) continue;
      if (value !== lastCounts[gate]) flashCircle(GATE_ROW_ELS[gate].circle);
      GATE_ROW_ELS[gate].circle.textContent = value;
    }
    lastCounts = counts;
    updateRateDisplay(total);
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
    // Server already rendered exactly this mob's gates as the only
    // rows present (see server.py's /active route) -- just fill in
    // this mob's custom label text (e.g. "Selling" instead of "Left"),
    // no visibility toggling needed here anymore.
    for (const gate of ACTIVE_GATES) {
      if (GATE_ROW_ELS[gate] && gate in currentMob.gate_labels) {
        GATE_ROW_ELS[gate].label.textContent = currentMob.gate_labels[gate];
      }
    }
  } else {
    els.mobBanner.classList.remove("has-mob");
    els.mobBanner.classList.add("no-mob");
    els.mobBanner.querySelector(".active-mob-banner-label").textContent = "No mob selected";
    for (const gate of ACTIVE_GATES) {
      if (GATE_ROW_ELS[gate]) {
        GATE_ROW_ELS[gate].label.textContent = gate.charAt(0).toUpperCase() + gate.slice(1);
      }
    }
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
    sessionStartedAt = data.started_at || null;
    updateMobBanner();
    updateButtons(!!data.calibrated);
    if (!sessionActive) {
      els.rate.style.display = "none";
    }
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
    if (!res.ok) {
      if (data.error_type === "gate_mismatch") {
        showMismatchPrompt(data);
        return;
      }
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    sessionActive = true;
    updateButtons(true);
    showToast("Session started.");
  } catch (err) {
    console.error("start session failed", err);
    showToast(err.message || "Could not start session.");
    pollSession(); // resync real state/reason from the server
  }
});

function describeGateList(gates) {
  return gates.map((g) => g.charAt(0).toUpperCase() + g.slice(1)).join(" + ");
}

function showMismatchPrompt(data) {
  els.mismatchDetail.textContent =
    `This mob uses ${describeGateList(data.mob_gates)}, but the camera's currently calibrated for ` +
    `${describeGateList(data.calibrated_gates)}. Recalibrate to match this mob before starting.`;
  els.mismatchOverlay.style.display = "";
  els.startBtn.disabled = false;

  els.mismatchRecalBtn.onclick = async () => {
    els.mismatchRecalBtn.disabled = true;
    try {
      // Point /calibrate at exactly this mob's gates, not whatever's
      // currently calibrated (which is precisely the mismatched set) —
      // reuses the same gate-selection endpoint the Select Gates page
      // itself posts to, rather than a separate mechanism.
      await fetch("/api/gate-selection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gates: data.mob_gates }),
      });
      window.location.href = "/calibrate";
    } catch (err) {
      console.error("failed to set gate selection for recalibration", err);
      window.location.href = "/calibrate"; // still navigate -- worst case, /calibrate falls back sensibly
    }
  };

  els.mismatchDismissBtn.onclick = () => {
    els.mismatchOverlay.style.display = "none";
  };
}

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
