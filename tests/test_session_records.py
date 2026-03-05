"""
Tests for mobs/session_record.py — durable per-session snapshots.

Run with: pytest tests/ -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mobs.session_record import SessionRecordStore

GATE_LABELS = {"left": "Selling", "straight": "Pokers", "right": "Works"}


class TestSessionRecordCreation:
    def test_create_computes_total_and_duration(self, tmp_path):
        store = SessionRecordStore(data_dir=str(tmp_path))
        record = store.create_record(
            mob_id="mob-1", mob_name="Hoggets", gate_labels=GATE_LABELS,
            counts={"left": 2, "straight": 8, "right": 1},
            started_at=1000.0, ended_at=1125.7,
        )
        assert record.total == 11
        assert abs(record.duration_seconds - 125.7) < 0.01

    def test_create_and_get_round_trip(self, tmp_path):
        store = SessionRecordStore(data_dir=str(tmp_path))
        record = store.create_record(
            mob_id="mob-1", mob_name="Hoggets", gate_labels=GATE_LABELS,
            counts={"left": 2, "straight": 8, "right": 1},
            started_at=1000.0, ended_at=1125.7,
        )
        fetched = store.get_record(record.id)
        assert fetched is not None
        assert fetched.mob_name == "Hoggets"
        assert fetched.counts == {"left": 2, "straight": 8, "right": 1}
        assert fetched.gate_labels == GATE_LABELS

    def test_get_unknown_record_returns_none(self, tmp_path):
        store = SessionRecordStore(data_dir=str(tmp_path))
        assert store.get_record("does-not-exist") is None

    def test_negative_duration_clamps_to_zero(self, tmp_path):
        """Defensive: clock skew or bad input shouldn't produce a
        negative duration on screen."""
        store = SessionRecordStore(data_dir=str(tmp_path))
        record = store.create_record(
            mob_id="mob-1", mob_name="X", gate_labels=GATE_LABELS,
            counts={"left": 0, "straight": 0, "right": 0},
            started_at=100.0, ended_at=99.0,
        )
        assert record.duration_seconds == 0.0


class TestSessionRecordPersistence:
    def test_persists_across_fresh_store_instances(self, tmp_path):
        data_dir = str(tmp_path)
        store1 = SessionRecordStore(data_dir=data_dir)
        record = store1.create_record(
            mob_id="mob-1", mob_name="Hoggets", gate_labels=GATE_LABELS,
            counts={"left": 2, "straight": 8, "right": 1},
            started_at=1000.0, ended_at=1125.7,
        )
        del store1

        store2 = SessionRecordStore(data_dir=data_dir)
        fetched = store2.get_record(record.id)
        assert fetched is not None
        assert fetched.counts == {"left": 2, "straight": 8, "right": 1}


class TestSessionRecordListing:
    def test_most_recently_ended_first(self, tmp_path):
        store = SessionRecordStore(data_dir=str(tmp_path))
        store.create_record("mobA", "MobA", GATE_LABELS, {"left": 1, "straight": 0, "right": 0}, 1000, 1010)
        store.create_record("mobB", "MobB", GATE_LABELS, {"left": 2, "straight": 0, "right": 0}, 2000, 2020)
        store.create_record("mobA", "MobA", GATE_LABELS, {"left": 3, "straight": 0, "right": 0}, 3000, 3030)

        records = store.list_records()
        assert [r.ended_at for r in records] == [3030, 2020, 1010]

    def test_filters_by_mob_id(self, tmp_path):
        store = SessionRecordStore(data_dir=str(tmp_path))
        store.create_record("mobA", "MobA", GATE_LABELS, {"left": 1, "straight": 0, "right": 0}, 1000, 1010)
        store.create_record("mobB", "MobB", GATE_LABELS, {"left": 2, "straight": 0, "right": 0}, 2000, 2020)
        store.create_record("mobA", "MobA", GATE_LABELS, {"left": 3, "straight": 0, "right": 0}, 3000, 3030)

        mob_a_records = store.list_records(mob_id="mobA")
        assert len(mob_a_records) == 2
        assert all(r.mob_id == "mobA" for r in mob_a_records)

    def test_corrupt_file_skipped_not_fatal(self, tmp_path):
        store = SessionRecordStore(data_dir=str(tmp_path))
        store.create_record("mobA", "Good", GATE_LABELS, {"left": 1, "straight": 0, "right": 0}, 1000, 1010)
        (tmp_path / "garbage.json").write_text("{not valid json")

        records = store.list_records()
        assert len(records) == 1
        assert records[0].mob_name == "Good"

    def test_empty_store_returns_empty_list(self, tmp_path):
        store = SessionRecordStore(data_dir=str(tmp_path))
        assert store.list_records() == []


class TestCascadeDeletion:
    def test_delete_records_for_mob_removes_only_that_mobs_sessions(self, tmp_path):
        store = SessionRecordStore(data_dir=str(tmp_path))
        store.create_record("mobA", "MobA", GATE_LABELS, {"left": 1, "straight": 0, "right": 0}, 100, 110)
        store.create_record("mobA", "MobA", GATE_LABELS, {"left": 0, "straight": 2, "right": 0}, 200, 220)
        store.create_record("mobB", "MobB", GATE_LABELS, {"left": 0, "straight": 0, "right": 1}, 300, 310)

        deleted_count = store.delete_records_for_mob("mobA")
        assert deleted_count == 2

        remaining = store.list_records()
        assert len(remaining) == 1
        assert remaining[0].mob_id == "mobB"

    def test_delete_records_for_mob_with_no_sessions_returns_zero(self, tmp_path):
        store = SessionRecordStore(data_dir=str(tmp_path))
        assert store.delete_records_for_mob("never-existed") == 0
