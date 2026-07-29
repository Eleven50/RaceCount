# RaceCount

Real-time sheep tracking and direction counting for a drafting race, running
on a Raspberry Pi 5 with a Reolink PoE camera and a Waveshare touchscreen.

Deploying or debugging? Keep reading — this file covers setup, testing, and
known tradeoffs. Navigating or changing the code itself? See
[`ARCHITECTURE.md`](ARCHITECTURE.md) instead — a map of what's where and why,
written for exactly that.

## How it's wired together

```
Reolink RTSP  →  camera/          background thread, always-latest-frame,
                                    never buffers, auto-reconnects
                     │
                     ▼
              detection/          YOLOv8n, filtered to the "sheep" COCO
                                    class only — every other class
                                    (including "person") is discarded by
                                    the model call itself
                     │
                     ▼
              tracking/           ByteTrack assigns persistent IDs,
                                    survives brief occlusion (e.g. a
                                    handler's arm at the gate)
                     │
                     ▼
              logic/              3 calibrated gate lines + a crossing
                                    classifier (a track's movement must
                                    cross a gate's line, then hold on the
                                    far side for one more observation
                                    before it's confirmed)
                     │
                     ▼
              counting/           one count per track ID, ever
                     │
                     ▼
              ui/                 Flask: MJPEG stream + counts API +
                                    touchscreen dashboard
                     │
                     ▼
        systemd/racecount.service supervises main.py — auto-start,
                                    crash + hang restart
```

Three threads, deliberately not more: the camera reader (always-latest-frame
capture), the pipeline (detect → track → classify → count → draw), and
Flask's own thread (dashboard + API). The pipeline thread pulls whatever
frame is currently latest at the top of each loop iteration rather than
being fed a queue — if inference takes longer than the camera's frame
interval, the next iteration just picks up a newer frame, and everything in
between is dropped with no separate frame-skipping logic required.

**Detection only runs during an active session.** The camera reader always
runs (comparatively cheap, and both the idle Active screen and the
calibration page need a live frame regardless), but YOLO inference,
tracking, and classification are gated behind `SessionState` — off until
Start Session is pressed on the Active screen, off again after End Session.
Starting a session is itself gated server-side on two conditions (checked
again on the API call, not just trusted from the button's disabled state):
the gates must be calibrated, and a mob must be selected via Start. Session
start also fully resets the live counter, the classifier's per-track state,
and the tracker (a fresh `ByteTrack` instance, not just cleared dicts) —
track IDs from a session hours or days ago have no business persisting into
a new one.

## Before running this for the first time

**1. Set the camera password** — it's deliberately not hardcoded:
```bash
export RACECOUNT_CAMERA_PASSWORD='your-actual-password'
```
For systemd, put this in `/etc/racecount.env` (root-owned, `chmod 600`) and
uncomment `EnvironmentFile=` in `systemd/racecount.service` instead of
leaving it inline in the unit file.

**2. Confirm the RTSP path.** `camera/config.py` assumes
`/h264Preview_01_main`, Reolink's standard main-stream path. Some firmware
exposes `/Preview_01_main` instead — check the Reolink app's advanced RTSP
settings if the stream won't open, and update `RTSP_PATH`.

**3. Calibrate the 3 gate zones.** There's no way to hardcode correct
zones without seeing your actual mounted camera view — the shipped repo
ships with no `logic/zones_config.json` on purpose.

The gate layout this was built for: a top-down camera over the end of the
race, where 3 physical gate arms form an upside-down U — a left arm (Gate
A), a horizontal arm across the top (Gate B), and a right arm (Gate C) —
opening the sheep into left / straight / right pens respectively. The
calibration page uses this naming (**Gate A = left, Gate B = straight, Gate
C = right**) since it matches how the gates are physically referred to,
with the direction always shown alongside so it's never ambiguous which is
which.

Calibrate from the dashboard: tap the ⚙ icon top-right of the video panel,
or go straight to `http://<pi-ip>:8080/calibrate`. The camera view fills
the whole screen; prompt and controls float over it. For each gate in turn:
tap **Point A** then **Point B** on that gate's physical location in the
frame (its two endpoints — for Gate A/C, that's roughly top and bottom of
the vertical arm; for Gate B, roughly the two ends of the horizontal arm),
then tap **Next gate** to confirm and move on. After Gate C, it shows all 3
generated zones together for a final check before **Save zones** — which
applies immediately, no restart needed; the running pipeline picks it up on
the next processed frame. Undo steps backward through points one at a time,
including back across a gate boundary if needed.

This is a normal web page, so it works identically with a mouse on any
regular monitor as it will later with the touchscreen — useful right now
since the Waveshare display hasn't arrived yet.

**If the Pi's only network is the isolated one to the camera** (PoE switch,
no WiFi, not part of your house/farm LAN) — which is the normal setup here,
per the hardware spec — a laptop on your regular LAN won't be able to reach
the Pi at all, on this or anything else. That's fine: plug a monitor and
mouse straight into the Pi and open `http://localhost:8080/calibrate` in a
browser running on the Pi itself. That's loopback, not network traffic, so
it doesn't matter that the camera's network is isolated. This is also the
only way to do anything else on the Pi directly (checking logs, setting
`RACECOUNT_CAMERA_PASSWORD`, etc.) until the touchscreen's attached — normal
for a fixed embedded install, not a workaround.

