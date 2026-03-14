/*
  Start screen: two modes toggled by the top bar.
  - "create": type a mob name + 3 gate labels, Confirm creates a brand
    new mob (counts start at 0) and makes it the active one.
  - "previous": pick an existing mob from the list, Confirm makes it the
    active one without touching its accumulated counts.

  Either way, a successful Confirm hands off to /active — the Active
  screen doesn't yet consume the selected mob for live counting (that's
  further pipeline wiring, not a Start-screen concern), but the
  selection itself is already recorded server-side via /api/mobs/active.
*/

const els = {
  modeCreateBtn: document.getElementById("modeCreateBtn"),
  modeUsePreviousBtn: document.getElementById("modeUsePreviousBtn"),
  createPanel: document.getElementById("createPanel"),
  previousPanel: document.getElementById("previousPanel"),
  mobNameInput: document.getElementById("mobNameInput"),
  leftNameInput: document.getElementById("leftNameInput"),
  straightNameInput: document.getElementById("straightNameInput"),
  rightNameInput: document.getElementById("rightNameInput"),
  mobList: document.getElementById("mobList"),
  confirmBtn: document.getElementById("confirmBtn"),
  toast: document.getElementById("startToast"),
};

let mode = "create"; // "create" | "previous"
let selectedMobId = null;
let mobsLoaded = false;
let mobsCache = [];

function showToast(message, kind) {
  els.toast.textContent = message;
  els.toast.className = `start-toast ${kind || ""}`;
  els.toast.style.display = "";
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => {
    els.toast.style.display = "none";
  }, 3500);
}

// ---------- Mode switching ----------

function setMode(newMode) {
  mode = newMode;
  els.modeCreateBtn.classList.toggle("active", mode === "create");
  els.modeUsePreviousBtn.classList.toggle("active", mode === "previous");
  els.createPanel.style.display = mode === "create" ? "" : "none";
  els.previousPanel.style.display = mode === "previous" ? "" : "none";

  if (mode === "previous" && !mobsLoaded) {
    loadMobs();
  }
  updateConfirmState();
}

els.modeCreateBtn.addEventListener("click", () => setMode("create"));
els.modeUsePreviousBtn.addEventListener("click", () => setMode("previous"));

// ---------- Create mode validation ----------

function createFieldsValid() {
  return (
    els.mobNameInput.value.trim().length > 0 &&
    els.leftNameInput.value.trim().length > 0 &&
    els.straightNameInput.value.trim().length > 0 &&
    els.rightNameInput.value.trim().length > 0
  );
}

function updateConfirmState() {
  if (mode === "create") {
    els.confirmBtn.textContent = "Create Mob";
    els.confirmBtn.disabled = !createFieldsValid();
  } else {
    const selected = selectedMobId ? mobsCache.find((m) => m.id === selectedMobId) : null;
    els.confirmBtn.textContent = selected ? `Continue "${selected.name}"` : "Select a mob above";
    els.confirmBtn.disabled = !selected;
  }
}

[els.mobNameInput, els.leftNameInput, els.straightNameInput, els.rightNameInput].forEach((input) => {
  input.addEventListener("input", updateConfirmState);
});

// ---------- Use Previous: mob list ----------

async function loadMobs() {
  els.mobList.innerHTML = '<div class="start-mob-list-loading">Loading previous mobs…</div>';
  try {
    const res = await fetch("/api/mobs");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    mobsCache = data.mobs || [];
    mobsLoaded = true;
    renderMobList();
  } catch (err) {
    console.error("failed to load mobs", err);
    els.mobList.innerHTML = '<div class="start-mob-list-empty">Could not load previous mobs. Pull down or reopen this screen to retry.</div>';
  }
}

// formatShortDate() comes from brand.js, loaded on every screen via base.html

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function renderMobList() {
  if (mobsCache.length === 0) {
    els.mobList.innerHTML = '<div class="start-mob-list-empty">No previous mobs yet — create one first.</div>';
    return;
  }

  els.mobList.innerHTML = "";
  for (const mob of mobsCache) {
    const row = document.createElement("div");
    row.className = "start-mob-row" + (mob.id === selectedMobId ? " selected" : "");
    row.dataset.mobId = mob.id;
    row.innerHTML = `
      <div class="start-mob-row-main">
        <div class="start-mob-row-name">${escapeHtml(mob.name)}</div>
        <div class="start-mob-row-labels">
          <span class="gl">${escapeHtml(mob.gate_labels.left)}</span> ·
          <span class="gs">${escapeHtml(mob.gate_labels.straight)}</span> ·
          <span class="gr">${escapeHtml(mob.gate_labels.right)}</span>
        </div>
      </div>
      <div class="start-mob-row-meta">
        <div class="start-mob-row-total">${mob.total}</div>
        <div class="start-mob-row-date">${formatShortDate(mob.updated_at)}</div>
      </div>
    `;
    row.addEventListener("click", () => {
      selectedMobId = mob.id;
      renderMobList();
      updateConfirmState();
    });
    els.mobList.appendChild(row);
  }
}

// ---------- Confirm ----------

els.confirmBtn.addEventListener("click", async () => {
  els.confirmBtn.disabled = true;

  try {
    if (mode === "create") {
      const payload = {
        name: els.mobNameInput.value.trim(),
        gate_labels: {
          left: els.leftNameInput.value.trim(),
          straight: els.straightNameInput.value.trim(),
          right: els.rightNameInput.value.trim(),
        },
      };
      const res = await fetch("/api/mobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      showToast(`Mob "${data.mob.name}" created.`, "");
    } else {
      const res = await fetch(`/api/mobs/${selectedMobId}/select`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      showToast(`Continuing "${data.mob.name}".`, "");
    }

    setTimeout(() => {
      window.location.href = "/active";
    }, 700);
  } catch (err) {
    console.error("confirm failed", err);
    showToast(`Could not continue: ${err.message}`, "error");
    updateConfirmState(); // re-enable if still valid
  }
});

updateConfirmState();
