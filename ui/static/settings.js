/*
  Settings screen. Theme changes apply immediately via /api/settings/theme
  and take effect on next navigation (base.html reads the persisted value
  server-side on every render, so no client-side re-theming logic is
  needed here beyond updating this page's own toggle state).

  The Advanced section's PIN check happens server-side
  (/api/settings/verify-pin) rather than comparing against a value baked
  into this page -- the actual PIN never needs to reach the client at
  all this way, unlike a client-side comparison would require.
*/

const els = {
  themeButtons: document.querySelectorAll(".settings-theme-btn"),
  pinInput: document.getElementById("pinInput"),
  unlockBtn: document.getElementById("unlockBtn"),
  pinError: document.getElementById("pinError"),
  advancedLocked: document.getElementById("advancedLocked"),
  advancedUnlocked: document.getElementById("advancedUnlocked"),
};

const currentTheme = document.documentElement.getAttribute("data-theme") || "dark";

function updateThemeButtons() {
  const active = document.documentElement.getAttribute("data-theme") || "dark";
  els.themeButtons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.theme === active);
  });
}

els.themeButtons.forEach((btn) => {
  btn.addEventListener("click", async () => {
    const theme = btn.dataset.theme;
    document.documentElement.setAttribute("data-theme", theme); // instant feedback
    updateThemeButtons();
    try {
      await fetch("/api/settings/theme", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme }),
      });
    } catch (err) {
      console.error("failed to persist theme", err);
    }
  });
});

updateThemeButtons();

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
      els.advancedLocked.style.display = "none";
      els.advancedUnlocked.style.display = "";
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
