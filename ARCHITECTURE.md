# RaceCount — Architecture Overview

A map of the codebase, not a duplicate of README.md — README covers
deployment and testing; this covers **what's here, how it fits
together, and where to look when you want to change something**.

## Tech stack, in one list

| Layer | What | Why |
|---|---|---|
| Language | Python 3.11+ | Whole backend, tests, tooling |
| Detection | Ultralytics YOLOv8n (COCO-pretrained) | Small, fast enough for a Pi 5 CPU, "sheep" is already a stock COCO class |
| Tracking | ByteTrack (via `supervision` library) | Survives brief occlusion better than plain SORT — see `tracking/tracker.py` |
| Camera capture | OpenCV (`cv2.VideoCapture`, FFmpeg backend) | RTSP in, latest-frame-only reader thread |
| Web backend | Flask | One process, serves both the HTML screens and the JSON API they poll |
| Frontend | Plain HTML/CSS/JS, no framework | No build step needed on a kiosk device; every screen is a normal server-rendered page |
| Data persistence | Flat JSON files, atomic writes (temp file + `os.replace`) | No database dependency; crash-safe without needing one |
| Tests | pytest (Python), Node.js (`.mjs` files, no test framework — just plain scripts with asserts) | JS logic (calibration math/state machine) has no DOM dependency, so it runs standalone under Node rather than needing a browser |
| Deployment | systemd (backend) + labwc autostart (kiosk browser) | See README.md's deployment section for the full story, including the compositor detective work |

## The pipeline, end to end

```
Reolink camera (RTSP)
    │
    ▼
camera/rtsp_stream.py      background thread, always-latest-frame,
                             never buffers, auto-reconnects
    │
    ▼
detection/yolo_engine.py   YOLO inference, filtered to "sheep" class
                             only at the model call itself
    │
    ▼
tracking/tracker.py        ByteTrack assigns persistent IDs, keeps a
                             short trajectory (centroid history) per ID
    │
    ▼
logic/zones.py             3 calibrated gate LINES (not areas) +
                             segment-intersection crossing detection
    │
    ▼
logic/direction_logic.py   debounced confirmation (a crossing needs
                             one more observation on the far side
                             before it counts — guards against jitter)
    │
    ▼
counting/counter.py        live, in-memory, THIS-SESSION-ONLY count
    │                       (resets on the next Start Session)
    │
    ▼
mobs/mob_store.py          persisted, CUMULATIVE count for whichever
                             mob is active — survives restarts, weeks
                             between sessions, etc.
    │
    ▼
mobs/session_record.py     an immutable snapshot taken at End Session —
                             this mob, these labels, this session's
                             counts, how long it took
```

**The three "count" concepts, disambiguated** (this is the single
easiest thing to get confused navigating the code):
- `counting/counter.py`'s `DirectionCounter` — the live number on the
  Active screen right now. In memory only. Resets every time a new
  session starts.
- `mobs/mob_store.py`'s `Mob.counts` — the running total for a mob,
  accumulated across every session it's ever had. Persisted to disk on
  every single increment.
- `mobs/session_record.py`'s `SessionRecord.counts` — a frozen copy of
  what *one specific session* contributed, captured once at End Session
  and never touched again.

## Module-by-module

| Folder | What's in it | Start here if you want to... |
|---|---|---|
| `camera/` | `config.py` (connection settings, password handling), `rtsp_stream.py` (the reader thread) | change the camera IP/stream type, tune reconnect behaviour |
| `detection/` | `yolo_engine.py` | swap models, change confidence threshold, adjust `imgsz` for speed |
| `tracking/` | `tracker.py` | tune ByteTrack's matching/buffer parameters |
| `logic/` | `zones.py` (pure geometry), `direction_logic.py` (the confirmation/jitter logic) | change how a "crossing" is defined or confirmed |
| `counting/` | `counter.py` | change live-session counting behaviour |
| `mobs/` | `mob_store.py` (mobs), `session_record.py` (session snapshots) | change what a mob or session record stores |
| `ui/server.py` | every Flask route, plus the small shared-state classes (`DashboardState`, `ActiveMobState`, `SessionState`) | add an API endpoint, change what a screen's backend does |
| `ui/templates/` + `ui/static/` | one `.html` + `.css` + `.js` per screen, plus `base.html`/`brand.css`/`brand.js` (shared header, design tokens, date formatting) | change a screen's layout, styling, or client-side behaviour |
| `main.py` | the pipeline loop + wiring everything together at startup | change the session-gating logic, what happens on Start/End Session at the pipeline level |
| `tools/` | `calibrate_zones.py` (CLI fallback), `export_model.py` (ONNX export) | not part of the running app — one-off utilities |
| `systemd/` | service unit, kiosk launch script, autostart entry, auto-update timer | deployment-only, see README.md |
| `tests/` | one file per module being tested, roughly | see "Running the tests" in README.md |

