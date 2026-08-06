"""
Tests for the pure-logic modules: zone geometry (rectangles, not lines),
gate calibration, enter-then-exit direction classification, and the
single-count-per-track counter. None of these need a camera or a model,
so they run anywhere, including CI.

Run with: pytest tests/ -v
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from logic.zones import ZoneManager, _segments_intersect, _orientation, _normalize_rect, _point_in_rect
from logic.direction_logic import DirectionClassifier
from counting.counter import DirectionCounter


def make_zone_manager():
    """3 rectangles matching the real gate layout, each with real width
    and height (calibrated with gap from the physical gate structure,
    not flush against it — see logic/zones.py's module docstring)."""
    zm = ZoneManager.__new__(ZoneManager)  # bypass file loading
    zm.config_path = "unused"
    zm.calibrated = True
    zm._lock = threading.Lock()
    zm.zones = {
        "left": ((80, 0), (140, 200)),
        "straight": ((300, 60), (500, 140)),
        "right": ((660, 0), (720, 200)),
    }
    return zm


class TestSegmentGeometry:
    """_segments_intersect/_orientation are unchanged from the line-
    crossing era — still correct, still used internally, just applied
    to rectangle edges now instead of directly to gate lines."""

    def test_perpendicular_crossing_detected(self):
        assert _segments_intersect((50, 100), (150, 100), (100, 0), (100, 200)) is True

    def test_movement_short_of_line_not_detected(self):
        assert _segments_intersect((50, 100), (90, 100), (100, 0), (100, 200)) is False

    def test_infinite_line_crossed_but_finite_segment_missed(self):
        assert _segments_intersect((50, 500), (150, 500), (100, 0), (100, 200)) is False

    def test_crossing_direction_agnostic(self):
        assert _segments_intersect((150, 100), (50, 100), (100, 0), (100, 200)) is True

    def test_parallel_segments_never_intersect(self):
        assert _segments_intersect((0, 0), (0, 100), (50, 0), (50, 100)) is False


class TestRectGeometry:
    def test_normalize_rect_already_ordered(self):
        assert _normalize_rect((100, 100), (200, 200)) == (100, 100, 200, 200)

    def test_normalize_rect_reversed_corners(self):
        assert _normalize_rect((200, 50), (100, 200)) == (100, 50, 200, 200)

    def test_point_in_rect_boundary_inclusive(self):
        rect = (100, 100, 200, 200)
        assert _point_in_rect((150, 150), rect) is True
        assert _point_in_rect((100, 100), rect) is True
        assert _point_in_rect((200, 200), rect) is True
        assert _point_in_rect((50, 150), rect) is False


class TestZoneManagerContainment:
    def test_contains_true_inside_gate(self):
        zm = make_zone_manager()
        assert zm.contains("left", (110, 100)) is True

    def test_contains_false_outside_gate(self):
        zm = make_zone_manager()
        assert zm.contains("left", (400, 100)) is False

    def test_contains_none_for_none_point(self):
        zm = make_zone_manager()
        assert zm.contains("left", None) is None

    def test_contains_none_for_unknown_gate(self):
        zm = make_zone_manager()
        assert zm.contains("nonexistent", (1, 1)) is None

    def test_path_skipped_through_detects_fast_movement(self):
        """The safety net: neither endpoint lands inside the rectangle,
        but the straight-line path between them genuinely passes
        through it -- matters at low FPS or fast animal movement."""
        zm = make_zone_manager()
        # left gate rect is x:[80,140] y:[0,200] -- this segment starts
        # well left of it and ends well right of it, passing straight through
        assert zm.path_skipped_through("left", (20, 100), (200, 100)) is True

    def test_path_skipped_through_false_when_path_misses(self):
        zm = make_zone_manager()
        assert zm.path_skipped_through("left", (20, 300), (200, 300)) is False

    def test_path_skipped_through_none_cases(self):
        zm = make_zone_manager()
        assert zm.path_skipped_through("left", None, (1, 1)) is None
        assert zm.path_skipped_through("left", (1, 1), None) is None
        assert zm.path_skipped_through("nonexistent", (1, 1), (2, 2)) is None


class TestGateCalibration:
    def test_update_from_gate_points_applies_live(self, tmp_path):
        zm = ZoneManager(config_path=str(tmp_path / "zones.json"))
        gate_points = {
            "left": [[80, 0], [140, 200]],
            "straight": [[300, 60], [500, 140]],
            "right": [[660, 0], [720, 200]],
        }
        zm.update_from_gate_points(gate_points)

        assert zm.contains("left", (110, 100)) is True
        assert zm.contains("straight", (400, 100)) is True
        assert zm.contains("right", (690, 100)) is True
        assert zm.calibrated is True

    def test_persists_and_reloads_in_fresh_instance(self, tmp_path):
        config_path = str(tmp_path / "zones.json")
        gate_points = {
            "left": [[80, 0], [140, 200]],
            "straight": [[300, 60], [500, 140]],
            "right": [[660, 0], [720, 200]],
        }
        ZoneManager(config_path=config_path).update_from_gate_points(gate_points)

        fresh = ZoneManager(config_path=config_path)
        assert fresh.calibrated is True
        assert fresh.contains("left", (110, 100)) is True
        assert fresh.get_last_gate_points() == gate_points

    def test_rejects_rectangle_too_thin_horizontally(self, tmp_path):
        zm = ZoneManager(config_path=str(tmp_path / "zones.json"))
        with pytest.raises(ValueError):
            zm.update_from_gate_points({
                "left": [[10, 10], [12, 200]],  # only 2px wide
                "straight": [[300, 60], [500, 140]],
                "right": [[660, 0], [720, 200]],
            })

    def test_rejects_rectangle_too_thin_vertically(self, tmp_path):
        zm = ZoneManager(config_path=str(tmp_path / "zones.json"))
        with pytest.raises(ValueError):
            zm.update_from_gate_points({
                "left": [[10, 10], [200, 12]],  # only 2px tall
                "straight": [[300, 60], [500, 140]],
                "right": [[660, 0], [720, 200]],
            })

    def test_accepts_partial_gate_set(self, tmp_path):
        """1 or 2 gates is now valid, not an error -- this is the whole
        point of supporting variable gate counts."""
        zm = ZoneManager(config_path=str(tmp_path / "zones.json"))
        zm.update_from_gate_points({"left": [[80, 0], [140, 200]]})
        assert zm.calibrated is True
        assert list(zm.zones.keys()) == ["left"]
        assert zm.contains("left", (110, 100)) is True

    def test_calibrating_fewer_gates_replaces_the_full_set(self, tmp_path):
        """Calibrating just 'left' after previously having all 3
        replaces the calibrated set entirely -- it doesn't merge with
        what was there before."""
        zm = ZoneManager(config_path=str(tmp_path / "zones.json"))
        zm.update_from_gate_points({
            "left": [[80, 0], [140, 200]],
            "straight": [[300, 60], [500, 140]],
            "right": [[660, 0], [720, 200]],
        })
        assert len(zm.zones) == 3
        zm.update_from_gate_points({"left": [[80, 0], [140, 200]]})
        assert list(zm.zones.keys()) == ["left"]

    def test_rejects_empty_gate_set(self, tmp_path):
        zm = ZoneManager(config_path=str(tmp_path / "zones.json"))
        with pytest.raises(ValueError):
            zm.update_from_gate_points({})

    def test_rejects_unknown_gate_name(self, tmp_path):
        zm = ZoneManager(config_path=str(tmp_path / "zones.json"))
        with pytest.raises(ValueError):
            zm.update_from_gate_points({"diagonal": [[1, 1], [50, 50]]})

    def test_rejects_wrong_point_count(self, tmp_path):
        zm = ZoneManager(config_path=str(tmp_path / "zones.json"))
        with pytest.raises(ValueError):
            zm.update_from_gate_points({
                "left": [[1, 1], [50, 50], [99, 99]],
                "straight": [[1, 1], [50, 50]],
                "right": [[1, 1], [50, 50]],
            })


class TestDirectionCounter:
    """Unchanged from before -- this module wasn't touched by the
    rectangle rework, and its "count once per track_id+gate, ever" rule
    is exactly what makes direction_logic.py safe to keep simple."""

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
        assert counter.register_pass(1, "left") is True

    def test_unknown_direction_rejected(self):
        counter = DirectionCounter()
        assert counter.register_pass(1, "diagonal") is False


class TestDirectionClassifier:
    def test_enter_then_exit_counts(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter)

        assert clf.observe(1, (20, 100)) is None   # outside, first sighting
        assert clf.observe(1, (110, 100)) is None  # enters the rectangle -- not counted yet
        result = clf.observe(1, (200, 100))        # exits -- counted now
        assert result == "left"
        assert counter.snapshot()["counts"]["left"] == 1

    def test_enter_without_exit_not_counted(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter)

        clf.observe(1, (20, 100))
        result = clf.observe(1, (110, 100))  # enters, stays there
        assert result is None
        assert counter.snapshot()["counts"]["left"] == 0

    def test_lingering_inside_across_multiple_frames_not_a_premature_exit(self):
        """Regression test for a real bug found while building this:
        exit detection was reusing the same 'either endpoint inside OR
        segment crosses an edge' check used for entry detection. Since
        the *previous* point stays inside for as long as the track
        lingers in the rectangle, that check kept reporting 'still
        crossing' on every subsequent frame too -- meaning a track that
        entered and then sat still for a couple of frames before
        genuinely leaving would still register correctly, but a track
        that entered and immediately checked "am I still inside" one
        frame later could misfire. This test specifically exercises
        several consecutive frames genuinely inside before the real
        exit, to make sure lingering doesn't confuse entry with exit.
        """
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter)

        path = [(20, 100), (110, 100), (115, 100), (120, 100), (200, 100)]
        results = [clf.observe(1, p) for p in path]
        assert results == [None, None, None, None, "left"]
        assert counter.snapshot()["counts"]["left"] == 1

    def test_never_enters_never_counts(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter)

        for pt in [(400, 400), (410, 410), (420, 420)]:
            assert clf.observe(1, pt) is None
        assert sum(counter.snapshot()["counts"].values()) == 0

    def test_fast_movement_skip_through_still_counts(self):
        """The path-crossing safety net: a track that moves far enough
        in one frame-to-frame step to never land inside the rectangle
        on any single observed frame, but whose path genuinely passed
        through it, still counts -- exactly the low-FPS robustness this
        module's earlier line-crossing version had, carried over to the
        rectangle model rather than lost when switching to it."""
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter)

        clf.observe(1, (20, 100))
        result = clf.observe(1, (200, 100))  # single huge jump straight through
        assert result == "left"
        assert counter.snapshot()["counts"]["left"] == 1

    def test_none_centroid_does_not_crash(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter)
        assert clf.observe(1, None) is None

    def test_re_entry_after_already_counted_does_not_double_count(self):
        """A track jittering/wandering back into an already-passed
        gate's rectangle and back out again must not count a second
        time -- counter.register_pass()'s per-track_id+gate memory is
        the actual protection here, and this confirms it holds for the
        rectangle model the same way it did for line-crossing."""
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter)

        path = [(20, 100), (110, 100), (200, 100), (110, 100), (20, 100)]
        results = [clf.observe(1, p) for p in path]
        assert counter.snapshot()["counts"]["left"] == 1
        assert results.count("left") == 1

    def test_two_independent_tracks_both_count(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter)

        clf.observe(1, (20, 100)); clf.observe(2, (20, 100))
        clf.observe(1, (110, 100)); clf.observe(2, (110, 100))
        r1 = clf.observe(1, (200, 100))
        r2 = clf.observe(2, (200, 100))
        assert r1 == "left" and r2 == "left"
        assert counter.snapshot()["counts"]["left"] == 2

    def test_forget_clears_state(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter)

        clf.observe(1, (20, 100))
        clf.observe(1, (110, 100))  # entered, in progress
        assert clf.get_track_status(1) == "left"
        clf.forget(1)
        assert clf.get_track_status(1) is None
        # after forgetting, a fresh "first sighting" has nothing to compare against
        assert clf.observe(1, (200, 100)) is None

    def test_confirmed_gate_queryable_after_exit(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter)

        assert clf.get_confirmed_gate(1) is None
        clf.observe(1, (20, 100))
        clf.observe(1, (110, 100))
        assert clf.get_confirmed_gate(1) is None  # entered, not yet exited/confirmed
        clf.observe(1, (200, 100))  # exits -- confirmed here
        assert clf.get_confirmed_gate(1) == "left"

    def test_reset_clears_all_state(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter)

        clf.observe(1, (20, 100))
        clf.observe(1, (110, 100))  # entered, in progress
        assert clf.active_track_count() == 1

        clf.reset()
        assert clf.active_track_count() == 0
        assert clf.get_track_status(1) is None
        assert clf.get_confirmed_gate(1) is None

        # normal operation resumes correctly after reset
        clf.observe(1, (20, 100))
        clf.observe(1, (110, 100))
        result = clf.observe(1, (200, 100))
        assert result == "left"
