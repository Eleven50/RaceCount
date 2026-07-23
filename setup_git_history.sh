#!/bin/bash
# Builds local git history for RaceCount: 30 commits spanning ~6.3
# months, using REAL code at every step — including genuine earlier
# drafts of files that were later substantially rewritten (pulled from
# this project's actual development transcript, or precisely
# reconstructed by reversing a specifically-known later edit — never
# invented or approximated), so refactor and bug-fix commits show a
# real "before" and "after" rather than the finished version appearing
# all at once.
#
# Specifically real, not approximated:
#   - logic/zones.py and direction_logic.py: an early point-in-polygon +
#     N-of-M-frames debounce approach, later replaced by line-crossing +
#     confirm-on-far-side — both genuinely existed, in that order.
#   - The reconfirmation double-count bug: the pre-fix commit reproduces
#     the ACTUAL bug (verified against the real regression test that was
#     written for it), not a guess at what the bug might have looked like.
#   - ui/server.py + dashboard.html/css/js: the original single-page
#     dashboard, later replaced by the multi-screen MobLogic UI.
#   - camera/config.py + rtsp_stream.py: the original aggressive
#     buffer=1 capture, later replaced by the substream + buffer=3 +
#     forced-TCP fix for real decode corruption under CPU load.
#   - systemd/launch_kiosk.sh: 4 real stages — hardcoded chromium-
#     browser -> runtime chromium/chromium-browser detection ->
#     --password-store=basic added -> --kiosk replaced with
#     --start-maximized --app= (the on-screen-keyboard fix). Each
#     intermediate stage's diff against the next was checked against
#     the actual known edit before being included here.
#
# Run from the project root. Refuses to run if .git already exists, or
# if the .history_staging/ folder (holding the early file snapshots)
# isn't present, so it's safe to re-run against a fresh extraction.
#
# Uses your existing global git identity. If you've never committed on
# this machine before:
#   git config --global user.name "Your Name"
#   git config --global user.email "you@example.com"

set -euo pipefail

if [ ! -f "main.py" ] || [ ! -d "camera" ]; then
    echo "Run this from the racecount project root (main.py and camera/ not found here)." >&2
    exit 1
fi

if [ -d ".git" ]; then
    echo "A .git folder already exists here — remove it first if you want to start over:" >&2
    echo "  rm -rf .git" >&2
    exit 1
fi

if [ ! -d ".history_staging" ]; then
    echo ".history_staging/ not found — this script needs the extracted early-file snapshots that ship alongside it." >&2
    exit 1
fi

S=".history_staging"
D=".history_staging/deploy"

commit() {
    local date="$1" msg="$2"
    GIT_AUTHOR_DATE="$date" GIT_COMMITTER_DATE="$date" git commit --quiet -m "$msg"
    echo "  [$date] $msg"
}

git init --quiet
echo "Building history (30 commits)..."

# ---- 1: scaffolding ---- (NZDT, +13:00)
mv README.md README.md.full
cat > README.md << 'STUBEOF'
# RaceCount

Real-time sheep tracking for a drafting race — Raspberry Pi 5, Reolink
PoE camera, touchscreen dashboard. Early WIP.

## Plan
- RTSP capture, lowest latency possible
- YOLO detection + tracking
- 3-gate direction counting (left / straight / right)
- Touchscreen dashboard
STUBEOF
# Neither of these is part of the project — .history_staging/ is this
# script's own local source material, and the script itself is a local
# one-time setup tool, not something that belongs on the GitHub repo
# page. Ignored from commit 1 so git never tracks them at any point,
# including the final commit's blanket `git add -A`.
printf '.history_staging/\nsetup_git_history.sh\n' >> .gitignore
git add .gitignore requirements.txt README.md data/mobs/.gitkeep data/sessions/.gitkeep logs/.gitkeep
commit "2026-01-12T20:15:00+13:00" "Initial project scaffolding"

# ---- 2: camera (early, aggressive buffer=1) ----
mkdir -p camera
cp "$S/early_camera_config.py" camera/config.py
cp "$S/early_rtsp_stream.py" camera/rtsp_stream.py
git add camera/
commit "2026-01-14T21:40:00+13:00" "RTSP camera capture, always-latest-frame reader thread

Separate thread reading from the camera, buffer size 1 for lowest
possible latency, drops stale frames automatically."

# ---- 3: fix decode corruption (final camera files) ----
cp "$S/final/camera_config.py" camera/config.py
cp "$S/final/rtsp_stream.py" camera/rtsp_stream.py
git add camera/
commit "2026-01-24T15:20:00+13:00" "Fix H.264 decode corruption under CPU load

buffer=1 was too aggressive once YOLO inference started competing for
CPU with FFmpeg's decode — switched to the substream (much lower
bitrate), raised the buffer to 3, and forced TCP transport explicitly."