If your Pi *does* also happen to be reachable on your LAN (e.g. it has a
second network path, or the PoE switch is uplinked into your main network),
then `http://<pi-ip>:8080/calibrate` from any other device works the same
way — find `<pi-ip>` with `hostname -I` on the Pi.

There's also a standalone desktop-OpenCV version at
`tools/calibrate_zones.py` (click 2 points per gate, run from a terminal)
if you ever need calibration to work without the Flask server up — the
in-dashboard flow above is the one to reach for day-to-day.

**A note on how a gate's 2 tapped points are used**: each gate is a single
*line* between its 2 points, not an area — a sheep is counted the moment
its tracked movement crosses that line (a standard line-crossing / "trip
wire" approach), not by dwelling inside a zone. This is a deliberate
change from an earlier area-based version: a thin area can be stepped over
entirely between two processed frames if the achievable FPS is low relative
to how fast sheep move, since there's no guarantee any frame samples the
animal *while* it's inside a narrow strip — a crossing test only needs the
segment between two known positions to intersect the gate line, regardless
of how far apart those two samples are, so it degrades much better at low
FPS. It also matches the actual gate hardware more directly: a gate arm
*is* a line in the top-down view, not an area. To guard against a track's
centroid jittering right at the line (detector noise, not real movement),
a crossing isn't counted until the very next observation of that track is
still on the far side — see `DirectionClassifier`'s `confirm_observations`
in `main.py` if that needs tuning. The geometry itself (segment
intersection, handling any line orientation including Gate B's horizontal
arm) lives in `logic/zones.py` and is unit-tested directly, independent of
calibration or tracking.

## Installing on the Pi

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

`ultralytics` pulls in `torch`/`torchvision` automatically. On Raspberry Pi
OS's aarch64, pip will resolve the CPU-only ARM64 wheels directly — there's
no CUDA variant for that platform, so nothing extra to configure there.

## Running it

Dev / foreground:
```bash
venv/bin/python main.py
```
Then open `http://<pi-ip>:8080` (or `http://localhost:8080` on the Pi
itself) for the Home screen — a small multi-screen, RaceCount-branded UI:
Home, Start (create or continue a mob), Active (live camera + session
control), Session Stats (shown automatically after End Session), History
(list/expand/delete past mobs), Calibrate, and a splash screen shown once
at cold boot (see `systemd/launch_kiosk.sh`, which points at `/splash`
rather than `/` directly). All built and functional. Still open: folding
the RaceCount header into Calibration's own floating bar (it currently has
no shared header at all, by design — see the note in that file).

Production (auto-start on boot, restart on crash or hang):
```bash
sudo cp systemd/racecount.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now racecount.service
```
Full setup steps and what `Type=notify` + `WatchdogSec` actually buys you
are documented inline in `systemd/racecount.service`.

For the touchscreen to show the dashboard fullscreen on boot (rather than
just being reachable over the network), also set up the kiosk display —
see the next section, since which method you need depends on which
desktop compositor your Pi is actually running.

## Kiosk display setup

