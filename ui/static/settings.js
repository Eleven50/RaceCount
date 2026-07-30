/*
  Settings screen. Theme changes apply immediately via /api/settings/theme
  and take effect on next navigation (base.html reads the persisted value
  server-side on every render, so no client-side re-theming logic is
  needed here beyond updating this page's own toggle state).

  The Debug section's PIN check happens server-side
  (/api/settings/verify-pin) rather than comparing against a value baked
  into this page -- the actual PIN never needs to reach the client at
  all this way, unlike a client-side comparison would require.
*/

const els = {
  themeButtons: document.querySelectorAll(".settings-theme-btn"),
  pinInput: document.getElementById("pinInput"),
  unlockBtn: document.getElementById("unlockBtn"),
  pinError: document.getElementById("pinError"),
  debugLocked: document.getElementById("debugLocked"),
  debugUnlocked: document.getElementById("debugUnlocked"),
  cameraInfo: document.getElementById("debugCameraInfo"),
  piIp: document.getElementById("debugPiIp"),
  cpuTemp: document.getElementById("debugCpuTemp"),
  memory: document.getElementById("debugMemory"),
  disk: document.getElementById("debugDisk"),
  load: document.getElementById("debugLoad"),
  uptime: document.getElementById("debugUptime"),
  runTestsBtn: document.getElementById("runTestsBtn"),
  testOutput: document.getElementById("testOutput"),
};

function formatUptime(seconds) {
  if (seconds == null) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function updateThemeButtons() {
  const active = document.documentElement.getAttribute("data-theme") || "dark";
  els.themeButtons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.theme === active);
  });
}

els.themeButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const theme = btn.dataset.theme;
    document.documentElement.setAttribute("data-theme", theme); // instant feedback
    updateThemeButtons();
    // Fire-and-forget: the visual change above is already complete, this
    // fetch is purely for persistence. Not awaiting it means tapping Back
    // right after doesn't have to wait on it either.
    fetch("/api/settings/theme", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ theme }),
    }).catch((err) => {
      console.error("failed to persist theme", err);
    });
  });
});

updateThemeButtons();

async function loadDebugInfo() {
  try {
    const res = await fetch("/api/debug/info");
    const data = await res.json();
    els.cameraInfo.textContent = `${data.camera_user}@${data.camera_ip}:${data.camera_port}`;
    els.piIp.textContent = data.pi_ip;
    els.cpuTemp.textContent = data.cpu_temp_c != null ? `${data.cpu_temp_c}\u00b0C` : "not available";
    els.memory.textContent = data.memory
      ? `${data.memory.available_mb.toLocaleString()} MB free of ${data.memory.total_mb.toLocaleString()} MB (${data.memory.used_pct}% used)`
      : "not available";
    els.disk.textContent = data.disk
      ? `${data.disk.free_gb} GB free of ${data.disk.total_gb} GB (${data.disk.used_pct}% used)`
      : "not available";
    els.load.textContent = data.load_average
      ? `${data.load_average["1min"]} / ${data.load_average["5min"]} / ${data.load_average["15min"]}`
      : "not available";
    els.uptime.textContent = formatUptime(data.uptime_seconds);
  } catch (err) {
    console.error("failed to load debug info", err);
    els.cameraInfo.textContent = "Couldn't load.";
    els.piIp.textContent = "—";
  }
}

els.unlockBtn.addEventListener("click", async () => {
  const pin = els.pinInput.value.trim();
  if (!pin) return;
  els.unlockBtn.disabled = true;
  try {
    const res = await fetch("/api/settings/verify-pin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin }),
    });
    if (res.ok) {
      els.debugLocked.style.display = "none";
      els.debugUnlocked.style.display = "";
      loadDebugInfo();
    } else {
      els.pinError.style.display = "";
      els.pinInput.value = "";
      els.pinInput.focus();
    }
  } catch (err) {
    console.error("PIN verification failed", err);
    els.pinError.textContent = "Couldn't reach RaceCount to check the PIN.";
    els.pinError.style.display = "";
  } finally {
    els.unlockBtn.disabled = false;
  }
});

els.runTestsBtn.addEventListener("click", async () => {
  els.runTestsBtn.disabled = true;
  els.runTestsBtn.textContent = "Running…";
  els.testOutput.style.display = "";
  els.testOutput.textContent = "Running tests, this takes a few seconds…";
  try {
    const res = await fetch("/api/debug/run-tests", { method: "POST" });
    const data = await res.json();
    els.testOutput.textContent = data.output;
    els.testOutput.classList.toggle("settings-log-pass", !!data.passed);
    els.testOutput.classList.toggle("settings-log-fail", !data.passed);
  } catch (err) {
    console.error("test run failed", err);
    els.testOutput.textContent = "Couldn't reach RaceCount to run tests.";
    els.testOutput.classList.remove("settings-log-pass");
    els.testOutput.classList.add("settings-log-fail");
  } finally {
    els.runTestsBtn.disabled = false;
    els.runTestsBtn.textContent = "Run";
  }
});
