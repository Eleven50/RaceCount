/*
  Resilient internal navigation, v2.

  v1 checked the backend before every single navigation, blocking on a
  fetch even when everything was healthy -- which is the overwhelming
  majority of the time. Even at a tight 350ms timeout, that's still a
  real network round-trip added to the critical path of every screen
  switch, and on a Pi (much weaker than a dev machine) it was enough to
  make normal navigation feel sluggish. That was solving a rare problem
  (a brief blip during an auto-update restart) by taxing the common
  case (completely ordinary navigation) every time.

  v2 instead: a lightweight background poller keeps a continuously-
  updated "is the backend alive" flag, independent of navigation
  entirely. Clicking a link checks that already-known flag -- if it's
  healthy (the default, and the overwhelming common case), navigation
  is instant, exactly like a plain <a href>, zero added latency. The
  protective "Reconnecting…" overlay only ever appears when the
  poller has ALREADY detected a real problem, not as a tax on every
  click waiting to find out.
*/

const POLL_INTERVAL_MS = 2500;
const CHECK_TIMEOUT_MS = 800;
const RETRY_DELAY_MS = 400;
const MAX_QUIET_RETRIES = 6; // ~2.4s of quiet retrying once a problem is
                             // already known, before saying so explicitly

// Optimistic default: the page we're currently on just loaded
// successfully, so the backend was alive moments ago. Don't distrust
// that until the poller actually says otherwise.
let backendAlive = true;
let overlayEl = null;

function isInternalNavLink(a) {
  if (!a || a.tagName !== "A") return false;
  if (a.target === "_blank") return false;
  if (a.hasAttribute("download")) return false;
  if (a.classList.contains("disabled")) return false; // e.g. the back link during an active session
  const href = a.getAttribute("href");
  return !!href && href.startsWith("/");
}

async function checkBackendAliveOnce() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), CHECK_TIMEOUT_MS);
  try {
    const res = await fetch("/api/status", { signal: controller.signal, cache: "no-store" });
    clearTimeout(timeout);
    return res.ok;
  } catch (e) {
    clearTimeout(timeout);
    return false;
  }
}

async function backgroundPoll() {
  backendAlive = await checkBackendAliveOnce();
}
// Kick off an immediate check (don't wait on it, it just updates the
// flag for whenever the next click happens), then keep polling.
backgroundPoll();
setInterval(backgroundPoll, POLL_INTERVAL_MS);

function showOverlay(html) {
  if (!overlayEl) {
    overlayEl = document.createElement("div");
    overlayEl.id = "rc-nav-overlay";
    document.body.appendChild(overlayEl);
  }
  overlayEl.innerHTML = html;
}

function hideOverlay() {
  if (overlayEl) {
    overlayEl.remove();
    overlayEl = null;
  }
}

async function waitForRecoveryThenNavigate(href) {
  showOverlay('<div class="rc-nav-box"><div class="rc-nav-spinner"></div><div>Reconnecting\u2026</div></div>');

  for (let attempt = 0; attempt < MAX_QUIET_RETRIES; attempt++) {
    await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
    const alive = await checkBackendAliveOnce();
    backendAlive = alive;
    if (alive) {
      hideOverlay();
      window.location.href = href;
      return;
    }
  }

  showOverlay(
    '<div class="rc-nav-box"><div>Still trying to reach RaceCount\u2026</div>' +
    '<button type="button" id="rc-nav-retry">Try again</button></div>'
  );
  document.getElementById("rc-nav-retry").addEventListener("click", () => {
    hideOverlay();
    waitForRecoveryThenNavigate(href);
  });
}

document.addEventListener("click", (e) => {
  const a = e.target.closest("a");
  if (!isInternalNavLink(a)) return;
  e.preventDefault();
  const href = a.getAttribute("href");

  if (backendAlive) {
    // Common case, essentially always: navigate immediately, no check,
    // no delay -- identical to what a plain <a href> would do.
    window.location.href = href;
    return;
  }

  // The background poller has already detected a problem -- show the
  // overlay right away rather than making the user wait through yet
  // another check first to find out what the poller already knows.
  waitForRecoveryThenNavigate(href);
});