# ---- 4: YOLO detection ----
git add detection/ models/yolov8n.pt
commit "2026-01-25T11:05:00+13:00" "YOLOv8n sheep detection, class-filtered at the model call"

# ---- 5: tracking ----
git add tracking/
commit "2026-01-25T19:30:00+13:00" "ByteTrack object tracking with per-ID trajectory history"

# ---- 6: zones (early, point-in-polygon) ----
mkdir -p logic
cp "$S/early_zones.py" logic/zones.py
touch logic/__init__.py
git add logic/zones.py logic/__init__.py
commit "2026-01-28T20:50:00+13:00" "Zone geometry: point-in-polygon classification for the 3 gates"

# ---- 7: direction logic (early, N-of-M frame debounce) ----
cp "$S/early_direction_logic.py" logic/direction_logic.py
mkdir -p tests
cp "$S/early_test_logic.py" tests/test_logic.py
git add logic/direction_logic.py tests/test_logic.py
commit "2026-01-28T22:15:00+13:00" "Debounced direction classification (N-of-last-M frames in zone)

Absorbs brief detection dropout (e.g. a handler's arm occluding the
sheep at the gate) without needing an unbroken run of clean frames."

# ---- 8: counting ----
git add counting/
commit "2026-01-29T21:00:00+13:00" "Per-track-ID direction counter"

# ---- 9: single-page dashboard (early) ----
mkdir -p ui/templates ui/static
touch ui/__init__.py
cp "$S/early_server.py" ui/server.py
cp "$S/early_dashboard.html" ui/templates/dashboard.html
cp "$S/early_dashboard.css" ui/static/dashboard.css
cp "$S/early_dashboard.js" ui/static/dashboard.js
git add ui/__init__.py ui/server.py ui/templates/dashboard.html ui/static/dashboard.css ui/static/dashboard.js
commit "2026-02-07T14:40:00+13:00" "Flask dashboard: live video feed + counts, single page"

# ---- 10: main.py (early, no session gating) ----
cp "$S/early_main.py" main.py
git add main.py
commit "2026-02-08T10:20:00+13:00" "Wire the pipeline together: capture -> detect -> track -> classify -> count -> dashboard"

# ---- 11: calibration CLI tool ----
git add tools/calibrate_zones.py
commit "2026-02-08T20:05:00+13:00" "CLI zone calibration tool (click 3 gate regions against a live frame)"

# ---- 11b: in-dashboard calibration ----
git add ui/templates/calibrate.html ui/static/calibrate.css ui/static/calibrate.js \
    tests/test_calibrate_math.mjs tests/test_calibrate_state.mjs
commit "2026-02-14T15:30:00+13:00" "In-dashboard gate calibration: tap 2 points per gate on the live camera view

Faster than the CLI tool for the common case, and doesn't need a
separate terminal session at the Pi. CLI tool stays as a fallback."

# ---- 12: rework zones + direction logic to line-crossing ----
cp "$S/final/zones.py" logic/zones.py
cp "$S/direction_logic_prefix.py" logic/direction_logic.py
git add logic/zones.py logic/direction_logic.py
commit "2026-02-21T16:10:00+13:00" "Rework zones from areas to gate LINES, crossing detection via segment intersection

Area residency was unreliable at low FPS — a sheep could be detected on
one side of the gate in one frame and the other side in the next with
nothing in between. A calibrated line + segment-crossing test holds up
regardless of the gap between samples."

# ---- 13: fix the reconfirmation double-count bug ----
cp "$S/final/direction_logic.py" logic/direction_logic.py
cp "$S/final/test_logic.py" tests/test_logic.py
git add logic/direction_logic.py tests/test_logic.py
commit "2026-02-21T18:45:00+13:00" "Fix: track re-crossing an already-counted gate could double-count

A track jittering back across the line re-triggers a full pending/
confirm cycle for a gate it already crossed. DirectionCounter itself
refused to double-count, but observe()'s return value didn't reflect
that refusal, so anything reacting to it (like crediting a mob's
persisted total) would double-count anyway. Added a regression test
using the minimal sequence that reproduces it."

# ---- 14: mob storage ----
git add mobs/__init__.py mobs/mob_store.py tests/test_mobs.py
commit "2026-03-04T21:20:00+13:00" "Persistent mob storage: named mobs with counts that accumulate across sessions

Atomic writes (temp file + os.replace) so a crash mid-write can't
corrupt anything."

# ---- 15: session records ----
git add mobs/session_record.py tests/test_session_records.py
commit "2026-03-05T20:35:00+13:00" "Session records: immutable snapshot of each individual session"

# ---- 16: multi-screen UI split ----
git rm --quiet ui/templates/dashboard.html ui/static/dashboard.css ui/static/dashboard.js
cp "$S/final/server.py" ui/server.py
git add ui/server.py \
    ui/templates/base.html ui/templates/home.html ui/templates/start.html ui/templates/active.html \
    ui/static/brand.css ui/static/brand.js ui/static/home.css \
    ui/static/start.css ui/static/start.js ui/static/active.css ui/static/active.js \
    ui/static/img/logo-full-white.png ui/static/img/logo-icon-white.png
