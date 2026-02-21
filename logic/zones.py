"""
Zone geometry: the 3 drafting-gate lines (left / straight / right) and
pure trajectory-crossing tests against them.

Each gate is a single line segment (2 tapped points — its physical
endpoints), not an area. A sheep is classified into a gate's direction
when its trajectory's most recent movement (previous position -> current
position) crosses that gate's line segment — a standard line-crossing /
"trip-wire" counting approach, which matches the actual physical setup
(3 straight gate arms) more directly than an area-residency zone would,
and degrades better at low frame rates: a thin area can be stepped over
entirely between two processed frames if the animal moves far enough
per frame, but a crossing test only needs the segment between two known
points to intersect the gate line, regardless of how far apart those
two points are.

This module only knows about geometry — no debouncing/confirmation
decisions live here (see direction_logic.py for that). Keeping it this
narrow makes it independently testable with plain coordinates and no
camera/model dependencies.

Zone coordinates are in processed-frame pixel space (see
camera/config.PROCESSING_WIDTH) and MUST be calibrated to your actual
mounted camera view — use the in-dashboard calibration page at /calibrate
(or the standalone tools/calibrate_zones.py) rather than relying on the
placeholder thirds-of-frame lines below.
"""
import json
import logging
import threading
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("racecount.logic")

DEFAULT_ZONES_PATH = "logic/zones_config.json"
ZONE_NAMES = ("left", "straight", "right")
# BGR tuples (OpenCV order — NOT RGB). These correspond to the same
# --gate-left / --gate-straight / --gate-right accent colors defined in
# ui/static/brand.css (#E8A33D amber / #4CAF6D green / #4A90D9 steel
# blue) so the video overlay and every screen's counters/labels read as
# one coherent color language, not two systems
# bolted together.
ZONE_COLORS = {"left": (61, 163, 232), "straight": (109, 175, 76), "right": (217, 144, 74), None: (150, 150, 150)}


