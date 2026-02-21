"""
Tests for the pure-logic modules: zone geometry (line-crossing), gate
calibration, direction classification with jitter confirmation, and the
single-count-per-track counter. None of these need a camera or a model,
so they run anywhere, including CI.

Run with: pytest tests/ -v
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from logic.zones import ZoneManager, _segments_intersect, _orientation
from logic.direction_logic import DirectionClassifier
from counting.counter import DirectionCounter


def make_zone_manager():
    """3 lines matching the real gate layout: Gate A (left) and Gate C
    (right) are vertical, Gate B (straight) is horizontal — deliberately
    mixed orientations since that's what the actual gate diagram has."""
    zm = ZoneManager.__new__(ZoneManager)  # bypass file loading
    zm.config_path = "unused"
    zm.calibrated = True
    zm._lock = threading.Lock()
    zm.zones = {
        "left": ((100, 0), (100, 200)),
        "straight": ((300, 100), (500, 100)),
        "right": ((700, 0), (700, 200)),
    }
    return zm


class TestSegmentGeometry:
    def test_perpendicular_crossing_detected(self):
        assert _segments_intersect((50, 100), (150, 100), (100, 0), (100, 200)) is True

    def test_movement_short_of_line_not_detected(self):
        assert _segments_intersect((50, 100), (90, 100), (100, 0), (100, 200)) is False

    def test_infinite_line_crossed_but_finite_segment_missed(self):
        """Crossing x=100 far outside the gate's actual y-extent (0-200)
        must NOT register — this is the difference between a proper
        segment intersection test and a naive infinite-line side test."""
        assert _segments_intersect((50, 500), (150, 500), (100, 0), (100, 200)) is False

    def test_crossing_direction_agnostic(self):
        assert _segments_intersect((150, 100), (50, 100), (100, 0), (100, 200)) is True

    def test_horizontal_gate_crossed_by_vertical_movement(self):
        """Matches Gate B's real orientation."""
        assert _segments_intersect((500, 0), (500, 100), (400, 50), (600, 50)) is True

    def test_parallel_segments_never_intersect(self):
        assert _segments_intersect((0, 0), (0, 100), (50, 0), (50, 100)) is False


class TestZoneManagerCrossing:
    def test_crossing_left_gate_detected(self):
        zm = make_zone_manager()
        assert zm.crossed_gate((80, 100), (120, 100)) == "left"

    def test_crossing_straight_gate_detected(self):
        zm = make_zone_manager()
        assert zm.crossed_gate((400, 80), (400, 120)) == "straight"

    def test_crossing_right_gate_detected(self):
        zm = make_zone_manager()
        assert zm.crossed_gate((680, 100), (720, 100)) == "right"

    def test_unrelated_movement_not_detected(self):
        zm = make_zone_manager()
        assert zm.crossed_gate((400, 400), (410, 410)) is None

    def test_none_points_handled(self):
        zm = make_zone_manager()
        assert zm.crossed_gate(None, (1, 1)) is None
        assert zm.crossed_gate((1, 1), None) is None

    def test_side_of_line_consistent_and_opposite(self):
        zm = make_zone_manager()
        left_side = zm.side_of_line("left", (50, 100))
        right_side = zm.side_of_line("left", (150, 100))
        assert left_side != right_side
        assert zm.side_of_line("left", (60, 150)) == left_side

    def test_side_of_line_none_cases(self):
        zm = make_zone_manager()
        assert zm.side_of_line("left", None) is None
        assert zm.side_of_line("nonexistent", (1, 1)) is None


