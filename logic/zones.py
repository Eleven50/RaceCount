"""
Zone geometry: the 3 drafting-gate polygons (left / straight / right) and
pure point-in-polygon classification against them.

This module only knows about geometry — no debouncing, no counting
decisions live here (see direction_logic.py for that). Keeping it this
narrow makes it independently testable with plain coordinates and no
camera/model dependencies.

Zone coordinates are in processed-frame pixel space (see
camera/config.PROCESSING_WIDTH) and MUST be calibrated to your actual
mounted camera view — run tools/calibrate_zones.py against a live frame
rather than relying on the placeholder thirds-of-frame zones below.
"""
import json
import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("racecount.logic")

DEFAULT_ZONES_PATH = "logic/zones_config.json"
ZONE_NAMES = ("left", "straight", "right")
ZONE_COLORS = {"left": (61, 163, 232), "straight": (109, 175, 76), "right": (61, 90, 219), None: (150, 150, 150)}
# BGR tuples (OpenCV order) — matches the dashboard's accent colors so the
# video overlay and the counter tiles read as one coherent color language.


class ZoneManager:
    def __init__(self, config_path: str = DEFAULT_ZONES_PATH):
        self.config_path = config_path
        self.zones: dict = {}
        self.calibrated = False
        self._load_zones()

    def _load_zones(self):
        path = Path(self.config_path)
        if not path.exists():
            logger.warning(
                "Zone config %s not found — using placeholder thirds-of-frame "
                "zones. Direction counts will not be meaningful until you run "
                "tools/calibrate_zones.py.",
                self.config_path,
            )
            self.zones = self._placeholder_zones()
            self.calibrated = False
            return

        with open(path) as f:
            raw = json.load(f)
        self.zones = {name: np.array(points, dtype=np.int32) for name, points in raw.items()}
        missing = [n for n in ZONE_NAMES if n not in self.zones]
        if missing:
            logger.warning("Zone config is missing zones: %s", missing)
        self.calibrated = True
        logger.info("Loaded calibrated zones from %s: %s", self.config_path, list(self.zones.keys()))

    @staticmethod
    def _placeholder_zones() -> dict:
        # Rough thirds-of-frame placeholder assuming PROCESSING_WIDTH=960,
        # 4:3-ish source aspect. Purely so the system runs end-to-end
        # before calibration — do not rely on these for real counts.
        return {
            "left": np.array([[0, 300], [320, 300], [320, 720], [0, 720]], dtype=np.int32),
            "straight": np.array([[320, 300], [640, 300], [640, 720], [320, 720]], dtype=np.int32),
            "right": np.array([[640, 300], [960, 300], [960, 720], [640, 720]], dtype=np.int32),
        }

    def classify_point(self, point) -> Optional[str]:
        """Returns the zone name containing `point` (x, y), or None."""
        if point is None:
            return None
        pt = (float(point[0]), float(point[1]))
        for name, polygon in self.zones.items():
            if cv2.pointPolygonTest(polygon, pt, False) >= 0:
                return name
        return None

    def draw_zones(self, frame: np.ndarray, alpha: float = 0.20) -> np.ndarray:
        overlay = frame.copy()
        for name, polygon in self.zones.items():
            color = ZONE_COLORS.get(name, ZONE_COLORS[None])
            cv2.fillPoly(overlay, [polygon], color)
        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, dst=frame)
        for name, polygon in self.zones.items():
            color = ZONE_COLORS.get(name, ZONE_COLORS[None])
            cv2.polylines(frame, [polygon], isClosed=True, color=color, thickness=2)
            label_pos = tuple(polygon.mean(axis=0).astype(int))
            cv2.putText(frame, name.upper(), label_pos, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        if not self.calibrated:
            cv2.putText(
                frame, "UNCALIBRATED ZONES - run tools/calibrate_zones.py",
                (10, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2,
            )
        return frame
