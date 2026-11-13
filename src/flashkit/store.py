from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at INTEGER NOT NULL,
    kind TEXT NOT NULL,
    device TEXT NOT NULL DEFAULT '',
    serial TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 1,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at INTEGER NOT NULL,
    device TEXT NOT NULL,
    serial TEXT NOT NULL DEFAULT '',
    partition TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS flashes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at INTEGER NOT NULL,
    device TEXT NOT NULL,
    serial TEXT NOT NULL DEFAULT '',
    profile TEXT NOT NULL,
    outcome TEXT NOT NULL,
    steps INTEGER NOT NULL DEFAULT 0,
    log_path TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at INTEGER NOT NULL,
    device TEXT NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL DEFAULT '',
    UNIQUE(device, filename)
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, timeout=15.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def record_event(
        self,
        kind: str,
        message: str,
        device: str = "",
        serial: str = "",
        ok: bool = True,
        detail: str = "",
    ) -> None:
        self.db.execute(
            "INSERT INTO events (at, kind, device, serial, message, ok, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(time.time()), kind, device, serial, message, 1 if ok else 0, detail),
        )
        self.db.commit()

    def record_backup(
        self,
        device: str,
        partition: str,
        path: Path,
        sha256: str,
        size: int,
        status: str,
        serial: str = "",
        meta: dict[str, Any] | None = None,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO backups (at, device, serial, partition, path, sha256, size, status, meta)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(time.time()),
                device,
                serial,
                partition,
                str(path),
                sha256,
                size,
                status,
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        self.db.commit()
        return int(cur.lastrowid or 0)

    def record_flash(
        self,
        device: str,
        profile: str,
        outcome: str,
        steps: int,
        log_path: Path | str = "",
        serial: str = "",
        detail: str = "",
    ) -> None:
        self.db.execute(
            "INSERT INTO flashes (at, device, serial, profile, outcome, steps, log_path, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(time.time()), device, serial, profile, outcome, steps, str(log_path), detail),
        )
        self.db.commit()

    def record_artifact(
        self,
        device: str,
        filename: str,
        sha256: str,
        size: int,
        url: str = "",
    ) -> None:
        self.db.execute(
            "INSERT INTO artifacts (at, device, filename, sha256, size, url) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(device, filename) DO UPDATE SET"
            " at = excluded.at, sha256 = excluded.sha256, size = excluded.size, url = excluded.url",
            (int(time.time()), device, filename, sha256, size, url),
        )
        self.db.commit()

    def backup_ok(self, device: str, partition: str) -> sqlite3.Row | None:
        row = self.db.execute(
            "SELECT * FROM backups WHERE device = ? AND partition = ? AND status = 'ok'"
            " ORDER BY at DESC, id DESC LIMIT 1",
            (device, partition),
        ).fetchone()
        if row is None:
            return None
        path = Path(row["path"])
        if not path.is_file():
            return None
        if path.stat().st_size != row["size"]:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(4 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != row["sha256"]:
            return None
        return row

    def backups(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(
            self.db.execute(
                "SELECT * FROM backups ORDER BY at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        )

    def history(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(
            self.db.execute(
                "SELECT * FROM events ORDER BY at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        )

    def flashes(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(
            self.db.execute(
                "SELECT * FROM flashes ORDER BY at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        )
