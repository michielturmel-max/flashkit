from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import __version__

APP = "flashkit"
VERSION = __version__

ADB = os.getenv("FLASHKIT_ADB", "adb").strip() or "adb"
FASTBOOT = os.getenv("FLASHKIT_FASTBOOT", "fastboot").strip() or "fastboot"
COMMAND_TIMEOUT = float(os.getenv("FLASHKIT_TIMEOUT", "60"))
BOOT_TIMEOUT = float(os.getenv("FLASHKIT_BOOT_TIMEOUT", "300"))
SIDELOAD_TIMEOUT = float(os.getenv("FLASHKIT_SIDELOAD_TIMEOUT", "3600"))
MIN_FREE_GIB = float(os.getenv("FLASHKIT_MIN_FREE_GIB", "2"))

REPO_DIR = Path(__file__).resolve().parents[2]


def default_home() -> Path:
    override = os.getenv("FLASHKIT_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    base = os.getenv("XDG_DATA_HOME", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return root / APP


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def db(self) -> Path:
        return self.home / "flashkit.db"

    @property
    def artifacts(self) -> Path:
        return self.home / "artifacts"

    @property
    def backups(self) -> Path:
        return self.home / "backups"

    @property
    def logs(self) -> Path:
        return self.home / "logs"

    @property
    def profiles(self) -> Path:
        override = os.getenv("FLASHKIT_PROFILES", "").strip()
        return Path(override).expanduser() if override else REPO_DIR / "profiles"

    def ensure(self) -> None:
        for path in (self.home, self.artifacts, self.backups, self.logs):
            path.mkdir(parents=True, exist_ok=True)
