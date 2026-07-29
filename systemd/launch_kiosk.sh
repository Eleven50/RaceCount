#!/bin/bash
# Waits for the RaceCount backend to start accepting connections, then
# launches Chromium in kiosk mode pointed at the splash screen, which
# briefly shows branding and then hands off to Home once it's confirmed
# the backend is actually responding (see ui/static/splash.js) — not
# just this script's own wait loop below, which is the first check but
# not the only one.
#
# This is launched via ~/.config/autostart (see racecount-kiosk.desktop)
# rather than systemd, because it needs to run inside the logged-in
# desktop session to reach the display/touchscreen — a system-level
# systemd service (like racecount.service, which runs the actual
# tracking backend) starts before any GUI session exists and has no
# straightforward access to it.

URL="http://localhost:8080/splash"
MAX_WAIT_SECONDS=60
waited=0

until curl --silent --fail --output /dev/null "$URL"; do
    sleep 1
    waited=$((waited + 1))
    if [ "$waited" -ge "$MAX_WAIT_SECONDS" ]; then
        echo "racecount-kiosk: backend did not come up after ${MAX_WAIT_SECONDS}s, opening anyway" >&2
        break
    fi
done

# Back to plain --kiosk. The reason this was avoided for a while: the
# OS-level on-screen keyboard (Squeekboard) is hardcoded to labwc's
# "top" compositor layer, while --kiosk puts Chromium on the
# "fullscreen" layer above it -- the keyboard was structurally unable
# to render above a true fullscreen window (labwc/labwc#2926, an open
# upstream issue). --start-maximized + --app=URL worked around that,
# at the cost of a visible titlebar Chromium draws itself regardless of
# window state.
#
# That workaround is no longer needed: RaceCount's text inputs now have
# their own built-in on-screen keyboard (ui/static/keyboard.js), which
# lives inside the page itself rather than depending on the OS/Wayland
# compositor at all -- so which window state Chromium is in stopped
# being relevant. Back to true --kiosk gets the clean, no-titlebar
# fullscreen experience back, with working text input at the same time,
# which the OS-level approach could never do simultaneously.

# Recent Raspberry Pi OS images have shipped with either the Pi-specific
# "chromium-browser" package or Debian's standard "chromium" package,
# depending on the image variant/history — only one is normally present,
# and which one varies by system. Detecting at runtime rather than
# hardcoding one avoids this silently breaking again on a re-image or a
# different Pi OS variant.
if command -v chromium-browser >/dev/null 2>&1; then
    CHROMIUM_CMD="chromium-browser"
elif command -v chromium >/dev/null 2>&1; then
    CHROMIUM_CMD="chromium"
else
    echo "racecount-kiosk: neither chromium-browser nor chromium found on this system" >&2
    exit 1
fi

# Chromium treats anything other than a clean shutdown (a crash, a
# power loss, a manual `pkill` during testing) as reason to prompt for
# session restore on next launch -- with no window chrome to click
# through, that prompt would just sit there blocking the kiosk. Since
# this profile only ever points at one fixed URL, there's nothing
# meaningful to restore in the first place: just tell it every launch
# was clean, before it gets the chance to think otherwise. Wrapped so
# that if this fails for any reason (missing file, unexpected format),
# it never blocks the actual kiosk launch below.
PREFS_FILE=$(find "$HOME/.config" -name "Preferences" -path "*hromium*" 2>/dev/null | head -1)
if [ -n "$PREFS_FILE" ]; then
    python3 -c "
import json
path = '$PREFS_FILE'
try:
    with open(path) as f:
        data = json.load(f)
    data.setdefault('profile', {})['exit_type'] = 'Normal'
    with open(path, 'w') as f:
        json.dump(data, f)
except Exception:
    pass
" 2>/dev/null || true
fi

exec "$CHROMIUM_CMD" \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-translate \
    --password-store=basic \
    --check-for-update-interval=31536000 \
    --overscroll-history-navigation=0 \
    "$URL"
