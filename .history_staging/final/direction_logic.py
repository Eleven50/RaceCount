"""
Direction decision logic: crossing detection + a light confirmation step.

When a track's movement (previous position -> current position) crosses
a gate's line, that's a *candidate* crossing, not an immediate count —
a track's centroid can jitter frame-to-frame due to detection noise even
while the animal isn't really moving, and a sheep hesitating right at a
gate line could otherwise trigger a spurious crossing. Instead, once a
candidate crossing is seen, the next observation of that track must
still be on the far side of the line before it's confirmed and handed to
the counter. If it's back on the near side instead (the crossing was
noise, or the animal stepped back), the candidate is dropped — not
counted, but not blocked from crossing again for real later either.

This deliberately does NOT require several consecutive frames on the far
side (unlike this module's previous area-residency version) — one
confirming observation is enough. That's intentional: a real crossing
event is a specific moment in time, and requiring sustained dwell time
on the far side doesn't match "did the sheep go through this gate" as
directly as a single confirmation does. Brief occlusion (e.g. a
handler's arm) between the crossing and its confirmation isn't a
problem either way, since observe() simply isn't called for a track on
frames where it wasn't detected at all — a missed frame doesn't cancel
a pending confirmation, it just delays it slightly.
"""
import logging
import time
from typing import Optional

logger = logging.getLogger("racecount.logic")


class DirectionClassifier:
    def __init__(self, zone_manager, counter, confirm_observations: int = 2, stale_after_seconds: float = 300.0):
        """
        confirm_observations: how many total observations on the far
        side of the line (including the crossing one itself) are needed
        before the crossing is confirmed. 2 means "one more observation
        after the crossing, still on the far side" — cheap insurance
        against single-frame jitter without meaningfully delaying the
        count. 1 would count on the very first crossing detection with
        no confirmation at all.

        stale_after_seconds: how long a track can go unobserved before
        its state here is dropped, freeing memory over a long-running
        session. Deliberately time-based rather than "not in this
        frame's active set" — a track missing for one frame (e.g.
        occluded by a handler's arm right as it crosses) must not be
        treated as gone, only one that's been missing for a while.
        """
        if confirm_observations < 1:
            raise ValueError("confirm_observations must be at least 1")
        self.zones = zone_manager
        self.counter = counter
        self.confirm_observations = confirm_observations
        self.stale_after_seconds = stale_after_seconds
        self._last_point: dict = {}
        self._pending: dict = {}  # track_id -> {"gate": str, "landing_side": bool, "streak": int}
        self._confirmed: dict = {}  # track_id -> gate name, once confirmed — for overlay coloring only
        self._last_seen: dict = {}

    def observe(self, track_id: int, centroid) -> Optional[str]:
        """
        Feed one frame's centroid observation for a track. Returns the
        gate name the moment a crossing is confirmed for this track_id,
        otherwise None. centroid may be None (track not detected this
        frame) — this simply skips both crossing-detection and
        confirmation-progress for this call without losing state.
        """
        if centroid is None:
            return None

        prev = self._last_point.get(track_id)
        self._last_point[track_id] = centroid
        self._last_seen[track_id] = time.monotonic()
        self._prune_stale()

        pending = self._pending.get(track_id)
        if pending is not None:
            side_now = self.zones.side_of_line(pending["gate"], centroid)
            if side_now == pending["landing_side"]:
                pending["streak"] += 1
                if pending["streak"] >= self.confirm_observations:
                    gate = pending["gate"]
                    del self._pending[track_id]
                    self._confirmed[track_id] = gate
                    counted = self.counter.register_pass(track_id, gate)
                    if counted:
                        logger.info("Track %s confirmed crossing: %s", track_id, gate)
                        return gate
                    # Streak completed again for a track that was already
                    # counted (e.g. it jittered back across the line and
                    # re-triggered a fresh pending/confirm cycle for the
                    # same gate) — counter.register_pass() correctly
                    # refused to double-count, and that refusal has to
                    # propagate here too, or every caller reacting to a
                    # non-None return (like crediting a mob's persisted
                    # total) would double-count even though the counter
                    # itself didn't.
                    logger.debug("Track %s re-confirmed %s but was already counted — not re-signalled", track_id, gate)
                    return None
            else:
                logger.debug("Track %s crossing candidate for %s cancelled (moved back)", track_id, pending["gate"])
                del self._pending[track_id]
            return None

        gate = self.zones.crossed_gate(prev, centroid)
        if gate is not None:
            landing_side = self.zones.side_of_line(gate, centroid)
            if self.confirm_observations <= 1:
                self._confirmed[track_id] = gate
                counted = self.counter.register_pass(track_id, gate)
                if counted:
                    logger.info("Track %s confirmed crossing: %s (no confirmation required)", track_id, gate)
                    return gate
                logger.debug("Track %s re-crossed %s but was already counted — not re-signalled", track_id, gate)
                return None
            self._pending[track_id] = {"gate": gate, "landing_side": landing_side, "streak": 1}
        return None

    def forget(self, track_id: int):
        self._last_point.pop(track_id, None)
        self._pending.pop(track_id, None)
        self._confirmed.pop(track_id, None)
        self._last_seen.pop(track_id, None)

    def reset(self):
        """Clears all per-track state for a new session — pending
        crossings, confirmed-gate overlay markers, everything. Does NOT
        touch self.counter or self.zones; those are reset/reconfigured
        independently by whatever owns them."""
        self._last_point.clear()
        self._pending.clear()
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
        """For overlay coloring: the gate this track is currently
        mid-crossing (pending confirmation), or None."""
        pending = self._pending.get(track_id)
        return pending["gate"] if pending else None

    def get_confirmed_gate(self, track_id: int) -> Optional[str]:
        """For overlay coloring: the gate this track was confirmed
        crossing, if any — distinct from get_track_status(), which only
        reflects an in-progress (not yet confirmed) crossing."""
        return self._confirmed.get(track_id)

    def active_track_count(self) -> int:
        return len(self._last_point)
