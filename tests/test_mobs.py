"""
Tests for mobs/mob_store.py — persistent, multi-session mob storage.

Run with: pytest tests/ -v
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from mobs.mob_store import MobStore


GATE_LABELS = {"left": "Selling", "straight": "Pokers", "right": "Works"}


class TestMobCreation:
    def test_create_returns_mob_with_zeroed_counts(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        mob = store.create_mob("Hoggets", GATE_LABELS)
        assert mob.name == "Hoggets"
        assert mob.gate_labels == GATE_LABELS
        assert mob.counts == {"left": 0, "straight": 0, "right": 0}
        assert mob.total == 0

    def test_create_and_get_round_trip(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        mob = store.create_mob("Hoggets", GATE_LABELS)
        fetched = store.get_mob(mob.id)
        assert fetched is not None
        assert fetched.name == "Hoggets"
        assert fetched.gate_labels["straight"] == "Pokers"

    def test_empty_name_rejected(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        with pytest.raises(ValueError):
            store.create_mob("", GATE_LABELS)

    def test_whitespace_only_name_rejected(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        with pytest.raises(ValueError):
            store.create_mob("   ", GATE_LABELS)

    def test_missing_gate_label_rejected(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        with pytest.raises(ValueError):
            store.create_mob("Test", {"left": "a", "straight": "b"})  # missing "right"

    def test_empty_gate_label_rejected(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        with pytest.raises(ValueError):
            store.create_mob("Test", {"left": "a", "straight": "", "right": "c"})

    def test_get_unknown_mob_returns_none(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        assert store.get_mob("does-not-exist") is None

    def test_duplicate_names_allowed_with_distinct_ids(self, tmp_path):
        """A farmer might genuinely create multiple 'Hoggets' mobs across
        different weeks — name isn't the identity, id is."""
        store = MobStore(data_dir=str(tmp_path))
        mob1 = store.create_mob("Hoggets", GATE_LABELS)
        mob2 = store.create_mob("Hoggets", GATE_LABELS)
        assert mob1.id != mob2.id


class TestMobDeletion:
    def test_delete_removes_mob(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        mob = store.create_mob("ToDelete", GATE_LABELS)
        assert store.get_mob(mob.id) is not None

        deleted = store.delete_mob(mob.id)
        assert deleted is True
        assert store.get_mob(mob.id) is None

    def test_delete_only_affects_the_named_mob(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        mob1 = store.create_mob("Delete me", GATE_LABELS)
        mob2 = store.create_mob("Keep me", GATE_LABELS)

        store.delete_mob(mob1.id)

        assert store.get_mob(mob1.id) is None
        assert store.get_mob(mob2.id) is not None
        assert len(store.list_mobs()) == 1

    def test_delete_nonexistent_mob_returns_false(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        assert store.delete_mob("never-existed") is False


class TestIncrement:
    def test_increment_accumulates(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        mob = store.create_mob("Test", GATE_LABELS)
        store.increment(mob.id, "left", amount=3)
        store.increment(mob.id, "left", amount=2)
        store.increment(mob.id, "straight")
        updated = store.get_mob(mob.id)
        assert updated.counts == {"left": 5, "straight": 1, "right": 0}
        assert updated.total == 6

    def test_increment_unknown_direction_raises(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        mob = store.create_mob("Test", GATE_LABELS)
        with pytest.raises(ValueError):
            store.increment(mob.id, "diagonal")

    def test_increment_unknown_mob_returns_none_not_exception(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        assert store.increment("nonexistent", "left") is None

    def test_increment_updates_timestamp(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        mob = store.create_mob("Test", GATE_LABELS)
        original_updated = mob.updated_at
        result = store.increment(mob.id, "left")
        assert result.updated_at >= original_updated


class TestMultiSessionAccumulation:
    """The core requirement: a mob's counts must survive across separate
    MobStore instances (i.e. separate process runs — the app restarting,
    the Pi rebooting, picking the mob back up a week later via 'Use
    Previous'), accumulating rather than resetting."""

    def test_counts_persist_across_fresh_store_instances(self, tmp_path):
        data_dir = str(tmp_path)

        session1 = MobStore(data_dir=data_dir)
        mob = session1.create_mob("2 tooths", GATE_LABELS)
        session1.increment(mob.id, "straight", amount=15)
        session1.increment(mob.id, "left", amount=4)
        del session1  # simulates the process exiting

        session2 = MobStore(data_dir=data_dir)  # simulates a fresh process start
        continued = session2.get_mob(mob.id)
        assert continued.counts == {"left": 4, "straight": 15, "right": 0}

        session2.increment(mob.id, "right", amount=9)
        session2.increment(mob.id, "straight", amount=10)

        session3 = MobStore(data_dir=data_dir)  # a third "session", for good measure
        final = session3.get_mob(mob.id)
        assert final.counts == {"left": 4, "straight": 25, "right": 9}
        assert final.total == 38


class TestListMobs:
    def test_most_recently_updated_first(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        m1 = store.create_mob("First", GATE_LABELS)
        time.sleep(0.01)
        store.create_mob("Second", GATE_LABELS)
        time.sleep(0.01)
        store.create_mob("Third", GATE_LABELS)

        names = [m.name for m in store.list_mobs()]
        assert names == ["Third", "Second", "First"]

        store.increment(m1.id, "left")  # touching an older mob bumps it to the front
        names_after = [m.name for m in store.list_mobs()]
        assert names_after[0] == "First"

    def test_corrupt_file_skipped_not_fatal(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        store.create_mob("Good", GATE_LABELS)
        (tmp_path / "garbage.json").write_text("{not valid json")

        mobs = store.list_mobs()
        assert len(mobs) == 1
        assert mobs[0].name == "Good"

    def test_tmp_files_excluded_from_listing(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        store.create_mob("Good", GATE_LABELS)
        (tmp_path / "stray.json.tmp").touch()

        mobs = store.list_mobs()
        assert len(mobs) == 1

    def test_empty_store_returns_empty_list(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        assert store.list_mobs() == []


class TestCrashSafety:
    def test_interrupted_write_does_not_corrupt_existing_file(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        mob = store.create_mob("CrashTest", GATE_LABELS)
        store.increment(mob.id, "left", amount=7)

        # Simulate a crash mid-write: a stray, invalid .tmp file exists,
        # but os.replace() was never reached, so the real .json is
        # untouched — this is exactly what a real power-loss leaves
        # behind, since only the atomic rename ever touches the real path.
        tmp_file = store._path_for(mob.id).with_name(store._path_for(mob.id).name + ".tmp")
        tmp_file.write_text('{"incomplete": tru')

        loaded = store.get_mob(mob.id)
        assert loaded.counts["left"] == 7
        assert loaded.name == "CrashTest"

    def test_normal_operation_resumes_after_simulated_crash(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        mob = store.create_mob("CrashTest", GATE_LABELS)
        store.increment(mob.id, "left", amount=7)

        tmp_file = store._path_for(mob.id).with_name(store._path_for(mob.id).name + ".tmp")
        tmp_file.write_text("garbage")

        result = store.increment(mob.id, "left", amount=3)
        assert result.counts["left"] == 10


class TestConcurrency:
    def test_no_lost_updates_under_concurrent_increments(self, tmp_path):
        store = MobStore(data_dir=str(tmp_path))
        mob = store.create_mob("ConcurrencyTest", GATE_LABELS)

        n_threads = 8
        n_per_thread = 50

        def worker():
            for _ in range(n_per_thread):
                store.increment(mob.id, "straight")

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = store.get_mob(mob.id)
        assert final.counts["straight"] == n_threads * n_per_thread