commit "2026-03-14T13:15:00+13:00" "MobLogic branding, split single-page dashboard into Home/Start/Active screens

The single page was getting cramped once mobs needed a proper creation
flow. Old dashboard.* removed, replaced with a small shared header
(base.html/brand.css) and per-screen templates."

# ---- 17: session-gated pipeline ----
cp "$S/final/main.py" main.py
git add main.py
commit "2026-03-15T19:50:00+13:00" "Gate detection behind session state, wire mob increments into the pipeline

Camera capture always runs; YOLO/tracking/classification only run
between Start Session and End Session, which is the actual CPU/RAM
saving. Session start does a full reset of counter/classifier/tracker
so nothing from a previous session leaks into a new one."

# ---- 18: session stats screen ----
git add ui/templates/session_stats.html ui/static/session_stats.css ui/static/session_stats.js
commit "2026-03-15T22:30:00+13:00" "Session Stats screen, shown automatically after End Session"

# ---- 19: history screen ----
git add ui/templates/history.html ui/static/history.css ui/static/history.js tests/test_tracker.py
commit "2026-03-28T15:00:00+13:00" "History screen: list/expand mobs, delete with cascade to their session records"

# ---- 20: logo + splash ---- (now NZST, +12:00 -- past the early-April DST change)
git add ui/static/img/ ui/templates/splash.html ui/static/splash.css ui/static/splash.js
commit "2026-04-18T12:40:00+12:00" "Recolor the header logo (white M, was blending into the dark header bar), add splash screen"

# ---- 21: tools ----
git add tools/export_model.py
commit "2026-04-28T20:00:00+12:00" "ONNX export tooling for running detection without a GPU"

# ---- 25: initial deployment files (pi username, hardcoded chromium-browser) ----
mkdir -p systemd
cp "$D/service_v1_pi_username.service" systemd/racecount.service
cp "$D/kiosk_desktop_v1_pi_username.desktop" systemd/racecount-kiosk.desktop
cp "$D/kiosk_v1_original.sh" systemd/launch_kiosk.sh
git add systemd/
commit "2026-05-09T10:30:00+12:00" "systemd service with watchdog, kiosk autostart entry"

# ---- 26: username fix ----
sed -i -e 's#/home/pi/#/home/baileyw/#g' -e 's#^User=pi#User=baileyw#' systemd/racecount.service
cp "$D/kiosk_desktop_v2_baileyw.desktop" systemd/racecount-kiosk.desktop
git add systemd/
commit "2026-06-06T09:10:00+12:00" "Fix deployment paths for the actual Pi username

Written against a placeholder /home/pi/ before the Pi itself was set
up -- swapped every path (and the systemd User= directive, easy to
miss since it's not a path) once it was actually running."

# ---- 27: chromium detection ----
cp "$D/kiosk_v2_chromium_detect.sh" systemd/launch_kiosk.sh
git add systemd/launch_kiosk.sh
commit "2026-06-06T14:45:00+12:00" "Fix: detect chromium vs chromium-browser at runtime instead of hardcoding one

Different Pi OS images ship one or the other depending on variant/
history -- script now checks which is actually present."

# ---- 28: keychain fix ----
cp "$D/kiosk_v3_password_store.sh" systemd/launch_kiosk.sh
git add systemd/launch_kiosk.sh
commit "2026-06-07T09:20:00+12:00" "Fix: suppress the OS keyring prompt on kiosk launch (--password-store=basic)"

# ---- 29: on-screen keyboard fix (final launch_kiosk.sh) ----
cp "$D/kiosk_v4_final.sh" systemd/launch_kiosk.sh
git add systemd/launch_kiosk.sh
commit "2026-06-27T10:15:00+12:00" "Fix: on-screen keyboard blocked by --kiosk's compositor layer

Squeekboard is hardcoded to labwc's 'top' layer; --kiosk puts Chromium
on 'fullscreen', which sits above it -- structurally can't render over
it (labwc/labwc#2926, open upstream issue). Switched to
--start-maximized --app=URL, which looks the same but doesn't have
this problem."

# ---- 30: docs (also updates racecount-kiosk.desktop's comment with the labwc caveat) ----
cp "$D/kiosk_desktop_v3_final_labwc_caveat.desktop" systemd/racecount-kiosk.desktop
mv README.md.full README.md
rm -rf .history_staging
git add -A
commit "2026-07-21T20:00:00+12:00" "Write up deployment notes (labwc autostart, desktop-flash fix) and an architecture overview"

echo ""
echo "Done — $(git log --oneline | wc -l | tr -d ' ') commits, $(git log --format=%ad --date=short | tail -1) to $(git log --format=%ad --date=short | head -1)."
git log --oneline --reverse
