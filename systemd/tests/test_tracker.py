"""
Tests for tracking/tracker.py.

Uses real YOLO inference against ultralytics' own bundled sample image
(ships with the ultralytics package, no network needed) rather than
faking an ultralytics Results object — sv.Detections.from_ultralytics()
expects a real Results structure, and hand-constructing a convincing
fake is more fragile than just running real (fast, ~1s) inference
against a local image. This also means these tests exercise the same
YOLO -> SheepTracker integration path the real pipeline uses.

Run with: pytest tests/test_tracker.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from detection.yolo_engine import YoloEngine
from tracking.tracker import SheepTracker

TEST_IMAGE = "/usr/local/lib/python3.12/dist-packages/ultralytics/assets/zidane.jpg"


def _get_detector_and_frame():
    detector = YoloEngine(model_path="models/yolov8n.pt", target_classes=[0], confidence=0.4)  # person, for real detections
    frame = cv2.imread(TEST_IMAGE)
    return detector, frame


class TestSheepTrackerBasics:
    def test_assigns_and_persists_track_ids_across_frames(self):
        detector, frame = _get_detector_and_frame()
        tracker = SheepTracker(frame_rate=15)

        ids_seen = []
        for _ in range(3):
            result = detector.infer(frame)
            tracked = tracker.update(result)
            ids_seen.append(sorted(int(t) for t in tracked.tracker_id if t is not None))

        assert len(ids_seen[0]) > 0, "expected at least one detection in the test image"
        assert ids_seen[0] == ids_seen[1] == ids_seen[2], "track IDs should stay stable across repeated frames"

    def test_trajectory_accumulates(self):
        detector, frame = _get_detector_and_frame()
        tracker = SheepTracker(frame_rate=15)

        for _ in range(4):
            result = detector.infer(frame)
            tracker.update(result)

        result = detector.infer(frame)
        tracked = tracker.update(result)
        first_id = int(tracked.tracker_id[0])
        traj = tracker.get_trajectory(first_id)
        assert len(traj) == 5


class TestSheepTrackerReset:
    def test_reset_clears_trajectories(self):
        detector, frame = _get_detector_and_frame()
        tracker = SheepTracker(frame_rate=15)

        result = detector.infer(frame)
        tracker.update(result)
        assert len(tracker.trajectories) > 0

        tracker.reset()
        assert len(tracker.trajectories) == 0

    def test_ids_restart_cleanly_after_reset(self):
        """The whole point of resetting between sessions — a fresh
        session shouldn't inherit track IDs (or any internal ByteTrack
        motion-model state) from a session that could have ended hours
        or days earlier."""
        detector, frame = _get_detector_and_frame()
        tracker = SheepTracker(frame_rate=15)

        result1 = detector.infer(frame)
        tracked1 = tracker.update(result1)
        ids_before = sorted(int(t) for t in tracked1.tracker_id if t is not None)

        tracker.reset()

        result2 = detector.infer(frame)
        tracked2 = tracker.update(result2)
        ids_after = sorted(int(t) for t in tracked2.tracker_id if t is not None)

        assert ids_after == ids_before, "IDs should restart from the same starting sequence after a full reset"

    def test_normal_operation_resumes_after_reset(self):
        detector, frame = _get_detector_and_frame()
        tracker = SheepTracker(frame_rate=15)

        result = detector.infer(frame)
        tracker.update(result)
        tracker.reset()

        # tracking across multiple frames post-reset should work exactly
        # as it does on a fresh tracker instance
        ids_seen = []
        for _ in range(3):
            result = detector.infer(frame)
            tracked = tracker.update(result)
            ids_seen.append(sorted(int(t) for t in tracked.tracker_id if t is not None))
        assert ids_seen[0] == ids_seen[1] == ids_seen[2]
