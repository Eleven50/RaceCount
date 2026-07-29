"""
UI backend: Flask app serving the touchscreen dashboard, an MJPEG video
feed, and a small JSON API for counts/status.

DashboardState is the thread-safe bridge between the CV pipeline thread
(which calls update_frame() once per processed frame) and Flask's
request-handling threads (which read from it on every dashboard poll /
video frame request). It holds only the single latest JPEG — never a
queue — consistent with the rest of the system's no-buffering design.
"""
import logging
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
from flask import Flask, Response, jsonify, render_template, request

logger = logging.getLogger("racecount.ui")

# Bumped manually. Shown in the header on every screen — change this one
# constant rather than hunting through templates when it needs updating.
# Format: v1.MAJOR.MINOR. The leading "1" is a fixed generation marker
# (would only change for a genuine v2 rewrite). MAJOR increments for a
# new screen or a significant visual/structural change; MINOR increments
# for a bugfix or small tweak that doesn't add a screen. Bump this by
# hand as part of whatever change warrants it -- there's no automated
# tracking, so accuracy from here on depends on actually doing this.
#
# Reconstructed history to establish where this starts counting from
# (not perfectly precise for the earliest phases, but a genuine account
# of the real major phases, not an arbitrary number):
#   1 - multi-screen split (Home/Start/Active/Calibrate) from the
#       original single-page dashboard
#   2 - History + Session Stats screens added
#   3 - Splash screen added
#   4 - RaceCount rebrand (replaced the original MobLogic branding)
#   5 - Settings screen, light/dark theme, throughput stats, numeral
#       font fix (this change)
APP_VERSION = "v1.5.0"


class DashboardState:
    def __init__(self):
        self._lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None
        self._latest_raw_jpeg: Optional[bytes] = None
        self._camera_connected = False
        self._fps = 0.0
        self._last_frame_time = 0.0

    def update_frame(self, frame_bgr, jpeg_quality: int = 80):
        ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        if not ok:
            logger.warning("JPEG encode failed, skipping frame for dashboard")
            return
        now = time.monotonic()
        with self._lock:
            if self._last_frame_time:
                dt = now - self._last_frame_time
                if dt > 0:
                    instantaneous = 1.0 / dt
                    self._fps = self._fps * 0.9 + instantaneous * 0.1 if self._fps else instantaneous
            self._last_frame_time = now
            self._latest_jpeg = buf.tobytes()

    def update_raw_frame(self, frame_bgr, jpeg_quality: int = 90):
        """
        Separate from update_frame(): the annotated frame has zone
        outlines / boxes / trajectories burned in, which would be
        confusing to calibrate against (you'd be tapping points relative
        to overlays drawn from the *previous* calibration). The
        calibration page needs the clean camera frame. Higher JPEG
        quality than the live stream since this is a single still image
        someone will be tapping precise points on, not 10+ frames/sec.
        """
        ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        if not ok:
            logger.warning("JPEG encode failed, skipping raw frame for calibration")
            return
        with self._lock:
            self._latest_raw_jpeg = buf.tobytes()

    def get_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

    def get_raw_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_raw_jpeg

    def set_camera_connected(self, connected: bool):
        with self._lock:
            self._camera_connected = connected

    def status(self) -> dict:
        with self._lock:
            has_frame = self._latest_jpeg is not None
            return {
                "camera_connected": self._camera_connected,
                "fps": round(self._fps, 1),
                "has_frame": has_frame,
            }


