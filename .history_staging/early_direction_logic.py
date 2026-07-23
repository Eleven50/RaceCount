"""
Direction decision logic: debounced zone classification per track.

A track must be observed inside the same zone for `confirm_frames` out
of its last `window_frames` processed observations before that direction
is confirmed and handed to the counter. Using "N-out-of-last-M" rather
than "N consecutive" is deliberate: it absorbs brief flicker — e.g. a
detection dropout for a frame or two while a handler's arm occludes the
sheep at the gate — without needing an unbroken run of clean frames.

This module ties zones.py (geometry) to counting/counter.py (the
count-once-per-ID memory); zones.py stays purely geometric and
independently testable.
"""
import logging
from collections import defaultdict, deque
from typing import Optional

logger = logging.getLogger("racecount.logic")


class DirectionClassifier:
    def __init__(self, zone_manager, counter, window_frames: int = 6, confirm_frames: int = 4):
        if confirm_frames > window_frames:
            raise ValueError("confirm_frames cannot exceed window_frames")
        self.zones = zone_manager
        self.counter = counter
        self.window_frames = window_frames
        self.confirm_frames = confirm_frames
        self._history: dict = defaultdict(lambda: deque(maxlen=self.window_frames))

    def observe(self, track_id: int, centroid) -> Optional[str]:
        """
        Feed one frame's centroid observation for a track. Returns the
        direction the moment it's first confirmed for this track_id
        (whether or not this call actually incremented the counter — a
        track already counted from a prior confirmation simply won't
        increment again, per counter.py's own ID memory), otherwise None.
        """
        zone = self.zones.classify_point(centroid)
        history = self._history[track_id]
        history.append(zone)

        for direction in ("left", "straight", "right"):
            hits = sum(1 for z in history if z == direction)
            if hits >= self.confirm_frames:
                self.counter.register_pass(track_id, direction)
                return direction
        return None

    def forget(self, track_id: int):
        self._history.pop(track_id, None)

    def active_track_count(self) -> int:
        return len(self._history)