This took real trial-and-error to get right on a real device, and the
answer turned out to depend on Raspberry Pi OS internals that changed
recently — worth reading this rather than assuming the obvious approach
works, since it didn't the first few times here either.

**First, find out which compositor you're on** — the fix is different
for each, and guessing wrong just wastes a reboot:
```bash
echo $XDG_SESSION_TYPE
```
If that says `wayland`:
```bash
ps aux | grep -E "wayfire|labwc" | grep -v grep
```

**If you see `labwc`** (the default on Raspberry Pi OS Bookworm images
from October 2024 onward — almost certainly what a fresh install gives
you):
```bash
mkdir -p ~/.config/labwc
echo '/home/<user>/racecount/systemd/launch_kiosk.sh &' >> ~/.config/labwc/autostart
```
The trailing `&` matters — without it, the compositor can stall waiting
for the script to exit, since `autostart` is executed as a shell script
and a blocking foreground command holds up everything after it.

**If you see `wayfire`:**
```bash
echo '[autostart]
racecount_kiosk = /home/<user>/racecount/systemd/launch_kiosk.sh' >> ~/.config/wayfire.ini
```

**If `$XDG_SESSION_TYPE` said `x11`:** use `systemd/racecount-kiosk.desktop`
as documented in its own comment block — this is the one case where the
XDG-standard `~/.config/autostart/*.desktop` approach reliably works.

**Either way, also enable auto-login** (`sudo raspi-config` → System
Options → Boot / Auto Login → Desktop Autologin) — the autostart entry
only fires once a desktop session has actually started, so without this
the kiosk won't appear until someone logs in manually at the touchscreen.

**Hiding the desktop flash before the kiosk takes over**: by default
you'll briefly see the wallpaper/icons and taskbar before the browser
window catches up, since those launch in parallel and simply render
faster. On labwc, disabling them means editing the *system-wide*
autostart file (different from the one above):
```bash
cat /etc/xdg/labwc/autostart
```
Typically has a `pcmanfm-pi` line (wallpaper/icons) and a `wf-panel-pi`
line (taskbar) — comment both out (prefix with `#`) to remove the flash.
This also removes the desktop entirely outside kiosk mode too (e.g. if
you `Alt+F4` out), so you'd be doing any file browsing or WiFi
reconfiguration from a terminal from then on — a reasonable tradeoff for
a dedicated device, but worth choosing deliberately rather than by accident.