class ActiveMobState:
    """
    Which mob is currently selected for the session in progress, if any —
    set when Start screen's confirm action succeeds, read by the Active
    screen once that's wired up to actually log counts against it.

    Lives in memory only (not persisted) — unlike a mob's own counts,
    "which mob is active right now" doesn't need to survive a restart;
    if the app restarts, the natural recovery is going back through
    Start and picking (or continuing) a mob again, not silently resuming
    an old selection that predates the restart.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._mob_id: Optional[str] = None

    def set(self, mob_id: Optional[str]):
        with self._lock:
            self._mob_id = mob_id

    def get(self) -> Optional[str]:
        with self._lock:
            return self._mob_id


class SessionState:
    """
    Whether the pipeline should currently be doing the expensive part —
    YOLO inference, tracking, crossing classification, counting — as
    opposed to just idling with the raw camera feed visible. This is the
    on/off switch Start Session / End Session flip; the pipeline thread
    reads it every loop iteration and only pays the CPU/RAM cost of
    detection while it's True. Camera capture itself keeps running
    either way — it's comparatively cheap, and the calibration page and
    the idle Active-screen view both still need a live frame regardless
    of whether a session is running.

    Also tracks when the current session started, purely so
    /api/session/end can compute a duration for the session record —
    this isn't persisted itself (the session record is what's durable;
    this is just scratch state for one in-progress session).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._active = False
        self._started_at: Optional[float] = None

    def set_active(self, active: bool):
        with self._lock:
            self._active = active
            self._started_at = time.time() if active else None

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def get_started_at(self) -> Optional[float]:
        with self._lock:
            return self._started_at


