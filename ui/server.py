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
import threading
import time
from typing import Optional

import cv2
from flask import Flask, Response, jsonify, render_template

logger = logging.getLogger("racecount.ui")


class DashboardState:
    def __init__(self):
        self._lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None
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

    def get_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

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


def create_app(dashboard_state: DashboardState, counter) -> Flask:
    app = Flask(__name__)
    app.logger.setLevel(logging.WARNING)  # Flask's own request logging is noisy for a kiosk; pipeline events already go to logs/racecount.log

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

    @app.route("/")
    def index():
        return render_template("dashboard.html")

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

    return app


def run_dashboard(dashboard_state: DashboardState, counter, host: str = "0.0.0.0", port: int = 8080):
    """
    Binds to 0.0.0.0 so the dashboard is also reachable from other devices
    on the same LAN (e.g. checking counts from a phone in the yard) — this
    stays LAN-only and doesn't require internet. Restrict to host="127.0.0.1"
    if you'd rather the dashboard only be reachable from the Pi itself.

    threaded=True lets the MJPEG stream (a long-lived connection) and
    short API polls be served concurrently. use_reloader must stay False
    since this runs from a thread-owning process, not the Flask CLI.
    """
    app = create_app(dashboard_state, counter)
    app.run(host=host, port=port, threaded=True, use_reloader=False)
