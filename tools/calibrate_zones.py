"""
Interactive zone calibration (standalone, desktop OpenCV window).

Zone coordinates are specific to how your camera is physically mounted —
there's no way to hardcode correct rectangles without seeing your actual
frame. Run this once (and again any time the camera is remounted) to
click out the left/straight/right gate rectangles and save them to
logic/zones_config.json, which ZoneManager loads at runtime.

Each gate is exactly 2 points — opposite corners of its counting
rectangle, matching the in-dashboard /calibrate page's Point A / Point B
convention. A sheep is counted once its track is genuinely seen entering
that rectangle and then exiting it (see logic/zones.py for why this is
an area, not a line, and logic/direction_logic.py for the enter/exit
counting logic). Calibrate the rectangle with real gap from the
physical gate structure, not flush against it.

This is the fallback tool (no Flask server needed) — the in-dashboard
/calibrate page is the one to reach for day-to-day, and uses
"Gate A / Gate B / Gate C" as display names for these same three zones
(A=left, B=straight, C=right) to match how the gates are physically
referred to. This tool sticks with left/straight/right directly since
there's no ambiguity to resolve in a terminal prompt.

Usage:
    python tools/calibrate_zones.py

Controls:
    left click   place Point A, then Point B (opposite corners), for the current gate
    n            confirm current gate's 2 points, move to the next (left -> straight -> right)
    u            undo last point
    r            clear current in-progress gate
    s            save all defined gates and exit
    q            quit without saving
"""
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camera.config import build_rtsp_url  # noqa: E402
from camera.rtsp_stream import LowLatencyRTSPStream  # noqa: E402

GATE_ORDER = ["left", "straight", "right"]
GATE_COLORS = {"left": (61, 163, 232), "straight": (109, 175, 76), "right": (217, 144, 74)}
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "logic" / "zones_config.json"


class Calibrator:
    def __init__(self, frame):
        self.base_frame = frame
        self.gates: dict = {}
        self.current_points: list = []
        self.gate_index = 0

    def mouse_callback(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and len(self.current_points) < 2:
            self.current_points.append([x, y])

    def current_gate_name(self):
        if self.gate_index >= len(GATE_ORDER):
            return None
        return GATE_ORDER[self.gate_index]

    def render(self):
        frame = self.base_frame.copy()
        name = self.current_gate_name()

        header = (
            f"Gate: {name.upper()}  ({len(self.current_points)}/2 points)  |  n=confirm  u=undo  r=reset  s=save  q=quit"
            if name else "All 3 gates marked — press 's' to save, or 'r' then redo the last one"
        )
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 34), (0, 0, 0), -1)
        cv2.putText(frame, header, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        for saved_name, points in self.gates.items():
            color = GATE_COLORS.get(saved_name, (200, 200, 200))
            a, b = tuple(points[0]), tuple(points[1])
            x_min, x_max = sorted((a[0], b[0]))
            y_min, y_max = sorted((a[1], b[1]))
            cv2.rectangle(frame, (x_min, y_min), (x_max, y_max), color, 3)
            for p in (a, b, (x_min, y_max), (x_max, y_min)):
                cv2.circle(frame, p, 6, color, -1)
                cv2.circle(frame, p, 6, (20, 20, 20), 2)
            cv2.putText(frame, saved_name.upper(), (x_min + 6, y_min - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        if name:
            color = GATE_COLORS.get(name, (0, 0, 255))
            for i, p in enumerate(self.current_points):
                cv2.circle(frame, tuple(p), 6, color, -1)
                label = "A" if i == 0 else "B"
                cv2.putText(frame, label, (p[0] + 10, p[1] + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            if len(self.current_points) == 2:
                a, b = self.current_points
                x_min, x_max = sorted((a[0], b[0]))
                y_min, y_max = sorted((a[1], b[1]))
                cv2.rectangle(frame, (x_min, y_min), (x_max, y_max), color, 2)

        return frame

    def commit_current_gate(self) -> bool:
        name = self.current_gate_name()
        if name is None:
            return False
        if len(self.current_points) != 2:
            print(f"Need exactly 2 points for '{name}' before moving on (have {len(self.current_points)}).")
            return False
        self.gates[name] = [list(p) for p in self.current_points]
        self.current_points = []
        self.gate_index += 1
        print(f"Saved gate '{name}': {self.gates[name]}")
        return True


def grab_calibration_frame(timeout_frames: int = 150):
    print(f"Connecting to camera ({build_rtsp_url().split('@')[-1]})...")
    stream = LowLatencyRTSPStream(build_rtsp_url())
    stream.start()

    frame = None
    for _ in range(timeout_frames):
        frame = stream.get_latest_frame()
        if frame is not None:
            break
        cv2.waitKey(50)
    stream.stop()
    return frame


def main():
    frame = grab_calibration_frame()
    if frame is None:
        print(
            "Could not get a frame from the camera. Check RACECOUNT_CAMERA_PASSWORD, "
            "the camera's IP/RTSP path in camera/config.py, and that the PoE link is up."
        )
        return 1

    calib = Calibrator(frame)
    window = "RaceCount Zone Calibration"
    cv2.namedWindow(window)
    cv2.setMouseCallback(window, calib.mouse_callback)

    print("\nClick 2 points per gate (Point A, then Point B — opposite corners of the gate rectangle), in order: left, straight, right.\n")

    while True:
        cv2.imshow(window, calib.render())
        key = cv2.waitKey(30) & 0xFF

        if key == ord("n"):
            calib.commit_current_gate()
        elif key == ord("u") and calib.current_points:
            calib.current_points.pop()
        elif key == ord("r"):
            calib.current_points = []
        elif key == ord("s"):
            if calib.current_gate_name() is not None:
                calib.commit_current_gate()
            if len(calib.gates) < 3:
                print(f"Only {len(calib.gates)}/3 gates defined.")
                confirm = input("Save anyway? (y/n): ").strip().lower()
                if confirm != "y":
                    continue
            OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(OUTPUT_PATH, "w") as f:
                json.dump(calib.gates, f, indent=2)
            print(f"\nSaved {len(calib.gates)} gate(s) to {OUTPUT_PATH}")
            break
        elif key == ord("q"):
            print("Quit without saving.")
            break

    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