class TestGateCalibration:
    def test_update_from_gate_points_applies_live(self, tmp_path):
        zm = ZoneManager(config_path=str(tmp_path / "zones.json"))
        gate_points = {
            "left": [[100, 50], [100, 250]],
            "straight": [[300, 50], [500, 50]],
            "right": [[700, 50], [700, 250]],
        }
        zm.update_from_gate_points(gate_points)

        assert zm.crossed_gate((80, 150), (120, 150)) == "left"
        assert zm.crossed_gate((400, 30), (400, 70)) == "straight"
        assert zm.crossed_gate((680, 150), (720, 150)) == "right"
        assert zm.calibrated is True

    def test_persists_and_reloads_in_fresh_instance(self, tmp_path):
        config_path = str(tmp_path / "zones.json")
        gate_points = {
            "left": [[100, 50], [100, 250]],
            "straight": [[300, 50], [500, 50]],
            "right": [[700, 50], [700, 250]],
        }
        ZoneManager(config_path=config_path).update_from_gate_points(gate_points)

        fresh = ZoneManager(config_path=config_path)
        assert fresh.calibrated is True
        assert fresh.crossed_gate((80, 150), (120, 150)) == "left"
        assert fresh.get_last_gate_points() == gate_points

    def test_rejects_too_close_points(self, tmp_path):
        zm = ZoneManager(config_path=str(tmp_path / "zones.json"))
        with pytest.raises(ValueError):
            zm.update_from_gate_points({
                "left": [[10, 10], [10, 11]],
                "straight": [[1, 1], [2, 2]],
                "right": [[3, 3], [4, 4]],
            })

    def test_rejects_missing_gate(self, tmp_path):
        zm = ZoneManager(config_path=str(tmp_path / "zones.json"))
        with pytest.raises(ValueError):
            zm.update_from_gate_points({"left": [[1, 1], [2, 2]], "straight": [[1, 1], [2, 2]]})

    def test_rejects_wrong_point_count(self, tmp_path):
        zm = ZoneManager(config_path=str(tmp_path / "zones.json"))
        with pytest.raises(ValueError):
            zm.update_from_gate_points({
                "left": [[1, 1], [2, 2], [3, 3]],
                "straight": [[1, 1], [2, 2]],
                "right": [[1, 1], [2, 2]],
            })


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
        assert counter.register_pass(1, "left") is True

    def test_unknown_direction_rejected(self):
        counter = DirectionCounter()
        assert counter.register_pass(1, "diagonal") is False


