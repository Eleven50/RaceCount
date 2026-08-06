"""
Persistent mob storage.

A "mob" is a named batch of livestock being drafted, with custom labels
for whichever of the 3 physical gates this particular mob actually uses
— 1, 2, or 3 of them (e.g. "Pokers" / "Works" / "Selling" rather than
generic Left/Straight/Right), and a running count per active gate that
accumulates across however many separate sessions it takes to get
through the whole mob — you might draft half of it today and come back
next week for the rest, and the total needs to still be there.

This is a meaningfully different durability requirement than
counting/counter.py's in-memory DirectionCounter, which is still exactly
what it was: a per-process-run guard against double-counting a track ID
and a live display during one active session. A mob's accumulated total
surviving a week and multiple reboots is the actual point here, so unlike
the earlier "no unnecessary disk writes" tradeoff documented in
counter.py, writing on every confirmed count is the correct choice, not
something to avoid — the thing worth avoiding is corrupting that write,
not making it.

Each mob is one JSON file under data/mobs/, written atomically (temp
file in the same directory, then os.replace(), which POSIX guarantees is
atomic) so a power loss mid-write can never leave a mob's file
half-written — the rename either hasn't happened yet (old complete data
intact) or has completed (new complete data in place), never something
in between.
"""
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("racecount.mobs")

DIRECTIONS = ("left", "straight", "right")
DEFAULT_DATA_DIR = "data/mobs"


@dataclass
class Mob:
    id: str
    name: str
    gate_labels: dict  # {"left": "Pokers", "straight": "Works", "right": "Selling"}
    counts: dict = field(default_factory=lambda: {d: 0 for d in DIRECTIONS})
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Mob":
        return Mob(
            id=data["id"],
            name=data["name"],
            gate_labels=dict(data["gate_labels"]),
            counts=dict(data["counts"]),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
        )


def _validate_gate_labels(gate_labels: dict):
    if not gate_labels:
        raise ValueError("At least one gate is required")
    unknown = [d for d in gate_labels if d not in DIRECTIONS]
    if unknown:
        raise ValueError(f"Unknown gate(s): {unknown}")
    empty = [d for d, label in gate_labels.items() if not str(label).strip()]
    if empty:
        raise ValueError(f"Missing gate label(s) for: {empty}")


class MobStore:
    def __init__(self, data_dir: str = DEFAULT_DATA_DIR):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path_for(self, mob_id: str) -> Path:
        return self.data_dir / f"{mob_id}.json"

    def _atomic_write(self, path: Path, data: dict):
        tmp_path = path.with_name(path.name + ".tmp")
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)  # atomic on POSIX — never a half-written mob file

    def create_mob(self, name: str, gate_labels: dict) -> Mob:
        name = name.strip()
        if not name:
            raise ValueError("Mob name cannot be empty")
        _validate_gate_labels(gate_labels)

        now = time.time()
        mob_id = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{uuid.uuid4().hex[:6]}"
        mob = Mob(
            id=mob_id,
            name=name,
            gate_labels={d: str(v).strip() for d, v in gate_labels.items()},
            counts={d: 0 for d in gate_labels},
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._atomic_write(self._path_for(mob_id), mob.to_dict())
        logger.info("Created mob '%s' (%s) with gates: %s", name, mob_id, list(gate_labels.keys()))
        return mob

    def get_mob(self, mob_id: str) -> Optional[Mob]:
        path = self._path_for(mob_id)
        if not path.exists():
            return None
        try:
            with open(path) as f:
                return Mob.from_dict(json.load(f))
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.error("Failed to load mob %s: %s", mob_id, e)
            return None

    def list_mobs(self) -> list:
        """Most-recently-updated first — matches History screen's
        expected order (most relevant/recent mobs at the top)."""
        mobs = []
        for path in self.data_dir.glob("*.json"):
            if path.name.endswith(".tmp"):
                continue
            try:
                with open(path) as f:
                    mobs.append(Mob.from_dict(json.load(f)))
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.error("Skipping unreadable mob file %s: %s", path, e)
        mobs.sort(key=lambda m: m.updated_at, reverse=True)
        return mobs

    def increment(self, mob_id: str, direction: str, amount: int = 1) -> Optional[Mob]:
        if direction not in DIRECTIONS:
            raise ValueError(f"Unknown direction: {direction}")
        with self._lock:
            mob = self.get_mob(mob_id)
            if mob is None:
                logger.warning("increment() called for unknown mob %s", mob_id)
                return None
            if direction not in mob.counts:
                # A calibrated gate that isn't one of THIS mob's active
                # gates -- a real mismatch between calibration and mob
                # config, not something that should ever crash the
                # pipeline over. Logged, not counted; the recalibration
                # prompt in the UI is the actual fix for this, not a
                # silent partial-increment here.
                logger.warning(
                    "Track passed gate '%s' but mob '%s' doesn't use that gate (active: %s) — not counted",
                    direction, mob_id, list(mob.counts.keys()),
                )
                return mob
            mob.counts[direction] += amount
            mob.updated_at = time.time()
            self._atomic_write(self._path_for(mob_id), mob.to_dict())
        return mob

    def delete_mob(self, mob_id: str) -> bool:
        """Returns True if a mob was actually deleted, False if it
        didn't exist. Only removes the mob's own file — deleting its
        associated session records (if any) is the caller's job, since
        that's a cross-store concern this class deliberately doesn't
        know about (see ui/server.py's DELETE /api/mobs/<id> handler,
        which orchestrates both)."""
        path = self._path_for(mob_id)
        with self._lock:
            if not path.exists():
                return False
            path.unlink()
        logger.info("Deleted mob %s", mob_id)
        return True
