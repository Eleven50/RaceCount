"""
Session records: a durable snapshot of one Start-Session-to-End-Session
run, captured at the moment a session ends.

This is deliberately separate from a mob's own cumulative counts
(mobs/mob_store.py). A mob's counts are mutable and grow forever across
however many sessions it takes to get through it; a session record is
immutable once written — a receipt for one specific sitting: which mob,
what each gate meant that session, how many went through each one this
time (not the mob's running total), and how long it took. The Session
Stats screen reads one of these right after End Session; History will
read the list of them later for the same reason DirectionCounter isn't
what either screen reads from directly — the live in-memory counter
resets on the next session's start, so anything meant to survive past
that moment needs its own durable copy taken before the reset happens.

Same atomic-write pattern as MobStore and for the same reason: a power
loss mid-write must never leave a half-written record on disk.
"""
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("racecount.mobs")

DEFAULT_DATA_DIR = "data/sessions"


@dataclass
class SessionRecord:
    id: str
    mob_id: str
    mob_name: str
    gate_labels: dict  # snapshot at the time of this session, e.g. {"left": "Selling", ...}
    counts: dict  # THIS session's contribution only — not the mob's cumulative total
    started_at: float
    ended_at: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.ended_at - self.started_at)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "SessionRecord":
        return SessionRecord(
            id=data["id"],
            mob_id=data["mob_id"],
            mob_name=data["mob_name"],
            gate_labels=dict(data["gate_labels"]),
            counts=dict(data["counts"]),
            started_at=data["started_at"],
            ended_at=data["ended_at"],
        )


class SessionRecordStore:
    def __init__(self, data_dir: str = DEFAULT_DATA_DIR):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path_for(self, record_id: str) -> Path:
        return self.data_dir / f"{record_id}.json"

    def _atomic_write(self, path: Path, data: dict):
        tmp_path = path.with_name(path.name + ".tmp")
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)

    def create_record(
        self, mob_id: str, mob_name: str, gate_labels: dict, counts: dict, started_at: float, ended_at: float,
    ) -> SessionRecord:
        now = time.time()
        record_id = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{uuid.uuid4().hex[:6]}"
        record = SessionRecord(
            id=record_id,
            mob_id=mob_id,
            mob_name=mob_name,
            gate_labels=dict(gate_labels),
            counts=dict(counts),
            started_at=started_at,
            ended_at=ended_at,
        )
        with self._lock:
            self._atomic_write(self._path_for(record_id), record.to_dict())
        logger.info(
            "Session record created: mob '%s', %s, %.0fs",
            mob_name, record.counts, record.duration_seconds,
        )
        return record

    def get_record(self, record_id: str) -> Optional[SessionRecord]:
        path = self._path_for(record_id)
        if not path.exists():
            return None
        try:
            with open(path) as f:
                return SessionRecord.from_dict(json.load(f))
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.error("Failed to load session record %s: %s", record_id, e)
            return None

    def list_records(self, mob_id: Optional[str] = None) -> list:
        """Most-recent-first. Filter to one mob's sessions with mob_id
        for a future 'drill into this mob's history' view."""
        records = []
        for path in self.data_dir.glob("*.json"):
            if path.name.endswith(".tmp"):
                continue
            try:
                with open(path) as f:
                    record = SessionRecord.from_dict(json.load(f))
                if mob_id is None or record.mob_id == mob_id:
                    records.append(record)
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.error("Skipping unreadable session record %s: %s", path, e)
        records.sort(key=lambda r: r.ended_at, reverse=True)
        return records

    def delete_records_for_mob(self, mob_id: str) -> int:
        """Cascade-delete: called when a mob itself is deleted, so its
        session history doesn't linger around referencing a mob_id that
        no longer resolves to anything. Returns how many were removed."""
        records = self.list_records(mob_id=mob_id)
        with self._lock:
            for record in records:
                path = self._path_for(record.id)
                if path.exists():
                    path.unlink()
        if records:
            logger.info("Deleted %d session record(s) for mob %s", len(records), mob_id)
        return len(records)
