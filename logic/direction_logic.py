"""
Direction decision logic: enter-then-exit tracking against each gate's
rectangle.

A track is counted for a gate the moment it's confirmed to have exited
that gate's rectangle, but only if it was genuinely marked as having
entered it first — "once the animal tracks in, then out of the zone, it
can be counted." No consecutive-frame dwell requirement inside the
rectangle (unlike this module's much earlier area-residency version) —
a single frame (or a fast-movement segment crossing, see zones.py) is
enough to mark entry, since a real rectangle with genuine gap from the
physical gate is a stable, sustained thing to be inside, not a knife-
edge boundary prone to frame-to-frame jitter the way a bare line was.

The actual "did we already count this animal for this gate" protection
lives one layer down, in counting/counter.py, not here — that's the
single source of truth per track_id+gate, and it's what makes it safe
for this module to stay this simple: even if per-track state here ever
somehow flickered (detection noise near a rectangle edge causing a
spurious extra enter/exit cycle), the counter itself refuses to count
the same track_id+gate more than once, so the final tally is still
correct regardless of what happens up here.
"""
import logging
import time
from typing import Optional

logger = logging.getLogger("racecount.logic")


class DirectionClassifier:
    def __init__(self, zone_manager, counter, stale_after_seconds: float = 300.0):
        """
        stale_after_seconds: how long a track can go unobserved before
        its state here is dropped, freeing memory over a long-running
        session. Deliberately time-based rather than "not in this
        frame's active set" — a track missing for one frame (e.g.
        occluded by a handler's arm right as it passes through) must not
        be treated as gone, only one that's been missing for a while.
        """
        self.zones = zone_manager
        self.counter = counter
        self.stale_after_seconds = stale_after_seconds
        self._last_point: dict = {}
        self._inside: dict = {}  # track_id -> set of gate names currently "entered"
        self._confirmed: dict = {}  # track_id -> gate name, once confirmed — for overlay coloring only
        self._last_seen: dict = {}

    def observe(self, track_id: int, centroid) -> Optional[str]:
        """
        Feed one frame's centroid observation for a track. Returns the
        gate name the moment an exit-after-entry is confirmed (and
        actually counted — see counter.register_pass) for this
        track_id, otherwise None. centroid may be None (track not
        detected this frame) — this simply skips both entry/exit
        detection for this call without losing state.
        """
        if centroid is None:
            return None

        prev = self._last_point.get(track_id)
        self._last_point[track_id] = centroid
        self._last_seen[track_id] = time.monotonic()
        self._prune_stale()

        currently_inside = self._inside.setdefault(track_id, set())
        result = None

        for gate in self.zones.zones.keys():
            was_inside = gate in currently_inside
            curr_inside = self.zones.contains(gate, centroid)

            if was_inside:
                if not curr_inside:
                    # Genuine exit: was inside, now directly isn't.
                    currently_inside.discard(gate)
                    counted = self.counter.register_pass(track_id, gate)
                    if counted:
                        logger.info("Track %s confirmed pass: %s", track_id, gate)
                        self._confirmed[track_id] = gate
                        result = gate
                    else:
                        logger.debug("Track %s re-exited %s but was already counted — not re-signalled", track_id, gate)
                # else: still inside, nothing to do this frame.
            else:
                if curr_inside:
                    currently_inside.add(gate)
                    logger.debug("Track %s entered gate %s", track_id, gate)
                elif prev is not None and self.zones.path_skipped_through(gate, prev, centroid):
                    # Moved fast enough to skip from outside straight
                    # through to outside again within one observation —
                    # both the entry and the exit happened in this same
                    # step, so count it now rather than waiting for a
                    # later frame that will never come.
                    counted = self.counter.register_pass(track_id, gate)
                    if counted:
                        logger.info("Track %s confirmed pass (fast skip-through): %s", track_id, gate)
                        self._confirmed[track_id] = gate
                        result = gate
                    else:
                        logger.debug("Track %s skip-through %s but was already counted — not re-signalled", track_id, gate)

        return result

    def forget(self, track_id: int):
        self._last_point.pop(track_id, None)
        self._inside.pop(track_id, None)
        self._confirmed.pop(track_id, None)
        self._last_seen.pop(track_id, None)

    def reset(self):
        """Clears all per-track state for a new session — in-progress
        entries, confirmed-gate overlay markers, everything. Does NOT
        touch self.counter or self.zones; those are reset/reconfigured
        independently by whatever owns them."""
        self._last_point.clear()
        self._inside.clear()
        self._confirmed.clear()
        self._last_seen.clear()
        logger.info("DirectionClassifier state reset for new session")

    def _prune_stale(self):
        now = time.monotonic()
        stale_ids = [
            tid for tid, last_seen in self._last_seen.items()
            if now - last_seen > self.stale_after_seconds
        ]
        for tid in stale_ids:
            self.forget(tid)
        if stale_ids:
            logger.debug("Pruned %d stale classifier track(s)", len(stale_ids))

    def get_track_status(self, track_id: int) -> Optional[str]:
        """For overlay coloring: a gate this track is currently inside
        (entered, not yet exited), or None. If a track is somehow inside
        more than one gate's rectangle at once (shouldn't happen with
        sensible calibration, but not geometrically impossible), returns
        whichever one is found first — this is a display hint, not a
        counting decision."""
        inside = self._inside.get(track_id)
        return next(iter(inside), None) if inside else None

    def get_confirmed_gate(self, track_id: int) -> Optional[str]:
        """For overlay coloring: the gate this track was last confirmed
        passing through, if any — distinct from get_track_status(),
        which only reflects a currently in-progress (not yet exited)
        entry."""
        return self._confirmed.get(track_id)

    def active_track_count(self) -> int:
        return len(self._last_point)
