"""
Tests for the pure-logic modules: zone geometry, debounced direction
classification, and the single-count-per-track counter. None of these
need a camera or a model, so they run anywhere, including CI.

Run with: pytest tests/ -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

from logic.zones import ZoneManager
from logic.direction_logic import DirectionClassifier
from counting.counter import DirectionCounter


def make_zone_manager():
    zm = ZoneManager.__new__(ZoneManager)  # bypass file loading
    zm.config_path = "unused"
    zm.calibrated = True
    zm.zones = {
        "left": np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=np.int32),
        "straight": np.array([[20, 0], [30, 0], [30, 10], [20, 10]], dtype=np.int32),
        "right": np.array([[40, 0], [50, 0], [50, 10], [40, 10]], dtype=np.int32),
    }
    return zm


class TestZoneManager:
    def test_point_inside_left(self):
        zm = make_zone_manager()
        assert zm.classify_point((5, 5)) == "left"

    def test_point_inside_straight(self):
        zm = make_zone_manager()
        assert zm.classify_point((25, 5)) == "straight"

    def test_point_outside_all_zones(self):
        zm = make_zone_manager()
        assert zm.classify_point((15, 5)) is None

    def test_none_point(self):
        zm = make_zone_manager()
        assert zm.classify_point(None) is None


class TestDirectionCounter:
    def test_first_pass_counts(self):
        counter = DirectionCounter()
        assert counter.register_pass(1, "left") is True
        assert counter.snapshot()["counts"]["left"] == 1

    def test_same_track_id_not_double_counted(self):
        counter = DirectionCounter()
        counter.register_pass(1, "left")
        result = counter.register_pass(1, "left")
        assert result is False
        assert counter.snapshot()["counts"]["left"] == 1

    def test_same_track_id_cannot_flip_direction(self):
        """A track already counted as 'left' must not also count as
        'right' if a tracking glitch briefly misclassifies it later."""
        counter = DirectionCounter()
        counter.register_pass(1, "left")
        counter.register_pass(1, "right")
        snap = counter.snapshot()["counts"]
        assert snap["left"] == 1
        assert snap["right"] == 0

    def test_different_tracks_count_independently(self):
        counter = DirectionCounter()
        counter.register_pass(1, "left")
        counter.register_pass(2, "right")
        counter.register_pass(3, "straight")
        snap = counter.snapshot()["counts"]
        assert snap == {"left": 1, "straight": 1, "right": 1}

    def test_reset_clears_counts_and_memory(self):
        counter = DirectionCounter()
        counter.register_pass(1, "left")
        counter.reset()
        assert counter.snapshot()["counts"]["left"] == 0
        # track 1 should be countable again after reset
        assert counter.register_pass(1, "left") is True

    def test_unknown_direction_rejected(self):
        counter = DirectionCounter()
        assert counter.register_pass(1, "diagonal") is False


class TestDirectionClassifier:
    def test_confirms_after_enough_hits(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, window_frames=6, confirm_frames=4)

        result = None
        for _ in range(3):
            result = clf.observe(track_id=1, centroid=(5, 5))  # inside 'left'
        assert result is None  # only 3 hits so far, need 4

        result = clf.observe(track_id=1, centroid=(5, 5))  # 4th hit
        assert result == "left"
        assert counter.snapshot()["counts"]["left"] == 1

    def test_brief_occlusion_does_not_reset_confirmation(self):
        """Simulates a handler's arm occluding the sheep for 1-2 frames
        (centroid classifies as None / outside all zones) in the middle
        of an otherwise-consistent 'left' pass."""
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, window_frames=6, confirm_frames=4)

        clf.observe(1, (5, 5))       # left
        clf.observe(1, (5, 5))       # left
        clf.observe(1, (100, 100))   # occluded / outside all zones -> None
        result = clf.observe(1, (5, 5))  # left again
        assert result is None  # 3 'left' hits out of 4 observations so far

        result = clf.observe(1, (5, 5))  # 4th 'left' hit within window
        assert result == "left"

    def test_flicker_between_zones_does_not_falsely_confirm(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, window_frames=6, confirm_frames=4)

        sequence = [(5, 5), (25, 5), (5, 5), (25, 5), (5, 5), (25, 5)]
        results = [clf.observe(1, pt) for pt in sequence]
        assert all(r is None for r in results)  # never 4 consistent hits for either zone

    def test_single_track_only_confirmed_once(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, window_frames=6, confirm_frames=4)

        for _ in range(10):
            clf.observe(1, (5, 5))
        assert counter.snapshot()["counts"]["left"] == 1  # not 7

    def test_invalid_confirm_frames_raises(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        with pytest.raises(ValueError):
            DirectionClassifier(zm, counter, window_frames=3, confirm_frames=5)
