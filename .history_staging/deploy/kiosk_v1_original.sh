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

exec chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-translate \
    --check-for-update-interval=31536000 \
    --overscroll-history-navigation=0 \
    "$URL"