**The on-screen keyboard not appearing over the kiosk window** is a real,
currently-open upstream limitation, not a config mistake: Squeekboard
(Raspberry Pi OS's on-screen keyboard) is hardcoded to labwc's "top"
compositor layer, while Chromium's `--kiosk` flag puts the browser on
the "fullscreen" layer, which sits above "top" — the keyboard is
structurally unable to render above it
([labwc/labwc#2926](https://github.com/labwc/labwc/issues/2926)).
`systemd/launch_kiosk.sh` already works around this by using
`--start-maximized --app=URL` instead of `--kiosk`, which looks the same
in practice but sits on a layer the keyboard can appear above. If you
ever swap that back to `--kiosk` for any reason, this will come back.

## The on-screen keyboard is built into the app, not the OS

Every text field (the four on the Start screen — mob name, three gate
labels) gets a keyboard that lives entirely inside the page itself
(`ui/static/keyboard.js` + `keyboard.css`, loaded globally via
`base.html`), not Raspberry Pi OS's own on-screen keyboard.

This isn't a style preference — it's the actual fix for a real,
extensively-tested dead end. Every OS-level on-screen keyboard
(Squeekboard, wvkbd) is a *separate* Wayland client that has to render
*above* Chromium via the compositor's layer-shell protocol, and
fullscreen/kiosk Chromium sits on a compositor layer that one couldn't
get above (a confirmed, open upstream labwc/Squeekboard limitation) and
the other didn't render above either despite defaulting to the
"correct" layer in its own source. A keyboard built into the page
sidesteps the entire category of problem: it's not a second window
trying to layer above a first one, it's just DOM content Chromium is
already rendering as part of the same page.

Bonus this unlocked: since the keyboard no longer depends on Chromium
running in a specific window state, `launch_kiosk.sh` is back to plain
`--kiosk` instead of the `--start-maximized --app=` workaround —
the clean, no-titlebar fullscreen experience *and* working text input,
together, which the OS-level approach could never do at the same time.

Attaches via event delegation (`focusin`/`focusout` on `document`), so
any text input on any screen gets it automatically without needing to
opt in — relevant if more text fields get added later. Handles cursor
position correctly for mid-word insertion and range-replacement, and
explicitly enforces each field's `maxlength`, since setting `.value`
programmatically (the only option here — there's no real keystroke to
simulate) bypasses the browser's native enforcement of it entirely.

## Auto-updates from GitHub

Once this is pushed to a repo, the Pi can pull and deploy new commits on
its own — no more copying files over by hand:

```bash
sudo cp systemd/racecount-update.service systemd/racecount-update.timer \
        systemd/racecount-boot-update.service systemd/racecount.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now racecount-update.timer
sudo systemctl restart racecount.service
```
(That last restart picks up racecount.service's updated `Wants=`/`After=`
lines if you already had an older copy installed — see below.)

The restart step needs passwordless sudo for exactly one command, not
broad access — add this as its own file (never edit `/etc/sudoers`
directly):
```bash
sudo visudo -f /etc/sudoers.d/racecount-update
```
Add this line:
```
baileyw ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart racecount.service
```

Every 15 minutes (configurable in `racecount-update.timer`), the Pi
checks GitHub for new commits on `main`. Nothing new → does nothing, no
log entry. Something new, but a session's currently active → doesn't
even pull yet, just logs that it's deferring and checks again next
cycle — restarting the service mid-session would interrupt live
counting, which is worse than an update landing a few minutes late.
Something new and nothing running → pulls it, reinstalls Python
dependencies if `requirements.txt` changed, and runs the full test suite
**before** touching the live service — a push that fails tests never
gets deployed, the working tree is reset back to the last known-good
commit, and the already-running (old, working) service is left
untouched. Check `logs/auto-update.log` to see what it's actually done,
or `journalctl -u racecount-update.service -f` to watch it live.

**Also checks once at boot**, before `racecount.service` starts — so a
fresh boot runs the latest pushed code immediately rather than whatever
was on disk from before, without waiting up to 15 minutes for the first
timer tick. This is deliberately best-effort and time-bounded (a hard
30s timeout): if there's no WiFi yet, or the network's still coming up,
it's skipped silently and `racecount.service` starts normally on
whatever code is already there — it does not delay boot waiting for a
network that might not show up, and a skip doesn't show up as a "failed"
unit, since that's expected behaviour out somewhere with no signal, not
a fault. `racecount-boot-update.service` doesn't need its own `enable` —
it's pulled in automatically via `racecount.service`'s own `Wants=`.

This updates the application code only — changes to systemd unit files
themselves (`racecount.service`, this timer/service pair, the kiosk
files) still need a manual `sudo cp` + `daemon-reload`, deliberately.
Auto-applying changes to what a service runs as or executes is a bigger
trust boundary than auto-applying changes to the Python it runs.

## If the video looks corrupted or choppy

Symptoms: terminal full of `cabac decode of qscale diff failed` / `error
while decoding MB ...` / `bytestream -N`, and the actual picture looking
blocky, smeared, or low frame rate. This is FFmpeg's H.264 decoder failing
partway through — not a "wrong transport protocol" issue (OpenCV/FFmpeg
4.5.5+ already defaults to TCP for RTSP, so it's very likely using that
already regardless of anything in this repo).

The more likely cause: H.264 uses inter-frame prediction (a P-frame
references a prior I-frame), and decoding the camera's full main stream
competes for the same CPU as YOLO inference on the same Pi. If the decode
thread falls behind for a moment under that load, an aggressively small
internal buffer can end up discarding a reference frame mid-GOP rather than
cleanly dropping a whole frame — the decoder then fails to reconstruct
whatever depended on it, which is exactly what these errors look like.

Two changes address this directly, both already in `camera/`:
- **`camera/config.py`'s `STREAM_TYPE` now defaults to `"sub"`** instead of
  the main stream — Reolink's substream needs roughly 5-20x less bitrate to
  decode (P320: ~256Kbps-1Mbps vs. ~5-6Mbps default for main), which
  removes most of the CPU contention at the source rather than working
  around its symptoms. Tradeoff: substream resolution is meaningfully lower
  than main, so if sheep end up too small/blurry to detect reliably at your
  camera's mounting distance, that's the sign to switch `STREAM_TYPE` back
  to `"main"` and instead reduce contention from the *inference* side (see
  the ONNX export below) and/or lower the main stream's own bitrate/frame
  rate in the Reolink app's Encode settings.
- **`camera/rtsp_stream.py`'s `CAP_BUFFER_SIZE`** was relaxed from the
  minimum (1) to 3, giving FFmpeg enough slack to decode cleanly. The
  latency-sensitive "always get the newest frame" behaviour doesn't depend
  on this number — it's already implemented in that module's own reader
  loop (continuously reading and overwriting a single frame slot), so this
  buffer existing at the FFmpeg level was mostly redundant with what the
  Python code already guarantees, while being the more likely source of the
  corruption. If quality is still off, try raising it further (5, then 8);
  if latency becomes noticeably worse before quality noticeably improves,
  that's the sign the substream change alone should carry more of the fix.
- Explicit TCP transport forcing was also added as cheap insurance
  (`OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp`), even though it's
  probably already the default — removes it as a variable either way.

None of this was reproducible in my own testing environment (no real
camera, no real CPU contention pattern), so this is a well-reasoned fix
based on how H.264/FFmpeg buffering actually works, not something verified
against your specific corruption — if it's not fully resolved after both
changes, the next lever is reducing the main stream's bitrate/framerate
directly in the camera's own settings rather than anything further on the
Pi side.

## Hitting the <200ms latency target

Raw PyTorch inference for YOLOv8n at the usual imgsz=640 will very likely
miss a 200ms budget running on the Pi 5's CPU alone. Two independent levers,
in the order I'd pull them:

1. **Lower `imgsz`** in `detection/yolo_engine.py` (defaults to 480). Try
   320–416 next; sheep filling a good fraction of the frame at your camera's
   mounting distance don't need 640px to detect reliably.
2. **Export to ONNX**: `python tools/export_model.py`, then point
   `YoloEngine(model_path=...)` at the resulting `models/yolov8n.onnx`. No
   other code changes needed — loading is format-agnostic. This also
   directly helps the RTSP corruption issue above, since it reduces
   inference's own CPU footprint — less contention for the decode thread,
   not just faster detection.

I'd normally point you at NCNN instead of ONNX — it's usually the faster of
the two on ARM CPUs and is what most Pi/YOLO tutorials recommend. But as of
early 2026, Ultralytics has NCNN inference **disabled on ARM64**
specifically (`NotImplementedError` in `AutoBackend`, confirmed by an
Ultralytics maintainer:
[discussion #22214](https://github.com/orgs/ultralytics/discussions/22214)).
It's flagged as a temporary regression ("we will re-enable it later"), not
a permanent limitation — worth trying `--format ncnn` in
`tools/export_model.py` again if you're reading this well after mid-2026,
or pin `ultralytics<8.4.0` if NCNN is a hard requirement now.

Benchmark all of this **on the Pi itself** — nothing in this repo was
timed on real Pi hardware (see below).

## What's actually been tested, and what hasn't

Built and tested in a sandboxed Linux dev container with no Raspberry Pi,
no Reolink camera, and no display attached. Being specific about the line
between "verified" and "should work" here matters more than the usual
disclaimer boilerplate:

**Verified with real execution:**
- Full pipeline, end-to-end: mocked camera feed → real YOLO detection → real
  ByteTrack tracking → line-crossing classification with jitter confirmation
  → counting → overlay drawing → live Flask dashboard, all in one run, zero
  exceptions — including a real confirmed crossing registering correctly
  through the whole chain.
- **A real double-counting bug was found this way, not by the unit tests.**
  `DirectionClassifier.observe()` could return a gate name a second time for
  a track that was already counted (jittering back across a gate line
  re-triggers a full pending→confirm cycle, since the crossing geometry has
  no memory of what's already been confirmed) — `DirectionCounter` correctly
  refused to double-count internally, but `observe()`'s return value didn't
  reflect that refusal, so a mob's persisted total came out higher than the
  live session counter for the same session. Unit tests for the classifier
  and the counter both passed throughout, since each was tested against the
  contract it was written to — this only showed up running the real,
  unmocked pipeline end-to-end for long enough for the exact sequence to
  occur organically. Fixed, and the fix has its own regression tests now
  (`test_reconfirmation_of_already_counted_track_returns_none` and its
  immediate-counting-mode counterpart) using the minimal sequence that
  reproduces it, not just a re-run of the slow organic scenario.
- The crossing geometry itself — proper finite-segment intersection (not
  just an infinite-line side test, which would false-positive on movement
  nowhere near the actual gate), direction-agnostic, correct for any line
  orientation including Gate B's horizontal arm — unit-tested directly,
  plus cross-checked against an independent JS reimplementation of the same
  algorithm under Node to catch anything language-specific.
- Session-gated detection: verified with a real (not mocked) `detector.infer`
  call count — zero calls while idle, calls resume once a session is
  started, stop again once it ends. Also verified `/api/session/start`
  correctly rejects starting with no calibration, correctly rejects with no
  mob selected, and only succeeds once both are satisfied — server-side,
  not just as a disabled button.
- In-dashboard calibration, end-to-end via real HTTP requests: page load →
  snapshot → save (including validation rejecting degenerate/too-close
  points) → confirmed the *running* `ZoneManager` picks up the new gate
  lines immediately, with no restart, by testing a crossing right after
  saving. Also verified the save persists correctly to disk and reloads in
  a fresh process. A save with a genuinely horizontal Gate B (matching the
  real gate layout) was verified end-to-end against the live `ZoneManager`,
  not just the isolated math.
- The calibration page's screen-tap → image-pixel coordinate math (the part
  that has to correctly account for the displayed image being
  scaled/letterboxed relative to its native resolution) — tested with real
  JS execution under Node against several aspect-ratio/letterbox scenarios.
- The calibration page's gate-confirmation flow (tap Point A, Point B →
  explicit "Next gate" confirmation → advance; Undo stepping backward
  through points including across a gate boundary; Restart; ignoring taps
  once a gate's 2 points are already placed or once all 3 are confirmed) —
  also tested with real JS execution under Node, 34 assertions across the
  full happy path and every undo/restart edge case.
- The specific claim this project is built around: with the exact shipped
  default config (sheep-only class filter, no overrides), running inference
  on a real photo full of people produces **zero** detections — verified
  directly, not just asserted in a comment.
- ByteTrack ID persistence and trajectory accumulation across repeated
  frames.
- All 33 unit tests for segment/crossing geometry, gate calibration
  (line generation, live-apply, persistence, validation), jitter-confirmed
  direction classification (including a simulated brief occlusion
  immediately after a crossing), and single-count-per-track-ID behavior.
- Every Flask route (`/`, `/video_feed`, `/api/counts`, `/api/status`,
  `/api/reset`) against a running server.
- ONNX export → reload → inference, through the same `YoloEngine` class,
  zero code changes.
- `systemd-analyze verify` against the actual unit file (only flags the
  placeholder path, which is expected pre-deployment).

**Not tested, because the hardware isn't available here:**
- The RTSP corruption fix specifically (substream default, relaxed buffer
  size) — reasoned from how H.264/FFmpeg buffering works and confirmed
  camera spec numbers, but not reproduced or verified fixed here, since the
  corruption only showed up against real hardware under real CPU
  contention, neither of which exists in this sandbox. This is the one
  item on this list that moved from "should work" to "diagnosed against a
  real failure" — the RTSP connection itself was already proven working
  back in Phase 1 testing, and the corruption was found through actually
  running the full pipeline, not theorized in advance.
- Real-world latency numbers on Pi 5 CPU (everything I've timed ran on a
  sandboxed x86_64 CPU, which isn't a meaningful proxy — I've reported
  those numbers as "not representative" wherever they appear rather than
  pretending they translate).
- The Waveshare touchscreen and Chromium kiosk mode — the dashboard was
  designed for a 1280×800 landscape panel (the most common 10.1" Waveshare
  DSI variant) with a responsive fallback for portrait variants, but never
  rendered on the actual device.
- Similarly, the calibration page's actual rendering and tap interaction —
  verified via real HTTP requests (endpoints, save/apply/persist) and the
  coordinate math in isolation (via Node), but never loaded in an actual
  browser here, so things like exact marker placement or SVG layering on
  a real screen are unverified. If tapped points visibly land somewhere
  other than where you tapped, that's the first place to look.
- Detection/tracking accuracy on actual sheep — every real-model test here
  used stock COCO test images of people (with the filter proving people
  are correctly excluded), since no farm footage was available to test
  with. The sheep class itself is a standard, well-supported COCO class,
  but real-world accuracy on your specific breed/lighting/camera angle is
  unverified.

## Known tradeoffs worth knowing about, not just accepting silently

- **Counts are in-memory only**, per the "no unnecessary disk writes"
  requirement — a crash-and-restart (systemd will restart automatically)
  resets counts to zero mid-session. If that's worse than occasional writes,
  the lowest-cost fix is a periodic snapshot (e.g. write `counts.json` every
  60s, or only on clean shutdown) rather than a write per event — see
  `counting/counter.py`'s docstring.
- **`supervision.ByteTrack` is deprecated** (removal planned for
  `supervision` 0.30.0+) in favour of a separate `trackers` package. That
  package failed to install correctly in testing here (pip reported success
  but left nothing importable), so this repo pins `supervision<0.30.0` and
  stays on the current API. Re-check before lifting that pin — see
  `tracking/tracker.py`.
- **Dashboard binds to `0.0.0.0`**, so it's reachable from other devices on
  your LAN (checking counts from a phone in the yard), not just from the
  Pi's own touchscreen. Change to `127.0.0.1` in `ui/server.py` if you'd
  rather it wasn't.
- **Crossing confirmation delay** (`confirm_observations=2` in `main.py`)
  trades a little count latency for jitter robustness — a crossing needs
  one more observation on the far side of the gate line before it's
  counted, rather than counting on the instant it's first detected. Set it
  to `1` if that delay ever matters more than the jitter protection (see
  `logic/direction_logic.py`); the tradeoff and reasoning are documented
  there.
- **Gate lines assume straight arms** — each gate is the literal line
  between its 2 tapped points, matching the confirmed layout (3 straight
  arms forming the upside-down U, Gate B horizontal). If a real gate arm
  turns out to be curved or the counting line needs to sit offset from the
  physical arm rather than exactly on it, the fix is isolated to
  `logic/zones.py`'s crossing/geometry functions — calibration, tracking,
  and counting stay the same regardless of how that geometry is defined.

## Repo layout

```
racecount/
├── camera/       RTSP capture (latest-frame-only, reconnect, downscale)
├── detection/    YOLO inference, sheep-only class filter
├── tracking/     ByteTrack wrapper, per-ID trajectory history
├── logic/        gate-line geometry (crossing detection) + jitter-
│                 confirmed direction classification
├── counting/     thread-safe per-track-ID count memory (live session only)
├── mobs/         persistent mob + session-record storage (mob_store.py:
│                 named mobs with cumulative counts across sessions;
│                 session_record.py: an immutable snapshot of each
│                 individual session, for Session Stats / History)
├── data/         mobs/*.json and sessions/*.json live here — real farm
│                 records, not source (see .gitignore)
├── ui/           Flask backend + every screen's HTML/CSS/JS: Home,
│                 Start, Active, Session Stats, History (list/expand/delete
│                 mobs), in-browser zone calibration
├── tools/        zone calibration, model export (interactive/CLI, not part
│                 of the running pipeline)
├── systemd/      service unit, watchdog config, kiosk autostart
├── tests/        pytest suite for the pure-logic + storage modules, plus
│                 Node tests for the calibration page's client-side logic
├── models/       yolov8n.pt ships in-repo so first run needs no internet
└── main.py       wires it all together
```

## Running the tests

```bash
venv/bin/pytest tests/ -v
```
Covers zone/crossing geometry, gate calibration, jitter-confirmed direction
classification (including
the occlusion scenario), and counting — all pure logic, no camera or model
required, so this runs anywhere including CI.

The calibration page's client-side logic (coordinate math, gate
confirmation/undo state machine) has its own Node-based tests, since that
code runs in the browser rather than through pytest:
```bash
node tests/test_calibrate_math.mjs
node tests/test_calibrate_state.mjs
```
These mirror the equivalent functions in `ui/static/calibrate.js` — if you
change the state machine or coordinate logic there, update these to match
and re-run before trusting the change.
