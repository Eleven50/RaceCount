"""
Small persisted settings store — currently just the theme preference and
the PIN gating the Advanced section of the Settings screen. Same atomic-
write pattern as mobs/mob_store.py and mobs/session_record.py (temp file
+ os.replace), for the same reason: a crash mid-write should never leave
this file half-written and unparseable.

Deliberately not folded into mob_store.py's own storage -- this is app-
level configuration, not livestock-tracking data, and the two have no
reason to share a schema or a file.
"""

import json
import os
import threading
from pathlib import Path

DEFAULT_SETTINGS = {
    "theme": "dark",
    # Gates the Settings screen's Advanced section (recalibrate shortcut,
    # update-check status) -- not meant to be strong security, just a
    # deliberate speed bump so it isn't one accidental tap away for
    # whoever's using the kiosk. Change by editing this file directly,
    # or data/settings.json once created (same "edit the file" pattern
    # as the camera password in /etc/racecount.env).
    "advanced_pin": "1969",
}


class SettingsStore:
    def __init__(self, data_dir: str = "data"):
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "settings.json"
        self._lock = threading.RLock()
        if not self._path.exists():
            self._write(DEFAULT_SETTINGS)

    def _write(self, data: dict):
        tmp_path = self._path.with_suffix(".json.tmp")
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, self._path)

    def get_all(self) -> dict:
        with self._lock:
            try:
                with open(self._path) as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = dict(DEFAULT_SETTINGS)
                self._write(data)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            return merged

    def get_theme(self) -> str:
        theme = self.get_all().get("theme", "dark")
        return theme if theme in ("dark", "light") else "dark"

    def set_theme(self, theme: str):
        if theme not in ("dark", "light"):
            raise ValueError(f"Invalid theme: {theme!r}")
        with self._lock:
            data = self.get_all()
            data["theme"] = theme
            self._write(data)

    def check_advanced_pin(self, candidate: str) -> bool:
        return str(candidate) == str(self.get_all().get("advanced_pin", DEFAULT_SETTINGS["advanced_pin"]))
