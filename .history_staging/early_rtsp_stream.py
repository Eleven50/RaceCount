"""
Low-latency RTSP capture.

Runs a background thread that continuously reads frames and always
overwrites a single "latest frame" slot. There is no queue anywhere in
this module by design: a consumer calling get_latest_frame() always gets
the most recent frame available, and any frame it didn't get to in time
is simply gone. This is what gives the pipeline its "process only the
latest frame, drop stale frames" behaviour without any explicit
frame-skipping logic elsewhere — a slow consumer just sees fewer, always
current frames.
"""
import logging
import threading
import time

import cv2

from camera.config import PROCESSING_WIDTH

logger = logging.getLogger("racecount.camera")


class LowLatencyRTSPStream:
    def __init__(self, rtsp_url: str, reconnect_delay: float = 2.0, open_timeout: float = 5.0):
        self.rtsp_url = rtsp_url
        self.reconnect_delay = reconnect_delay
        self.open_timeout = open_timeout

        self.cap = None
        self.latest_frame = None
        self.frame_lock = threading.Lock()

        self.running = False
        self.thread = None
        self.connected = False

        self.frames_read = 0
        self.last_frame_time = 0.0

    def _open_capture(self):
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        # Ask FFmpeg to keep as little internal buffer as it will allow.
        # Not all builds honour this, which is why the reader loop below
        # never trusts buffering behaviour and always treats the newest
        # successful read as the only frame that matters.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _reader_loop(self):
        while self.running:
            if self.cap is None or not self.cap.isOpened():
                logger.warning("Camera not connected, attempting to connect...")
                self.cap = self._open_capture()
                if not self.cap.isOpened():
                    self.connected = False
                    logger.error(
                        "RTSP connect failed, retrying in %.1fs", self.reconnect_delay
                    )
                    self.cap.release()
                    self.cap = None
                    time.sleep(self.reconnect_delay)
                    continue
                self.connected = True
                logger.info("RTSP connected: %s", self._safe_url())

            ret, frame = self.cap.read()
            if not ret or frame is None:
                logger.warning("Frame read failed — dropping and reconnecting")
                self.connected = False
                self.cap.release()
                self.cap = None
                time.sleep(self.reconnect_delay)
                continue

            frame = self._downscale(frame)

            with self.frame_lock:
                self.latest_frame = frame
            self.frames_read += 1
            self.last_frame_time = time.monotonic()

    @staticmethod
    def _downscale(frame):
        h, w = frame.shape[:2]
        if w <= PROCESSING_WIDTH:
            return frame
        scale = PROCESSING_WIDTH / w
        new_size = (PROCESSING_WIDTH, int(round(h * scale)))
        return cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)

    def _safe_url(self) -> str:
        # Never log the password.
        if "@" in self.rtsp_url:
            scheme_and_creds, host_and_path = self.rtsp_url.split("@", 1)
            scheme = scheme_and_creds.split("://", 1)[0]
            return f"{scheme}://***:***@{host_and_path}"
        return self.rtsp_url

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._reader_loop, daemon=True, name="rtsp-reader")
        self.thread.start()

    def get_latest_frame(self):
        with self.frame_lock:
            return None if self.latest_frame is None else self.latest_frame.copy()

    def is_stale(self, max_age_seconds: float = 3.0) -> bool:
        if self.last_frame_time == 0.0:
            return True
        return (time.monotonic() - self.last_frame_time) > max_age_seconds

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.cap:
            self.cap.release()
            self.cap = None
