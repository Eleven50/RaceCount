/*
  Resilient internal navigation, v3.

  v1 checked the backend before every single navigation, blocking on a
  fetch even when everything was healthy -- taxing the common case
  (ordinary navigation) to guard against a rare one (a brief blip during
  an auto-update restart). v2 fixed that with a background poller and
  instant navigation on the known-healthy path.

  v3 fixes a real remaining gap: v2 only intercepted clicks on <a>
  tags, but plenty of navigation in this app happens as
  `window.location.href = ...` directly from JS -- after creating a
  mob, after ending a session, after saving a calibration, after
  picking gates on the Select Gates page. None of those were protected
  at all, so a backend blip at exactly one of those moments (which,
  unlike a random click, is *exactly* when the backend has just done
  real work and might be busiest) would still show the raw browser
  error page. This is genuinely why "sometimes" kept happening even
  after v2.

  window.rcNavigate(href) is now the one shared path every kind of
  navigation goes through -- the click-handler below calls it, and
  every other script's JS-driven redirects call it directly instead of
  setting window.location.href themselves.
*/

const POLL_INTERVAL_MS = 2500;
const CHECK_TIMEOUT_MS = 800;
const RETRY_DELAY_MS = 400;
const MAX_QUIET_RETRIES = 6; // ~2.4s of quiet retrying (just a spinner,
                             // no message) before saying anything --
                             // covers the common brief-blip case
const EXTENDED_RETRY_DELAY_MS = 2000; // slower cadence once it's genuinely
                                       // taking a while -- no need to
                                       // hammer /api/status every 400ms
                                       // for what might be a real restart
const MAX_EXTENDED_RETRIES = 60; // ~2 more minutes of patient, fully
                                  // automatic retrying on top of the quiet
                                  // phase -- covers a real systemd
                                  // restart (auto-update deploying a new
                                  // commit, or the watchdog firing),
                                  // which can genuinely take longer than
                                  // a few seconds on real Pi hardware.
                                  // The "Try again" button this phase
                                  // shows is a bonus for someone
                                  // impatient, not the only way to
                                  // recover -- this was a real gap
                                  // before: once the quiet phase gave
                                  // up, NOTHING kept retrying until a
                                  // human clicked.

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
// flag for whenever the next navigation happens), then keep polling.
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

  // Phase 1: quiet, tight retries -- covers the common case, a brief
  // blip that clears in a couple of seconds. No message beyond the
  // spinner, since this is expected to resolve almost immediately most
  // of the time.
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

  // Phase 2: it's taking longer than a brief blip -- likely a genuine
  // service restart (an auto-update deploying, or the watchdog firing),
  // which can take longer than a few seconds on real Pi hardware. Says
  // so, but keeps retrying automatically at a slower, less wasteful
  // cadence -- the manual button is there for someone who wants to
  // force an immediate recheck, not because retrying otherwise stops.
  showOverlay(
    '<div class="rc-nav-box"><div class="rc-nav-spinner"></div>' +
    '<div>Still trying to reach RaceCount\u2026</div>' +
    '<div class="rc-nav-sub">This can take a little longer right after an update.</div>' +
    '<button type="button" id="rc-nav-retry">Check now</button></div>'
  );
  let resolveManualCheck;
  document.getElementById("rc-nav-retry").addEventListener("click", () => {
    if (resolveManualCheck) resolveManualCheck();
  });

  for (let attempt = 0; attempt < MAX_EXTENDED_RETRIES; attempt++) {
    // A manual click races against the timer and wins instantly,
    // genuinely skipping the rest of the wait -- not just a flag
    // checked after the delay had already elapsed regardless.
    await new Promise((resolve) => {
      resolveManualCheck = resolve;
      setTimeout(resolve, EXTENDED_RETRY_DELAY_MS);
    });
    const alive = await checkBackendAliveOnce();
    backendAlive = alive;
    if (alive) {
      hideOverlay();
      window.location.href = href;
      return;
    }
  }

  // Genuinely stuck after ~2 minutes of patient automatic retrying --
  // continuing to silently retry forever would be worse than being
  // honest that this might not be a brief restart at all.
  showOverlay(
    '<div class="rc-nav-box"><div>Couldn\u2019t reach RaceCount.</div>' +
    '<div class="rc-nav-sub">Check the Pi is powered on and the camera/network are connected.</div>' +
    '<button type="button" id="rc-nav-retry">Try again</button></div>'
  );
  document.getElementById("rc-nav-retry").addEventListener("click", () => {
    hideOverlay();
    waitForRecoveryThenNavigate(href);
  });
}

// The one shared entry point for ALL navigation in the app, whether
// triggered by a click on a link or directly from JS after some other
// action completes. Common case (backend known healthy): navigates
// instantly, identical to a plain window.location.href assignment,
// zero added latency. Known-unhealthy case: shows the protective
// overlay and retries rather than letting the browser's raw error
// page through.
window.rcNavigate = function rcNavigate(href) {
  if (backendAlive) {
    window.location.href = href;
    return;
  }
  waitForRecoveryThenNavigate(href);
};

document.addEventListener("click", (e) => {
  const a = e.target.closest("a");
  if (!isInternalNavLink(a)) return;
  e.preventDefault();
  window.rcNavigate(a.getAttribute("href"));
});
