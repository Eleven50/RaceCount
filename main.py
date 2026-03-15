"""
RaceCount entrypoint.

Threading model (deliberately simple — see README for the reasoning):
  - camera reader thread   (camera/rtsp_stream.py): always-latest-frame capture
  - pipeline thread        (this file): detect -> track -> classify -> count -> annotate
  - main thread            (ui/server.py): Flask, serves the dashboard + API

The pipeline thread pulls whatever frame is currently latest at the top
of each loop iteration rather than being fed a queue. If inference takes
longer than the camera's frame interval, the next iteration simply picks
up a newer frame than the one just processed — frames in between are
dropped implicitly, with no separate frame-skipping logic required. This
is what satisfies "process only the latest frame / never buffer" without
needing a fourth, fully-async detection thread: capture is never blocked
by inference because it already lives on its own thread, and the UI is
never blocked by either because Flask reads only the latest annotated
frame via DashboardState.

Session-gated detection: the camera reader keeps running continuously
regardless (it's comparatively cheap, and both the idle Active-screen
view and the calibration page need a live frame even with no session
running) — but YOLO inference, tracking, and classification only run
while session_state.is_active() is True, i.e. between a Start Session
and End Session press on the Active screen. Outside a session the loop
just pushes the raw frame (with zone lines drawn for reference) and
idles, which is what actually keeps CPU/RAM down when nobody's drafting
— running the detector on every frame around the clock regardless of
whether anyone's using it would defeat the point of gating it at all.
"""
import logging
import sys
import threading
import time
from pathlib import Path

import cv2

from camera.config import build_rtsp_url
from camera.rtsp_stream import LowLatencyRTSPStream
from detection.yolo_engine import YoloEngine
from tracking.tracker import SheepTracker
from logic.zones import ZoneManager
from logic.direction_logic import DirectionClassifier
from counting.counter import DirectionCounter
from mobs.mob_store import MobStore
from mobs.session_record import SessionRecordStore
from ui.server import ActiveMobState, DashboardState, SessionState, run_dashboard

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "racecount.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("racecount.main")

# BGR — kept in sync with logic/zones.py:ZONE_COLORS and the brand CSS.
OVERLAY_COLORS = {"left": (61, 163, 232), "straight": (109, 175, 76), "right": (217, 144, 74), None: (150, 150, 150)}

# How long the loop sleeps between iterations while idle (no session
# running). No detection work is happening, so there's nothing to gain
# from spinning as fast as the active-session loop does.
IDLE_LOOP_INTERVAL_SECONDS = 0.08


