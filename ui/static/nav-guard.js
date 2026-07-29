/*
  Resilient internal navigation. Intercepts clicks on internal links
  and verifies the backend is actually responsive before navigating,
  rather than letting a plain <a href> hit a backend that's mid-restart
  (the auto-updater applying a new commit, or any other brief blip) and
  show the browser's own generic "can't reach this page" error --
  exactly what a professional kiosk shouldn't ever expose someone to.

  On a healthy backend this costs a few imperceptible milliseconds (one
  fetch to the existing lightweight /api/status endpoint). It only
  shows anything at all during a genuine, brief outage, and even then
  retries quietly in the background rather than dumping the user onto
  an ugly error page and making them figure out what to do about it.
*/

const HEALTH_CHECK_TIMEOUT_MS = 1200;
const RETRY_DELAY_MS = 500;
const MAX_QUIET_RETRIES = 6; // ~3s of retrying before saying anything

let overlayEl = null;

function isInternalNavLink(a) {
  if (!a || a.tagName !== "A") return false;
  if (a.target === "_blank") return false;
  if (a.hasAttribute("download")) return false;
  if (a.classList.contains("disabled")) return false; // e.g. the back link during an active session
  const href = a.getAttribute("href");
  return !!href && href.startsWith("/");
}

async function checkBackendAlive() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), HEALTH_CHECK_TIMEOUT_MS);
  try {
    const res = await fetch("/api/status", { signal: controller.signal, cache: "no-store" });
    clearTimeout(timeout);
    return res.ok;
  } catch (e) {
    clearTimeout(timeout);
    return false;
  }
}

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

async function navigateResiliently(href) {
  let alive = await checkBackendAlive();
  if (alive) {
    window.location.href = href;
    return;
  }

  showOverlay('<div class="rc-nav-box"><div class="rc-nav-spinner"></div><div>Reconnecting\u2026</div></div>');

  for (let attempt = 0; attempt < MAX_QUIET_RETRIES; attempt++) {
    await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
    alive = await checkBackendAlive();
    if (alive) {
      hideOverlay();
      window.location.href = href;
      return;
    }
  }

  // Genuinely stuck after ~3s -- say so clearly and offer a manual
  // retry, rather than retrying forever with no visible way out.
  showOverlay(
    '<div class="rc-nav-box"><div>Still trying to reach RaceCount\u2026</div>' +
    '<button type="button" id="rc-nav-retry">Try again</button></div>'
  );
  document.getElementById("rc-nav-retry").addEventListener("click", () => {
    hideOverlay();
    navigateResiliently(href);
  });
}

document.addEventListener("click", (e) => {
  const a = e.target.closest("a");
  if (!isInternalNavLink(a)) return;
  e.preventDefault();
  navigateResiliently(a.getAttribute("href"));
});
