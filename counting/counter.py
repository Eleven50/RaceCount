"""
Direction counter: per-track-ID single-count memory.

A given track_id can increment a counter at most once, ever — this is
the "prevent double counting" guarantee, enforced independently of the
debounce logic in logic/direction_logic.py (belt and braces: even if a
caller mistakenly calls register_pass twice for the same confirmed
track, only the first call counts).

Counts are in-memory only, by design: the spec calls for no unnecessary
disk writes, and a physical race processes sheep continuously, so
writing to disk on every single pass would mean frequent SD-card writes
over a working day. This means a mid-session crash-and-restart (systemd
will restart the process automatically) resets counts to zero. If you'd
rather counts survive a restart, the lowest-cost option is a periodic
snapshot (e.g. write counts.json every 60s, or only on clean shutdown)
rather than a write per event — that's a real product tradeoff worth
deciding deliberately rather than something this module should assume.
"""
import logging
import threading
import time

logger = logging.getLogger("racecount.counting")

DIRECTIONS = ("left", "straight", "right")


class DirectionCounter:
    def __init__(self):
        self._lock = threading.Lock()
        self._counts = {d: 0 for d in DIRECTIONS}
        self._counted_ids: set = set()
        self._session_start = time.time()

    def register_pass(self, track_id: int, direction: str) -> bool:
        """Returns True only if this call actually incremented a count."""
        if direction not in DIRECTIONS:
            logger.warning("Ignoring unknown direction '%s' for track %s", direction, track_id)
            return False
        with self._lock:
            if track_id in self._counted_ids:
                return False
            self._counted_ids.add(track_id)
            self._counts[direction] += 1
            counts_snapshot = dict(self._counts)
        logger.info("Counted track %s -> %s (totals: %s)", track_id, direction, counts_snapshot)
        return True

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "counts": dict(self._counts),
                "total": sum(self._counts.values()),
                "session_start": self._session_start,
            }

    def reset(self):
        with self._lock:
            self._counts = {d: 0 for d in DIRECTIONS}
            self._counted_ids.clear()
            self._session_start = time.time()
        logger.info("Counters reset")