def draw_overlay(frame, tracked, tracker_module: SheepTracker, classifier: DirectionClassifier, counter: DirectionCounter):
    frame = classifier.zones.draw_zones(frame)

    for box, tracker_id in zip(tracked.xyxy, tracked.tracker_id):
        if tracker_id is None:
            continue
        tid = int(tracker_id)
        x1, y1, x2, y2 = map(int, box)
        traj = tracker_module.get_trajectory(tid)

        # Neutral until a crossing is at least in progress, so the
        # overlay reads as diagnostic feedback: a box turning color
        # means the system has noticed *something*, not just a
        # decorative zone-tint like the old area-residency version had.
        confirmed = classifier.get_confirmed_gate(tid)
        pending = classifier.get_track_status(tid)
        gate = confirmed or pending
        color = OVERLAY_COLORS.get(gate, OVERLAY_COLORS[None])
        thickness = 3 if confirmed else 2

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
        cv2.putText(frame, f"#{tid}", (x1, max(0, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

        for i in range(1, len(traj)):
            cv2.line(frame, tuple(map(int, traj[i - 1])), tuple(map(int, traj[i])), color, 2)

    counts = counter.snapshot()["counts"]
    y = 28
    for name in ("left", "straight", "right"):
        cv2.putText(
            frame, f"{name.upper()}: {counts[name]}", (12, y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.75, OVERLAY_COLORS[name], 2,
        )
        y += 26

    return frame


def pipeline_loop(
    stream: LowLatencyRTSPStream,
    detector: YoloEngine,
    tracker_module: SheepTracker,
    classifier: DirectionClassifier,
    counter: DirectionCounter,
    dashboard_state: DashboardState,
    session_state: SessionState,
    mob_store: MobStore,
    active_mob_state: ActiveMobState,
    stop_event: threading.Event,
    notifier=None,
):
    consecutive_errors = 0
    was_active = False

    while not stop_event.is_set():
        dashboard_state.set_camera_connected(stream.connected)
        frame = stream.get_latest_frame()
        if frame is None:
            time.sleep(0.01)
            continue

        # Always available for the calibration page's snapshot endpoint,
        # regardless of whether a session is running.
        dashboard_state.update_raw_frame(frame)

        is_active = session_state.is_active()

        if not is_active:
            was_active = False
            # No detection/tracking work at all while idle — this is the
            # actual CPU/RAM saving, not just a UI state. Zone lines are
            # still drawn so the idle feed is useful for confirming the
            # camera's pointed the right way and calibration looks sane.
            idle_frame = classifier.zones.draw_zones(frame.copy())
            dashboard_state.update_frame(idle_frame)
            if notifier is not None:
                try:
                    notifier.notify("WATCHDOG=1")
                except Exception:
                    logger.debug("sdnotify watchdog ping failed", exc_info=True)
            time.sleep(IDLE_LOOP_INTERVAL_SECONDS)
            continue

        if not was_active:
            # Session was just (re)started — guarantee a clean slate
            # regardless of what a previous session (or an unclean
            # shutdown) left behind. Deliberately done here in the
            # pipeline thread, which already exclusively owns these
            # objects, rather than from the Flask request thread that
            # handled /api/session/start — avoids needing to add
            # cross-thread locking to classes that were designed for
            # single-threaded access.
            counter.reset()
            classifier.reset()
            tracker_module.reset()
            was_active = True
            logger.info("Session became active — pipeline now detecting/tracking/counting")

        try:
            result = detector.infer(frame)
            tracked = tracker_module.update(result)

            for box, tracker_id in zip(tracked.xyxy, tracked.tracker_id):
                if tracker_id is None:
                    continue
                tid = int(tracker_id)
                cx = float((box[0] + box[2]) / 2)
                cy = float((box[1] + box[3]) / 2)
                gate = classifier.observe(tid, (cx, cy))
                if gate is not None:
                    mob_id = active_mob_state.get()
                    if mob_id is not None:
                        mob_store.increment(mob_id, gate)
                    else:
                        # Shouldn't happen — /api/session/start requires
                        # an active mob — but the live session count
                        # must not silently go uncounted if it ever does.
                        logger.warning("Confirmed a %s crossing with no active mob to credit it to", gate)

            annotated = draw_overlay(frame, tracked, tracker_module, classifier, counter)
            dashboard_state.update_frame(annotated)
            consecutive_errors = 0

        except Exception:
            consecutive_errors += 1
            logger.exception("Pipeline iteration failed (%d consecutive)", consecutive_errors)
            # Still push the raw frame so the dashboard shows *something*
            # live rather than freezing on the last successfully-annotated
            # one, which could otherwise look like a camera fault.
            dashboard_state.update_frame(frame)
            if consecutive_errors >= 30:
                logger.critical("30 consecutive pipeline failures — exiting for systemd to restart")
                stop_event.set()
                break
            time.sleep(0.05)

        if notifier is not None:
            try:
                notifier.notify("WATCHDOG=1")
            except Exception:
                logger.debug("sdnotify watchdog ping failed", exc_info=True)


def main():
    logger.info("RaceCount starting up")

    stream = LowLatencyRTSPStream(build_rtsp_url())
    stream.start()

    detector = YoloEngine(model_path=str(Path(__file__).resolve().parent / "models" / "yolov8n.pt"))
    tracker_module = SheepTracker()
    zone_manager = ZoneManager(config_path=str(Path(__file__).resolve().parent / "logic" / "zones_config.json"))
    counter = DirectionCounter()
    classifier = DirectionClassifier(zone_manager, counter)
    dashboard_state = DashboardState()
    mob_store = MobStore(data_dir=str(Path(__file__).resolve().parent / "data" / "mobs"))
    session_record_store = SessionRecordStore(data_dir=str(Path(__file__).resolve().parent / "data" / "sessions"))
    active_mob_state = ActiveMobState()
    session_state = SessionState()

    notifier = None
    try:
        import sdnotify
        notifier = sdnotify.SystemdNotifier()
        notifier.notify("READY=1")
        logger.info("systemd watchdog notifications enabled")
    except ImportError:
        logger.info("sdnotify not installed — systemd Type=notify watchdog pings disabled "
                     "(process-exit restarts via Restart=always still work)")

    stop_event = threading.Event()
    pipeline_thread = threading.Thread(
        target=pipeline_loop,
        args=(
            stream, detector, tracker_module, classifier, counter, dashboard_state,
            session_state, mob_store, active_mob_state, stop_event, notifier,
        ),
        daemon=True,
        name="pipeline",
    )
    pipeline_thread.start()

    try:
        run_dashboard(dashboard_state, counter, zone_manager, mob_store, session_record_store, active_mob_state, session_state)
    finally:
        logger.info("Shutting down")
        stop_event.set()
        pipeline_thread.join(timeout=3)
        stream.stop()


if __name__ == "__main__":
    main()
