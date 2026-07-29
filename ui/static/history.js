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
let sessionsCache = {}; // mob_id -> array of session records, fetched lazily on expand

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function formatRate(total, durationSeconds) {
  // Same 30s floor used on Active and Session Stats -- a very short
  // session's rate isn't a meaningful number, just noise.
  if (durationSeconds < 30) return "—";
  const perHour = Math.round(total / (durationSeconds / 3600));
  return `${perHour.toLocaleString()} / hr`;
}

async function loadSessionsForMob(mobId, container) {
  if (sessionsCache[mobId]) {
    renderSessions(sessionsCache[mobId], container);
    return;
  }
  container.innerHTML = '<div class="history-sessions-loading">Loading sessions…</div>';
  try {
    const res = await fetch(`/api/sessions?mob_id=${encodeURIComponent(mobId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    sessionsCache[mobId] = data.sessions || [];
    renderSessions(sessionsCache[mobId], container);
  } catch (err) {
    console.error("failed to load sessions for mob", mobId, err);
    container.innerHTML = '<div class="history-sessions-loading">Couldn\'t load past sessions.</div>';
  }
}

function renderSessions(sessions, container) {
  if (sessions.length === 0) {
    container.innerHTML = '<div class="history-sessions-loading">No individual sessions recorded.</div>';
    return;
  }
  container.innerHTML = sessions.map((s) => `
    <div class="history-session-row">
      <span class="history-session-date">${formatShortDate(s.ended_at)}</span>
      <span class="history-session-duration">${formatDuration(s.duration_seconds)}</span>
      <span class="history-session-total">${s.total}</span>
      <span class="history-session-rate">${formatRate(s.total, s.duration_seconds)}</span>
    </div>
  `).join("");
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
    delete sessionsCache[mobId];
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

    const sessionsSection = document.createElement("div");
    sessionsSection.className = "history-sessions";
    sessionsSection.innerHTML = `
      <div class="history-sessions-header">
        <span>Date</span><span>Duration</span><span>Count</span><span>Rate</span>
      </div>
      <div class="history-sessions-list"></div>
    `;
    if (isExpanded) {
      loadSessionsForMob(mob.id, sessionsSection.querySelector(".history-sessions-list"));
    }

    card.appendChild(row);
    card.appendChild(breakdown);
    card.appendChild(sessionsSection);
    els.list.appendChild(card);
  }
}

loadMobs();
