"""
Zone geometry: the 3 drafting-gate rectangles (left / straight / right)
and point-in-rectangle / segment-crosses-rectangle tests against them.

Each gate is a rectangle (2 tapped points — opposite corners), not a
line. A sheep is counted for a gate once its track is genuinely seen
entering that rectangle and then exiting it — matching how the physical
gate actually works: an animal has to be *in* the gate area to have gone
through it. The rectangle is meant to be calibrated with real gap from
the physical gate structure itself, not flush against it — a spacious
box gives real frames of opportunity to catch the animal genuinely
inside it, which is what keeps this robust at low frame rates, not the
thinness of the zone.

That said, a single frame landing inside the box isn't the only way
entry is detected: path_skipped_through() also treats the frame-to-frame
*movement*
(previous position -> current position) as crossing into the rectangle
if that segment intersects any of its 4 edges, even if neither endpoint
itself landed inside on that particular frame. That's the same
segment-intersection math this module used when gates were lines,
reused here as a safety net under the area test rather than replacing
it — an animal moving fast enough to skip over registering "inside" on
any single frame still gets caught by its path having genuinely crossed
the boundary.

This module only knows about geometry — no debouncing/counting
decisions live here (see direction_logic.py for that). Keeping it this
narrow makes it independently testable with plain coordinates and no
camera/model dependencies.

Zone coordinates are in processed-frame pixel space (see
camera/config.PROCESSING_WIDTH) and MUST be calibrated to your actual
mounted camera view — use the in-dashboard calibration page at /calibrate
(or the standalone tools/calibrate_zones.py) rather than relying on the
placeholder thirds-of-frame rectangles below.
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
    only that it's consistent, which is all _segments_intersect needs."""
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


def _normalize_rect(a, b):
    """Two arbitrary opposite corners -> (x_min, y_min, x_max, y_max)."""
    x_min, x_max = (a[0], b[0]) if a[0] <= b[0] else (b[0], a[0])
    y_min, y_max = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])
    return x_min, y_min, x_max, y_max


def _point_in_rect(point, rect) -> bool:
    x_min, y_min, x_max, y_max = rect
    return x_min <= point[0] <= x_max and y_min <= point[1] <= y_max


def _segment_crosses_edges(p1, p2, rect) -> bool:
    """True if the segment p1->p2 crosses any of the rectangle's 4
    edges. Deliberately does NOT check whether either endpoint is
    inside — that's a separate question (_point_in_rect), and
    conflating the two was a real bug: reusing "either endpoint inside
    OR crosses an edge" for both entry AND exit detection meant a
    track that had just entered would appear to still be "crossing"
    on the very next frame too, since its previous point was inside,
    even once its current point had genuinely left. Entry detection
    and "is it still inside" need different checks."""
    x_min, y_min, x_max, y_max = rect
    corners = ((x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max))
    for i in range(4):
        edge_a, edge_b = corners[i], corners[(i + 1) % 4]
        if _segments_intersect(p1, p2, edge_a, edge_b):
            return True
    return False


class ZoneManager:
    def __init__(self, config_path: str = DEFAULT_ZONES_PATH):
        self.config_path = config_path
        self.zones: dict = {}  # name -> (point_a, point_b), opposite rectangle corners
        self.calibrated = False
        self._lock = threading.Lock()
        self._load_zones()

    def _load_zones(self):
        path = Path(self.config_path)
        if not path.exists():
            logger.warning(
                "Zone config %s not found — using placeholder thirds-of-frame "
                "rectangles. Direction counts will not be meaningful until you "
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
            if name in ZONE_NAMES
        }
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
            "left": ((260, 260), (380, 620)),
            "straight": ((580, 160), (700, 620)),
            "right": ((820, 260), (940, 620)),
        }

    def update_from_gate_points(self, gate_points: dict):
        """
        gate_points: {"left": [[x1,y1],[x2,y2]], ...} — 1, 2, or 3 of
        the 3 possible gates, whichever are actually in use for this
        setup. Each value is exactly 2 points — opposite corners of the
        gate's rectangle, calibrated with real gap from the physical
        gate structure rather than flush against it. Applied immediately
        (so the running pipeline picks it up on the very next frame —
        no restart needed) and persisted to disk. Replaces the full
        calibrated set — calling this with just {"left": ...} after
        previously calibrating all 3 means only "left" is calibrated
        afterwards, not "left plus whatever was there before".
        """
        if not gate_points:
            raise ValueError("At least one gate is required")
        unknown = [n for n in gate_points if n not in ZONE_NAMES]
        if unknown:
            raise ValueError(f"Unknown gate(s): {unknown}")

        new_zones = {}
        for name, points in gate_points.items():
            if len(points) != 2:
                raise ValueError(f"Gate '{name}' needs exactly 2 points, got {len(points)}")
            a, b = tuple(points[0]), tuple(points[1])
            if abs(a[0] - b[0]) < 5 or abs(a[1] - b[1]) < 5:
                raise ValueError(f"Gate '{name}' rectangle is too thin — tap two corners with real width and height apart")
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
            payload = {name: [list(a), list(b)] for name, (a, b) in self.zones.items()}
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

    def contains(self, gate_name: str, point) -> Optional[bool]:
        """Simple, direct check: is `point` inside gate_name's rectangle
        right now. None if gate_name is unknown or point is None."""
        if point is None:
            return None
        with self._lock:
            gate = self.zones.get(gate_name)
        if gate is None:
            return None
        rect = _normalize_rect(gate[0], gate[1])
        return _point_in_rect(point, rect)

    def path_skipped_through(self, gate_name: str, prev_point, curr_point) -> Optional[bool]:
        """True if prev_point and curr_point are both outside
        gate_name's rectangle, but the straight-line path between them
        passed through it anyway — the fast-movement safety net for a
        track that moved far enough in one frame-to-frame step to never
        land inside on any single observed frame (see module
        docstring). Only meaningful to call when contains() is already
        False for both points; doesn't re-check that itself. None if
        gate_name is unknown or either point is None."""
        if prev_point is None or curr_point is None:
            return None
        with self._lock:
            gate = self.zones.get(gate_name)
        if gate is None:
            return None
        rect = _normalize_rect(gate[0], gate[1])
        return _segment_crosses_edges(prev_point, curr_point, rect)

    def draw_zones(self, frame: np.ndarray) -> np.ndarray:
        with self._lock:
            zones_snapshot = dict(self.zones)
            calibrated = self.calibrated
        for name, (a, b) in zones_snapshot.items():
            color = ZONE_COLORS.get(name, ZONE_COLORS[None])
            x_min, y_min, x_max, y_max = _normalize_rect(a, b)
            pt_min = (int(x_min), int(y_min))
            pt_max = (int(x_max), int(y_max))
            cv2.rectangle(frame, pt_min, pt_max, color, 3)
            for corner in (pt_min, pt_max, (pt_min[0], pt_max[1]), (pt_max[0], pt_min[1])):
                cv2.circle(frame, corner, 5, color, -1)
                cv2.circle(frame, corner, 5, (20, 20, 20), 2)
            cv2.putText(frame, name.upper(), (pt_min[0] + 6, pt_min[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        if not calibrated:
            cv2.putText(
                frame, "UNCALIBRATED ZONES - tap the gear icon to calibrate",
                (10, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2,
            )
        return frame
