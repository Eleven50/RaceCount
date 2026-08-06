/*
  Gate count + which-gates selection, on the way to naming a new mob.
  Two steps shown/hidden in place (no page reload between them, for
  snappiness) rather than two separate routes -- POSTs the final
  selection to /api/gate-selection, then navigates to /start, which
  reads that selection server-side to know which label fields to show.
*/

const GATE_LABELS = { left: "Left", straight: "Straight", right: "Right" };

const WHICH_OPTIONS = {
  1: [["left"], ["straight"], ["right"]],
  2: [["left", "straight"], ["left", "right"], ["straight", "right"]],
};

const els = {
  stepCount: document.getElementById("stepCount"),
  stepWhich: document.getElementById("stepWhich"),
  whichTitle: document.getElementById("whichTitle"),
  whichChoices: document.getElementById("whichChoices"),
  whichBackBtn: document.getElementById("whichBackBtn"),
};

function describeGates(gates) {
  return gates.map((g) => GATE_LABELS[g]).join(" + ");
}

async function submitSelection(gates) {
  try {
    const res = await fetch("/api/gate-selection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gates }),
    });
    if (!res.ok) {
      const data = await res.json();
      alert(data.error || "Couldn't save gate selection.");
      return;
    }
    window.rcNavigate("/start");
  } catch (err) {
    console.error("failed to submit gate selection", err);
    alert("Couldn't reach RaceCount to save your selection.");
  }
}

function showWhichStep(count) {
  els.stepCount.style.display = "none";
  els.stepWhich.style.display = "";
  els.whichTitle.textContent = count === 1 ? "Which gate?" : "Which two gates?";
  els.whichChoices.innerHTML = "";

  for (const combo of WHICH_OPTIONS[count]) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "sg-choice-btn sg-choice-btn-wide";
    btn.textContent = describeGates(combo);
    btn.addEventListener("click", () => submitSelection(combo));
    els.whichChoices.appendChild(btn);
  }
}

document.querySelectorAll("#stepCount .sg-choice-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const count = parseInt(btn.dataset.count, 10);
    if (count === 3) {
      submitSelection(["left", "straight", "right"]);
    } else {
      showWhichStep(count);
    }
  });
});

els.whichBackBtn.addEventListener("click", () => {
  els.stepWhich.style.display = "none";
  els.stepCount.style.display = "";
});
