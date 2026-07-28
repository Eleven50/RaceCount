#!/bin/bash
# Polls GitHub for new commits on main. If there's nothing new, exits
# immediately without touching anything or writing to the log (so the
# log doesn't fill up with "checked, nothing new" every 15 minutes).
#
# If there IS something new: pulls it, reinstalls Python deps in case
# requirements.txt changed, then runs the real test suite as a gate
# BEFORE restarting the live service. A push that fails tests never
# gets deployed — the working tree is reset back to the last known-good
# commit, and the already-running service (old, working code) is left
# untouched. This matters more than it might seem: without the reset,
# a reboot before you fix the issue would start the service fresh from
# the broken on-disk code, turning a "next update will fix it" bug into
# an actual outage.
#
# Scope: this updates the racecount APPLICATION code (main.py, ui/,
# camera/, logic/, etc.) automatically. It deliberately does NOT
# auto-copy changes to systemd unit files themselves (racecount.service,
# this script's own .service/.timer, racecount-kiosk.desktop) into
# /etc/systemd/system or ~/.config — those still need a manual
# `sudo cp` + `daemon-reload` if they ever change. Auto-applying changes
# to what a systemd service runs as / executes is a meaningfully bigger
# trust boundary than auto-applying changes to the Python it runs, and
# isn't something this script does without you looking at it first.

set -euo pipefail

# Derived from this script's own location (systemd/racecount-update.sh
# -> repo root is one directory up) rather than a hardcoded path. Git
# names a cloned folder after the repo exactly as it appears in the
# clone URL -- "RaceCount" on GitHub clones to ./RaceCount, not
# ./racecount, and Linux filesystems are case-sensitive, so a hardcoded
# lowercase path here would silently point at nothing (or worse, an
# unrelated stale folder) the moment the actual folder's name doesn't
# match exactly. Self-locating sidesteps the whole problem.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$REPO_DIR/logs/auto-update.log"

cd "$REPO_DIR"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}

git fetch origin main --quiet

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" == "$REMOTE" ]; then
    exit 0
fi

log "New commits found: $(git rev-parse --short "$LOCAL") -> $(git rev-parse --short "$REMOTE")"

# Restarting mid-session would interrupt live counting -- a pending
# update just waits for a cycle where nothing's running, rather than
# forcing itself in. Doesn't even pull yet, so the working tree stays
# untouched until it's actually safe to deploy. If the backend's
# unreachable entirely (curl fails), that's not an active session to
# protect -- proceed as normal, since restarting a crashed service is
# exactly the right recovery behaviour.
SESSION_ACTIVE=$(curl -s --max-time 5 http://localhost:8080/api/session/status 2>/dev/null | grep -o '"active": *true' || true)
if [ -n "$SESSION_ACTIVE" ]; then
    log "Update available but a session is currently active -- deferring to next check."
    exit 0
fi

git pull --quiet origin main

if [ -f requirements.txt ]; then
    venv/bin/pip install -r requirements.txt --quiet --break-system-packages
fi

log "Running test suite before deploying..."
# Full Python suite -- the real Pi has torch/ultralytics installed as
# part of the actual deployment, unlike a stripped-down test sandbox,
# so there's no need to narrow this down. The .mjs calibration tests
# aren't included here since they run under Node, which isn't a Pi
# deployment dependency (it was only ever a development-time tool) --
# run those by hand after a calibration-page change if you want that
# coverage too.
if venv/bin/python -m pytest tests/ -q > /tmp/racecount-update-test-output.txt 2>&1; then
    log "Tests passed. Restarting racecount.service."
    sudo systemctl restart racecount.service
    log "Update complete, now running $(git rev-parse --short HEAD)"
else
    log "TESTS FAILED — not deploying. Reverting working tree to $(git rev-parse --short "$LOCAL")."
    log "Test output saved to /tmp/racecount-update-test-output.txt for review."
    git reset --hard "$LOCAL"
fi