def _orientation(a, b, c) -> float:
    """Signed area of triangle abc — sign gives which side of line ab
    point c is on. Positive/negative meaning isn't important on its own,
    only that it's consistent, which is all side_of_line/crossing need."""
    return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(a, b, c, d) -> bool:
    """True if segment ab properly crosses segment cd. Standard
    orientation-based test; deliberately doesn't special-case exact
    collinearity — with floating-point centroid data landing exactly on
    a line is a measure-zero event not worth the extra complexity."""
    d1 = _orientation(c, d, a)
    d2 = _orientation(c, d, b)
    d3 = _orientation(a, b, c)
    d4 = _orientation(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0


class ZoneManager:
    def __init__(self, config_path: str = DEFAULT_ZONES_PATH):
        self.config_path = config_path
        self.zones: dict = {}  # name -> (point_a, point_b), each an (x, y) tuple
        self.calibrated = False
        self._lock = threading.Lock()
        self._load_zones()

    def _load_zones(self):
        path = Path(self.config_path)
        if not path.exists():
            logger.warning(
                "Zone config %s not found — using placeholder thirds-of-frame "
                "lines. Direction counts will not be meaningful until you "
                "calibrate at /calibrate (or run tools/calibrate_zones.py).",
                self.config_path,
            )
            with self._lock:
                self.zones = self._placeholder_zones()
                self.calibrated = False
            return

        with open(path) as f:
            raw = json.load(f)
        zones = {
            name: (tuple(points[0]), tuple(points[1]))
            for name, points in raw.items()
            if name in ZONE_NAMES  # skip "_gate_points" metadata if present from a rectangle-era save
        }
        missing = [n for n in ZONE_NAMES if n not in zones]
        if missing:
            logger.warning("Zone config is missing zones: %s", missing)
        with self._lock:
            self.zones = zones
            self.calibrated = True
        logger.info("Loaded calibrated zones from %s: %s", self.config_path, list(zones.keys()))

    @staticmethod
    def _placeholder_zones() -> dict:
        # Rough thirds-of-frame placeholder assuming PROCESSING_WIDTH=960.
        # Purely so the system runs end-to-end before calibration — do
        # not rely on these for real counts.
        return {
            "left": ((320, 300), (320, 720)),
            "straight": ((640, 200), (640, 720)),
            "right": ((640, 300), (960, 300)),
        }

    def update_from_gate_points(self, gate_points: dict):
        """
        gate_points: {"left": [[x1,y1],[x2,y2]], "straight": [...], "right": [...]}
        Each value is exactly 2 points — the gate's physical endpoints
        (Point A, Point B in the calibration UI). Applied immediately (so
        the running pipeline picks it up on the very next frame — no
        restart needed) and persisted to disk.
        """
        missing = [n for n in ZONE_NAMES if n not in gate_points]
        if missing:
            raise ValueError(f"Missing gate(s): {missing}")

        new_zones = {}
        for name in ZONE_NAMES:
            points = gate_points[name]
            if len(points) != 2:
                raise ValueError(f"Gate '{name}' needs exactly 2 points, got {len(points)}")
            a, b = tuple(points[0]), tuple(points[1])
            if np.linalg.norm(np.array(a) - np.array(b)) < 5:
                raise ValueError(f"Gate '{name}' points are too close together — tap two distinct points")
            new_zones[name] = (a, b)

        with self._lock:
            self.zones = new_zones
            self.calibrated = True

        self._persist(gate_points)
        logger.info("Zones updated from in-UI calibration and applied live: %s", list(new_zones.keys()))

    def _persist(self, gate_points: dict):
        path = Path(self.config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            payload = {name: [list(self.zones[name][0]), list(self.zones[name][1])] for name in ZONE_NAMES}
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
        logger.info("Saved zones to %s", self.config_path)

    def get_last_gate_points(self) -> Optional[dict]:
        """Returns the saved {gate: [pointA, pointB]} points, if any, so
        the calibration page can pre-fill/show your previous taps
        instead of starting from nothing."""
        path = Path(self.config_path)
        if not path.exists():
            return None
        try:
            with open(path) as f:
                raw = json.load(f)
            return {name: raw[name] for name in ZONE_NAMES if name in raw}
        except (json.JSONDecodeError, OSError, KeyError):
            return None

    def side_of_line(self, gate_name: str, point) -> Optional[bool]:
        """Which side of gate_name's line `point` is on — an arbitrary
        but consistent True/False, used to confirm a crossing "stuck"
        rather than immediately jittering back. None if gate_name is
        unknown or point is None."""
        if point is None:
            return None
        with self._lock:
            gate = self.zones.get(gate_name)
        if gate is None:
            return None
        return _orientation(gate[0], gate[1], point) > 0

    def crossed_gate(self, prev_point, curr_point) -> Optional[str]:
        """Returns the name of the gate whose line the movement from
        prev_point to curr_point crosses, or None. If prev_point is None
        (first observation of a track, or a gap), there's nothing to
        test against yet."""
        if prev_point is None or curr_point is None:
            return None
        with self._lock:
            zones_snapshot = dict(self.zones)
        for name, (a, b) in zones_snapshot.items():
            if _segments_intersect(prev_point, curr_point, a, b):
                return name
        return None

    def draw_zones(self, frame: np.ndarray) -> np.ndarray:
        with self._lock:
            zones_snapshot = dict(self.zones)
            calibrated = self.calibrated
        for name, (a, b) in zones_snapshot.items():
            color = ZONE_COLORS.get(name, ZONE_COLORS[None])
            pt_a = tuple(int(v) for v in a)
            pt_b = tuple(int(v) for v in b)
            cv2.line(frame, pt_a, pt_b, color, 4)
            for endpoint in (pt_a, pt_b):
                cv2.circle(frame, endpoint, 6, color, -1)
                cv2.circle(frame, endpoint, 6, (20, 20, 20), 2)
            mid = ((pt_a[0] + pt_b[0]) // 2, (pt_a[1] + pt_b[1]) // 2)
            cv2.putText(frame, name.upper(), (mid[0] + 10, mid[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        if not calibrated:
            cv2.putText(
                frame, "UNCALIBRATED ZONES - tap the gear icon to calibrate",
                (10, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2,
            )
        return frame
