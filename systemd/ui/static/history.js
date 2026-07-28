/*
  History: lists mobs (most-recently-updated first, straight from
  MobStore.list_mobs() via GET /api/mobs), matching the original sketch
  — a collapsed name/date/total row that expands to show the per-gate
  breakdown using that mob's own labels. Delete uses the same tap-once-
  to-arm, tap-again-to-confirm pattern used elsewhere in this app (the
  calibration page's Undo, Start's mob-name validation) rather than a
  native confirm() dialog, which doesn't fit a kiosk touchscreen well.
*/

const els = {
  list: document.getElementById("historyList"),
};

let mobsCache = [];
let expandedId = null;
let armedDeleteId = null;
let armedDeleteTimer = null;

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function loadMobs() {
  els.list.innerHTML = '<div class="history-loading">Loading mobs…</div>';
  try {
    const res = await fetch("/api/mobs");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    mobsCache = data.mobs || [];
    render();
  } catch (err) {
    console.error("failed to load mobs", err);
    els.list.innerHTML = '<div class="history-empty">Could not load mob history. Try reopening this screen.</div>';
  }
}

function disarmDelete() {
  armedDeleteId = null;
  if (armedDeleteTimer) {
    clearTimeout(armedDeleteTimer);
    armedDeleteTimer = null;
  }
}

async function confirmDelete(mobId) {
  try {
    const res = await fetch(`/api/mobs/${mobId}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    mobsCache = mobsCache.filter((m) => m.id !== mobId);
    if (expandedId === mobId) expandedId = null;
    disarmDelete();
    render();
  } catch (err) {
    console.error("delete failed", err);
    disarmDelete();
    render();
    alert(`Couldn't delete this mob: ${err.message}`);
  }
}

function render() {
  if (mobsCache.length === 0) {
    els.list.innerHTML = '<div class="history-empty">No mobs yet — start a session to create one.</div>';
    return;
  }

  els.list.innerHTML = "";
  for (const mob of mobsCache) {
    const isExpanded = mob.id === expandedId;
    const isArmed = mob.id === armedDeleteId;

    const card = document.createElement("div");
    card.className = "history-card" + (isExpanded ? " expanded" : "");

    const row = document.createElement("div");
    row.className = "history-card-row";
    row.innerHTML = `
      <div class="history-card-main">
        <div class="history-card-name">${escapeHtml(mob.name)}</div>
        <div class="history-card-date">${formatShortDate(mob.updated_at)}</div>
      </div>
      <div class="history-card-total">
        <div class="history-card-total-value">${mob.total}</div>
        <div class="history-card-total-label">Total</div>
      </div>
      <span class="history-card-chevron">&rsaquo;</span>
    `;
    row.addEventListener("click", () => {
      expandedId = isExpanded ? null : mob.id;
      disarmDelete();
      render();
    });

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "history-card-delete" + (isArmed ? " armed" : "");
    deleteBtn.textContent = isArmed ? "Confirm delete" : "\u2715";
    deleteBtn.setAttribute("aria-label", "Delete mob");
    deleteBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (isArmed) {
        confirmDelete(mob.id);
      } else {
        armedDeleteId = mob.id;
        clearTimeout(armedDeleteTimer);
        armedDeleteTimer = setTimeout(() => {
          disarmDelete();
          render();
        }, 3500);
        render();
      }
    });
    row.appendChild(deleteBtn);

    const breakdown = document.createElement("div");
    breakdown.className = "history-card-breakdown";
    breakdown.innerHTML = ["left", "straight", "right"].map((dir) => `
      <div class="history-breakdown-row" data-dir="${dir}">
        <span class="history-breakdown-label">${escapeHtml(mob.gate_labels[dir])}</span>
        <span class="history-breakdown-count">${mob.counts[dir]}</span>
      </div>
    `).join("");

    card.appendChild(row);
    card.appendChild(breakdown);
    els.list.appendChild(card);
  }
}

loadMobs();
