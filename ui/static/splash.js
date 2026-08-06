/*
  Shown once at cold start (the kiosk launcher points Chromium here, not
  straight at "/" — see systemd/launch_kiosk.sh). All in-app navigation
  (header back links, End Session, etc.) always targets "/" directly and
  never revisits this page.

  Not purely decorative: launch_kiosk.sh already waits for the backend
  to respond before opening the browser at all, but this adds a second,
  belt-and-suspenders check for the case this page gets reached some
  other way (manual testing, a future launcher tweak) — polls the
  already-existing /api/status endpoint and won't advance to Home until
  it's gotten a real response, while a minimum display time keeps the
  branding from flashing by unnoticed if the backend was already warm,
  and a maximum fallback keeps a genuinely stuck backend from stranding
  the screen here forever.
*/

const MIN_DISPLAY_MS = 1300;
const MAX_WAIT_MS = 7000;
const POLL_INTERVAL_MS = 400;

const startTime = Date.now();
let backendReady = false;
let navigated = false;

function goHome() {
  if (navigated) return;
  navigated = true;
  window.rcNavigate("/");
}

async function pollBackend() {
  try {
    const res = await fetch("/api/status");
    if (res.ok) {
      backendReady = true;
    }
  } catch (err) {
    // backend not up yet -- keep waiting, this is expected during a cold start
  }
}

function tick() {
  const elapsed = Date.now() - startTime;

  if (elapsed >= MAX_WAIT_MS) {
    goHome();
    return;
  }
  if (backendReady && elapsed >= MIN_DISPLAY_MS) {
    goHome();
    return;
  }

  pollBackend();
  setTimeout(tick, POLL_INTERVAL_MS);
}

tick();
