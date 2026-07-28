"""
Tracking: assigns persistent IDs to per-frame detections and keeps a
short rolling trajectory (centroid history) per ID.

This module is deliberately class-agnostic — it only ever receives
already sheep-filtered detections from detection/yolo_engine.py, so
tracking and detection stay fully separable (you could point this at a
different detector entirely and it wouldn't need to change).

ByteTrack (via the `supervision` library) was chosen over plain SORT
specifically because it associates low-confidence detections instead of
discarding them outright — which matters here because a handler's arm
briefly occluding a sheep at the gate typically shows up as a
lower-confidence or partially-missed detection for a frame or two, not a
total absence. Plain SORT would be more prone to dropping and
re-assigning a new ID across that kind of brief occlusion.

NOTE on supervision's deprecation warning: as of supervision 0.28+,
sv.ByteTrack is marked deprecated in favour of a separate `trackers`
package (ByteTrackTracker). At the time this was written, `pip install
trackers` produced a broken install in testing (metadata installed, but
no importable package) — so this module deliberately stays on
sv.ByteTrack, pinned via `supervision<0.30.0` in requirements.txt so a
routine `pip install --upgrade` can't silently remove it. Before
upgrading past that pin, re-check whether the `trackers` package
installs cleanly and re-test tracking behaviour against this module's
tests.
"""
import logging
import time
from collections import defaultdict, deque

import supervision as sv

logger = logging.getLogger("racecount.tracking")


class SheepTracker:
    def __init__(
        self,
        trajectory_length: int = 30,
        track_activation_threshold: float = 0.45,
        lost_track_buffer: int = 30,
        minimum_matching_threshold: float = 0.8,
        frame_rate: int = 15,
        stale_after_seconds: float = 300.0,
    ):
        """
        frame_rate should match your actual measured pipeline FPS once
        deployed (not the camera's native FPS) — ByteTrack's motion model
        uses it to reason about how far an object could plausibly move
        between frames. Update it after benchmarking on the Pi.
        """
        # Stashed so reset() can build a fresh sv.ByteTrack with the same
        # config rather than hardcoding the params in two places.
        self._byte_track_kwargs = dict(
            track_activation_threshold=track_activation_threshold,
            lost_track_buffer=lost_track_buffer,
            minimum_matching_threshold=minimum_matching_threshold,
            frame_rate=frame_rate,
        )
        self.tracker = sv.ByteTrack(**self._byte_track_kwargs)
        self.trajectory_length = trajectory_length
        self.stale_after_seconds = stale_after_seconds
        self.trajectories: dict[int, deque] = defaultdict(lambda: deque(maxlen=self.trajectory_length))
        self._last_seen: dict[int, float] = {}

    def update(self, ultralytics_result) -> sv.Detections:
        detections = sv.Detections.from_ultralytics(ultralytics_result)
        tracked = self.tracker.update_with_detections(detections)

        now = time.monotonic()
        for box, tracker_id in zip(tracked.xyxy, tracked.tracker_id):
            if tracker_id is None:
                continue
            tid = int(tracker_id)
            cx = float((box[0] + box[2]) / 2)
            cy = float((box[1] + box[3]) / 2)
            self.trajectories[tid].append((cx, cy))
            self._last_seen[tid] = now

        self._prune_stale(now)
        return tracked

    def get_trajectory(self, tracker_id: int) -> list:
        return list(self.trajectories.get(tracker_id, ()))

    def get_centroid(self, tracker_id: int):
        traj = self.trajectories.get(tracker_id)
        return traj[-1] if traj else None

    def reset(self):
        """
        Full reset for a new session: track IDs from a previous session
        (possibly hours or days ago) have no business persisting into a
        fresh one, and re-instantiating ByteTrack itself (rather than
        just clearing this module's own dicts) also wipes whatever
        internal motion-model state it holds that isn't exposed here.
        Track IDs restart from 1, which is also just easier to reason
        about when debugging a specific session's log output.
        """
        self.tracker = sv.ByteTrack(**self._byte_track_kwargs)
        self.trajectories.clear()
        self._last_seen.clear()
        logger.info("SheepTracker reset for new session")

    def _prune_stale(self, now: float):
        """
        ByteTrack expires lost tracks internally after lost_track_buffer
        frames, but this module's own trajectory/last-seen dicts would
        otherwise grow unbounded over a multi-day uptime (a new track ID
        every pass, forever). This keeps long-running memory flat.
        """
        stale_ids = [
            tid for tid, last_seen in self._last_seen.items()
            if now - last_seen > self.stale_after_seconds
        ]
        for tid in stale_ids:
            self.trajectories.pop(tid, None)
            self._last_seen.pop(tid, None)
        if stale_ids:
            logger.debug("Pruned %d stale track(s)", len(stale_ids))
