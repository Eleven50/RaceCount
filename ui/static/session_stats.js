/*
  Session Stats: reads the record_id baked into the page by the Flask
  route (/session-stats/<record_id>), fetches that session's snapshot
  (mob name, gate labels, this session's counts, duration — all frozen
  at the moment End Session was pressed), plus a live fetch of the
  mob's current cumulative total so this can show "counted this
  session" alongside "mob total is now X" without conflating the two.
*/

const recordId = document.querySelector(".stats-screen").dataset.recordId;

const els = {
  loading: document.getElementById("statsLoading"),
  body: document.getElementById("statsBody"),
  error: document.getElementById("statsError"),
  errorDetail: document.getElementById("statsErrorDetail"),
  mobName: document.getElementById("statsMobName"),
  mobNameInline: document.getElementById("statsMobNameInline"),
  date: document.getElementById("statsDate"),
  duration: document.getElementById("statsDuration"),
  labelLeft: document.getElementById("statsLabelLeft"),
  labelStraight: document.getElementById("statsLabelStraight"),
  labelRight: document.getElementById("statsLabelRight"),
  countLeft: document.getElementById("statsCountLeft"),
  countStraight: document.getElementById("statsCountStraight"),
  countRight: document.getElementById("statsCountRight"),
  sessionTotal: document.getElementById("statsSessionTotal"),
  mobTotal: document.getElementById("statsMobTotal"),
};

function showError(message) {
  els.loading.style.display = "none";
  els.body.style.display = "none";
  els.errorDetail.textContent = message;
  els.error.style.display = "";
}

async function load() {
  if (!recordId) {
    showError("No session to show.");
    return;
  }

  let session;
  try {
    const res = await fetch(`/api/sessions/${recordId}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    session = data.session;
  } catch (err) {
    console.error("failed to load session record", err);
    showError("This session's record couldn't be loaded. It may not exist.");
    return;
  }

  els.mobName.textContent = session.mob_name;
  els.mobNameInline.textContent = session.mob_name;
  els.date.textContent = formatShortDate(session.ended_at);
  els.duration.textContent = formatDuration(session.duration_seconds);

  els.labelLeft.textContent = session.gate_labels.left;
  els.labelStraight.textContent = session.gate_labels.straight;
  els.labelRight.textContent = session.gate_labels.right;

  els.countLeft.textContent = session.counts.left;
  els.countStraight.textContent = session.counts.straight;
  els.countRight.textContent = session.counts.right;
  els.sessionTotal.textContent = session.total;

  els.loading.style.display = "none";
  els.body.style.display = "";

  // Best-effort: the mob's live cumulative total. If this fails (mob
  // since deleted, whatever), the rest of the page is still useful, so
  // this failure is silent rather than blocking the page on it.
  try {
    const mobRes = await fetch(`/api/mobs/${session.mob_id}`);
    if (mobRes.ok) {
      const mobData = await mobRes.json();
      els.mobTotal.textContent = mobData.mob.total;
    } else {
      els.mobTotal.textContent = "—";
    }
  } catch (err) {
    console.debug("mob total fetch failed (non-fatal)", err);
    els.mobTotal.textContent = "—";
  }
}

load();