class TestDirectionClassifier:
    def test_crossing_confirmed_on_next_matching_observation(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, confirm_observations=2)

        assert clf.observe(1, (80, 100)) is None   # first sighting, nothing to compare against yet
        assert clf.observe(1, (120, 100)) is None  # crosses 'left' -> pending, not yet confirmed
        result = clf.observe(1, (125, 100))        # still on the far side -> confirmed
        assert result == "left"
        assert counter.snapshot()["counts"]["left"] == 1

    def test_crossing_cancelled_if_it_immediately_reverses(self):
        """Jitter case: crosses the line, then the very next observation
        is back on the near side -- should NOT count."""
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, confirm_observations=2)

        clf.observe(1, (80, 100))
        clf.observe(1, (120, 100))   # crosses -> pending
        result = clf.observe(1, (90, 100))  # back on the near side -> cancelled
        assert result is None
        assert counter.snapshot()["counts"]["left"] == 0

    def test_gap_in_detection_does_not_cancel_pending_confirmation(self):
        """Simulates a handler's arm occluding the sheep for a frame
        right after it crosses the gate line — the track simply isn't
        observed that frame (observe() isn't called), which must not
        cancel the pending confirmation, only delay it."""
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, confirm_observations=2)

        clf.observe(1, (80, 100))
        clf.observe(1, (120, 100))  # crosses -> pending
        # occlusion: track not detected for a frame, so observe() simply
        # isn't called for track 1 at all this frame (nothing to assert)
        result = clf.observe(1, (130, 100))  # reappears, still far side
        assert result == "left"

    def test_confirm_observations_one_counts_immediately(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, confirm_observations=1)

        clf.observe(1, (80, 100))
        result = clf.observe(1, (120, 100))  # crosses -> counted immediately, no confirmation wait
        assert result == "left"
        assert counter.snapshot()["counts"]["left"] == 1

    def test_single_track_only_confirmed_once(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, confirm_observations=2)

        clf.observe(1, (80, 100))
        clf.observe(1, (120, 100))
        clf.observe(1, (125, 100))  # confirmed here
        # keep feeding more observations on the far side — must not re-count
        for x in (130, 135, 140):
            clf.observe(1, (x, 100))
        assert counter.snapshot()["counts"]["left"] == 1

    def test_no_crossing_never_confirms(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, confirm_observations=2)

        for pt in [(200, 100), (210, 100), (220, 100), (230, 100)]:
            assert clf.observe(1, pt) is None
        assert sum(counter.snapshot()["counts"].values()) == 0

    def test_forget_clears_state(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, confirm_observations=2)

        clf.observe(1, (80, 100))
        clf.observe(1, (120, 100))  # pending crossing
        assert clf.get_track_status(1) == "left"
        clf.forget(1)
        assert clf.get_track_status(1) is None
        # after forgetting, a fresh "first sighting" has nothing to compare against
        assert clf.observe(1, (125, 100)) is None

    def test_reconfirmation_of_already_counted_track_returns_none(self):
        """Regression test for a real bug: a track can legitimately
        re-trigger a full pending->confirm cycle for a gate it already
        crossed (e.g. jittering back across the line and re-crossing),
        since crossed_gate() has no memory of _confirmed state. The
        counter correctly refuses to double-count internally either way
        — but observe() itself must also return None the second time,
        not the gate name, or any caller reacting to a non-None return
        (like crediting a mob's persisted total) would double-count even
        though DirectionCounter didn't. Found via an organic end-to-end
        pipeline run where a mob's persisted count came out higher than
        the live session counter for the same session.
        """
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, confirm_observations=2)

        sequence = [(150, 100), (50, 100), (150, 100), (50, 100), (30, 100), (150, 100), (160, 100)]
        results = [clf.observe(1, pt) for pt in sequence]

        assert results[4] == "left"  # genuine first confirmation
        assert results[6] is None  # re-confirmation of the same already-counted track
        assert counter.snapshot()["counts"]["left"] == 1

    def test_reconfirmation_with_immediate_counting_also_returns_none(self):
        """Same bug, same fix, in the confirm_observations<=1 (immediate
        counting) code path specifically, which has its own separate
        counted-gating check."""
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, confirm_observations=1)

        result1 = clf.observe(1, (80, 100))
        result2 = clf.observe(1, (120, 100))  # crosses -> counted immediately
        assert result2 == "left"
        assert counter.snapshot()["counts"]["left"] == 1

        result3 = clf.observe(1, (80, 100))  # crosses back
        result4 = clf.observe(1, (120, 100))  # crosses again -- same track, already counted
        assert result4 is None
        assert counter.snapshot()["counts"]["left"] == 1

    def test_confirmed_gate_queryable_after_confirmation(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, confirm_observations=2)

        assert clf.get_confirmed_gate(1) is None
        clf.observe(1, (80, 100))
        clf.observe(1, (120, 100))
        assert clf.get_confirmed_gate(1) is None  # pending, not yet confirmed
        clf.observe(1, (125, 100))  # confirmed here
        assert clf.get_confirmed_gate(1) == "left"

    def test_reset_clears_all_state(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        clf = DirectionClassifier(zm, counter, confirm_observations=2)

        clf.observe(1, (80, 100))
        clf.observe(1, (120, 100))  # pending crossing for track 1
        assert clf.active_track_count() == 1

        clf.reset()
        assert clf.active_track_count() == 0
        assert clf.get_track_status(1) is None
        assert clf.get_confirmed_gate(1) is None

        # normal operation resumes correctly after reset
        clf.observe(1, (80, 100))
        result = clf.observe(1, (120, 100))
        result2 = clf.observe(1, (125, 100))
        assert result2 == "left"

    def test_invalid_confirm_observations_raises(self):
        zm = make_zone_manager()
        counter = DirectionCounter()
        with pytest.raises(ValueError):
            DirectionClassifier(zm, counter, confirm_observations=0)