## Key decisions worth knowing before you change things nearby

These are all explained in more depth as comments in the relevant file
— this is just the index, so you know which file to open.

- **Zones are lines, not areas** (`logic/zones.py`) — a gate is the
  literal line between its 2 calibrated points; a sheep is counted when
  its tracked movement crosses that line, not when it dwells inside a
  region. Chosen because it degrades better at low FPS than an
  area-residency check would.
- **Crossings need a second confirming observation**
  (`logic/direction_logic.py`) — guards against a track jittering back
  across the line and getting double-counted. Also has a real regression
  test (`test_reconfirmation_of_already_counted_track_returns_none`) for
  a genuine bug this exact mechanism once had.
- **Detection only runs during a session** (`main.py`'s `pipeline_loop`)
  — the camera reader always runs, but YOLO/tracking/classification are
  gated behind `SessionState.is_active()`, which only becomes true
  between Start Session and End Session. This is the actual CPU/RAM
  saving, not just a UI state.
- **Session start forces a full reset** — `DirectionCounter`,
  `DirectionClassifier`, and `SheepTracker` (a genuinely fresh
  `ByteTrack` instance, not just cleared dicts) all reset the moment a
  session becomes active, so nothing from a session hours or days ago
  leaks into a new one.
- **Mob data writes on every single increment, deliberately** — this is
  the one place in the codebase that does NOT try to minimize disk
  writes, unlike everywhere else. Losing a mob's running total would
  defeat the entire point of it existing. Atomic writes (temp file +
  `os.replace`) make each write crash-safe.
- **The sheep-only class filter lives in the model call itself**
  (`detection/yolo_engine.py`'s `classes=self.target_classes`) — not a
  downstream check. A false detection on a handler's arm never becomes a
  `Detections` entry at all, so nothing later in the pipeline can
  accidentally count it.

## Frontend conventions, if you're adding or editing a screen

- Every screen extends `ui/templates/base.html`, which provides the
  header (logo, RaceCount + version, live clock, back link) — except
  `calibrate.html` and `splash.html`, which are deliberately standalone
  (calibration needs the full screen for the camera view; splash is a
  one-time branding moment before any screen chrome exists).
- Colors are two separate token sets in `ui/static/brand.css`, on
  purpose: `--brand-*` (navy/green, MobLogic's identity — chrome,
  buttons, structure) and `--gate-*` (amber/green/blue — functional,
  tied to actual Python CV overlay code, means "which gate" everywhere
  it appears: video overlay, calibration page, Start screen field
  labels, Active screen counters, Session Stats, History). Don't merge
  these without a specific reason to.
- `formatShortDate()` and `formatDuration()` in `brand.js` are shared
  utilities — if you're formatting a date or a duration somewhere new,
  check there before writing another copy.
- Destructive actions (deleting a mob, resetting counts) use a
  tap-once-to-arm, tap-again-to-confirm pattern rather than a native
  `confirm()` dialog, which doesn't suit a kiosk touchscreen well. See
  `history.js`'s delete button for the reference implementation.

## What's NOT in the code

Worth knowing these live outside this repo entirely, so you don't go
looking for them here:
- Camera password (`RACECOUNT_CAMERA_PASSWORD` env var / `/etc/racecount.env`)
- The compositor-specific autostart fixes (`/etc/xdg/labwc/autostart`,
  `~/.config/labwc/autostart`) — Pi-system-level config, not project files
- Zone calibration data (`logic/zones_config.json`) and all mob/session
  data (`data/mobs/`, `data/sessions/`) — real farm data, gitignored,
  regenerated/accumulated on the actual device, not shipped with the code