def create_app(dashboard_state: DashboardState, counter, zone_manager, mob_store, session_record_store, active_mob_state: ActiveMobState, session_state: SessionState, settings_store) -> Flask:
    app = Flask(__name__)
    app.logger.setLevel(logging.WARNING)
    # werkzeug logs one line per HTTP request by default, including every
    # /api/counts poll and every MJPEG chunk — at a ~1.4Hz poll rate plus a
    # continuous video stream that fills the log file fast for no benefit.
    # Pipeline events (detections, counts, reconnects) already go to
    # logs/racecount.log via the loggers in each module.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    def mjpeg_generator():
        boundary = b"--frame"
        while True:
            jpeg = dashboard_state.get_jpeg()
            if jpeg is None:
                time.sleep(0.05)
                continue
            yield (
                boundary + b"\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            )
            time.sleep(0.01)

    # ---------------- Pages ----------------
    # One route per screen. All of them just render a template — each
    # screen's own JS pulls its actual data from the API routes below
    # after the page loads, so there's deliberately no server-side
    # templating of live data happening here beyond app_version and the
    # occasional URL parameter (record_id, show_back).

    @app.route("/splash")
    def splash():
        # Deliberately NOT theme-aware -- a fixed branding moment, same
        # reasoning as why it's exempt from the header/back-link system
        # every other screen shares.
        return render_template("splash.html")

    @app.route("/")
    def home():
        return render_template("home.html", app_version=APP_VERSION, show_back=False, theme=settings_store.get_theme())

    @app.route("/active")
    def active():
        return render_template("active.html", app_version=APP_VERSION, theme=settings_store.get_theme())

    @app.route("/start")
    def start():
        return render_template("start.html", app_version=APP_VERSION, theme=settings_store.get_theme())

    @app.route("/history")
    def history():
        return render_template("history.html", app_version=APP_VERSION, theme=settings_store.get_theme())

    @app.route("/session-stats/<record_id>")
    def session_stats(record_id):
        return render_template("session_stats.html", app_version=APP_VERSION, record_id=record_id, theme=settings_store.get_theme())

    @app.route("/settings")
    def settings():
        return render_template("settings.html", app_version=APP_VERSION, theme=settings_store.get_theme())

    @app.route("/api/settings")
    def api_settings_get():
        return jsonify({"theme": settings_store.get_theme()})

    @app.route("/api/settings/theme", methods=["POST"])
    def api_settings_set_theme():
        data = request.get_json(silent=True) or {}
        theme = data.get("theme")
        try:
            settings_store.set_theme(theme)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"theme": settings_store.get_theme()})

    @app.route("/api/settings/verify-pin", methods=["POST"])
    def api_settings_verify_pin():
        data = request.get_json(silent=True) or {}
        pin = data.get("pin", "")
        # Deliberately no lockout/rate-limiting -- this is a speed bump
        # against casual taps on a shared kiosk, not a security boundary
        # meant to resist someone actually trying to break in.
        if settings_store.check_advanced_pin(pin):
            return jsonify({"ok": True})
        return jsonify({"ok": False}), 403

    @app.route("/api/debug/info")
    def api_debug_info():
        # Camera username and IP are genuinely useful for confirming
        # RaceCount is pointed at the right device -- the password itself
        # is deliberately never exposed here, or anywhere in the UI.
        from camera.config import CAMERA_IP, CAMERA_PORT, CAMERA_USER

        pi_ip = "unknown"
        try:
            # UDP connect() never actually sends a packet -- this is a
            # local, offline-safe way to ask the kernel which interface
            # would be used, purely to read back its address.
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                pi_ip = s.getsockname()[0]
            finally:
                s.close()
        except OSError:
            pass

        return jsonify({
            "camera_ip": CAMERA_IP,
            "camera_port": CAMERA_PORT,
            "camera_user": CAMERA_USER,
            "pi_ip": pi_ip,
        })

    @app.route("/api/debug/run-tests", methods=["POST"])
    def api_debug_run_tests():
        # The fast, pure-Python suite only -- test_tracker.py needs a
        # real YOLO model load, which is genuinely slow and heavier than
        # a "is the deployed code healthy" check needs to be. Run that
        # one by hand (pytest tests/test_tracker.py) if you want that
        # coverage specifically.
        project_root = Path(__file__).resolve().parent.parent
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/test_logic.py", "tests/test_mobs.py", "tests/test_session_records.py", "-q"],
                capture_output=True, text=True, timeout=60, cwd=str(project_root),
            )
            return jsonify({
                "passed": result.returncode == 0,
                "output": (result.stdout + result.stderr)[-4000:],  # cap length, this renders in a small pre block
            })
        except subprocess.TimeoutExpired:
            return jsonify({"passed": False, "output": "Test run timed out after 60s."}), 500
        except Exception as e:
            return jsonify({"passed": False, "output": f"Could not run tests: {e}"}), 500

    # ---------------- Live pipeline (video feed, session-scoped counts) ----------------
    # counter/dashboard_state here are the LIVE, in-memory, current-
    # session view — resets on the next Start Session. Not to be
    # confused with a mob's persisted cumulative counts (mobs/mob_store.py)
    # or a finished session's durable record (mobs/session_record.py) —
    # see ARCHITECTURE.md if the distinction between these three isn't
    # obvious from here.

    @app.route("/video_feed")
    def video_feed():
        return Response(mjpeg_generator(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.route("/api/counts")
    def api_counts():
        return jsonify(counter.snapshot())

    @app.route("/api/status")
    def api_status():
        return jsonify(dashboard_state.status())

    @app.route("/api/reset", methods=["POST"])
    def api_reset():
        counter.reset()
        logger.info("Counts reset via dashboard")
        return jsonify({"ok": True})

    # ---------------- Mobs ----------------

    def mob_to_json(mob) -> dict:
        return {
            "id": mob.id,
            "name": mob.name,
            "gate_labels": mob.gate_labels,
            "counts": mob.counts,
            "total": mob.total,
            "created_at": mob.created_at,
            "updated_at": mob.updated_at,
        }

    @app.route("/api/mobs")
    def api_mobs_list():
        """Most-recently-updated first — for the Start screen's 'Use
        Previous' picker (and, later, History)."""
        return jsonify({"mobs": [mob_to_json(m) for m in mob_store.list_mobs()]})

    @app.route("/api/mobs", methods=["POST"])
    def api_mobs_create():
        data = request.get_json(silent=True) or {}
        name = data.get("name", "")
        gate_labels = data.get("gate_labels", {})
        try:
            mob = mob_store.create_mob(name, gate_labels)
        except (ValueError, TypeError) as e:
            return jsonify({"error": str(e)}), 400
        active_mob_state.set(mob.id)
        logger.info("Mob '%s' created and set active via Start screen", mob.name)
        return jsonify({"ok": True, "mob": mob_to_json(mob)})

    @app.route("/api/mobs/<mob_id>/select", methods=["POST"])
    def api_mobs_select(mob_id):
        """'Use Previous': make an existing mob the active one for this
        session, without creating anything or touching its counts."""
        mob = mob_store.get_mob(mob_id)
        if mob is None:
            return jsonify({"error": "No such mob"}), 404
        active_mob_state.set(mob.id)
        logger.info("Mob '%s' selected as active via Start screen (Use Previous)", mob.name)
        return jsonify({"ok": True, "mob": mob_to_json(mob)})

    @app.route("/api/mobs/<mob_id>")
    def api_mobs_get(mob_id):
        """Needed by the Session Stats screen to show the mob's current
        cumulative total after the just-ended session — the session
        record itself only has this session's contribution, not the
        mob's running total, and active_mob_state is already cleared by
        the time this screen loads."""
        mob = mob_store.get_mob(mob_id)
        if mob is None:
            return jsonify({"error": "No such mob"}), 404
        return jsonify({"mob": mob_to_json(mob)})

    @app.route("/api/mobs/<mob_id>", methods=["DELETE"])
    def api_mobs_delete(mob_id):
        if session_state.is_active() and active_mob_state.get() == mob_id:
            return jsonify({"error": "This mob is in use by a running session — end the session first."}), 400
        session_count = session_record_store.delete_records_for_mob(mob_id)
        deleted = mob_store.delete_mob(mob_id)
        if not deleted:
            return jsonify({"error": "No such mob"}), 404
        logger.info("Mob %s deleted via History (%d session record(s) removed with it)", mob_id, session_count)
        return jsonify({"ok": True, "deleted_session_records": session_count})

    @app.route("/api/mobs/active")
    def api_mobs_active():
        mob_id = active_mob_state.get()
        mob = mob_store.get_mob(mob_id) if mob_id else None
        return jsonify({"mob": mob_to_json(mob) if mob else None})

    # ---------------- Session (Start Session / End Session) ----------------

    @app.route("/api/session/status")
    def api_session_status():
        mob_id = active_mob_state.get()
        mob = mob_store.get_mob(mob_id) if mob_id else None
        return jsonify({
            "active": session_state.is_active(),
            "calibrated": zone_manager.calibrated,
            "mob": mob_to_json(mob) if mob else None,
            "started_at": session_state.get_started_at(),
        })

    @app.route("/api/session/start", methods=["POST"])
    def api_session_start():
        """
        Server-side gate, not just a client-side disabled button — the
        client already greys out Start Session under the same two
        conditions, but that's a UX nicety, not something to trust as
        the actual enforcement. Both conditions get checked again here
        regardless of what the button looked like when it was tapped.
        """
        if not zone_manager.calibrated:
            return jsonify({"error": "Gates aren't calibrated yet — calibrate before starting a session."}), 400
        if active_mob_state.get() is None:
            return jsonify({"error": "No mob selected — go to Start and create or pick one first."}), 400
        session_state.set_active(True)
        logger.info("Session started")
        return jsonify({"ok": True})

    @app.route("/api/session/end", methods=["POST"])
    def api_session_end():
        """
        Order matters here: everything needed for the record is read
        BEFORE session_state/active_mob_state get cleared, since those
        clears are what makes this data disappear from live state. The
        record itself is what's meant to survive past this point —
        DirectionCounter resets on the *next* Start Session, not now, so
        counter.snapshot() here is still this session's real numbers.
        """
        started_at = session_state.get_started_at()
        ended_at = time.time()
        mob_id = active_mob_state.get()
        mob = mob_store.get_mob(mob_id) if mob_id else None
        session_counts = counter.snapshot()["counts"]

        record = None
        if mob is not None and started_at is not None:
            record = session_record_store.create_record(
                mob_id=mob.id,
                mob_name=mob.name,
                gate_labels=mob.gate_labels,
                counts=session_counts,
                started_at=started_at,
                ended_at=ended_at,
            )
        else:
            logger.warning(
                "Session ended with no active mob or no start time recorded — "
                "no session record created (mob=%s, started_at=%s)", mob_id, started_at,
            )

        session_state.set_active(False)
        active_mob_state.set(None)
        logger.info("Session ended")
        return jsonify({"ok": True, "session_record_id": record.id if record else None})

    # ---------------- Session records (Session Stats / future History) ----------------

    def session_record_to_json(record) -> dict:
        return {
            "id": record.id,
            "mob_id": record.mob_id,
            "mob_name": record.mob_name,
            "gate_labels": record.gate_labels,
            "counts": record.counts,
            "total": record.total,
            "started_at": record.started_at,
            "ended_at": record.ended_at,
            "duration_seconds": record.duration_seconds,
        }

    @app.route("/api/sessions")
    def api_sessions_list():
        mob_id = request.args.get("mob_id")
        records = session_record_store.list_records(mob_id=mob_id)
        return jsonify({"sessions": [session_record_to_json(r) for r in records]})

    @app.route("/api/sessions/<record_id>")
    def api_sessions_get(record_id):
        record = session_record_store.get_record(record_id)
        if record is None:
            return jsonify({"error": "No such session record"}), 404
        return jsonify({"session": session_record_to_json(record)})

    # ---------------- Calibration ----------------

    @app.route("/calibrate")
    def calibrate_page():
        return render_template("calibrate.html", app_version=APP_VERSION)

    @app.route("/api/calibrate/snapshot")
    def calibrate_snapshot():
        """
        Returns ONE still frame (not a stream) for the calibration page to
        display and tap points on. A still image is deliberate — tapping
        precise points on a live-updating feed, especially over a phone/
        laptop on the LAN rather than the Pi's own screen, is much harder
        to do accurately than tapping a frozen frame.
        """
        jpeg = dashboard_state.get_raw_jpeg()
        if jpeg is None:
            return jsonify({"error": "No camera frame available yet — check the camera connection."}), 503
        return Response(jpeg, mimetype="image/jpeg")

    @app.route("/api/calibrate/existing")
    def calibrate_existing():
        """Previous session's tapped points, if any, so the calibration
        page can offer to show/reuse them instead of starting blank."""
        return jsonify({
            "gate_points": zone_manager.get_last_gate_points(),
            "calibrated": zone_manager.calibrated,
        })

    @app.route("/api/calibrate/save", methods=["POST"])
    def calibrate_save():
        data = request.get_json(silent=True) or {}
        gate_points = data.get("gate_points")
        if not gate_points:
            return jsonify({"error": "Missing gate_points"}), 400
        try:
            zone_manager.update_from_gate_points(gate_points)
        except (ValueError, TypeError) as e:
            return jsonify({"error": str(e)}), 400
        logger.info("Zones recalibrated via dashboard")
        return jsonify({"ok": True})

    return app


def run_dashboard(dashboard_state: DashboardState, counter, zone_manager, mob_store, session_record_store, active_mob_state: ActiveMobState, session_state: SessionState, settings_store, host: str = "0.0.0.0", port: int = 8080):
    """
    Binds to 0.0.0.0 so the dashboard is also reachable from other devices
    on the same LAN (e.g. checking counts from a phone in the yard) — this
    stays LAN-only and doesn't require internet. Restrict to host="127.0.0.1"
    if you'd rather the dashboard only be reachable from the Pi itself.

    threaded=True lets the MJPEG stream (a long-lived connection) and
    short API polls be served concurrently. use_reloader must stay False
    since this runs from a thread-owning process, not the Flask CLI.
    """
    app = create_app(dashboard_state, counter, zone_manager, mob_store, session_record_store, active_mob_state, session_state, settings_store)
    app.run(host=host, port=port, threaded=True, use_reloader=False)
