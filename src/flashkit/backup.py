from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .artifacts import human_size, sha256_file
from .devices import Device
from .profiles import Profile
from .runner import Runner
from .store import Store


class BackupError(Exception):
    pass


@dataclass(frozen=True)
class BackupResult:
    partition: str
    block: str
    path: Path
    sha256: str
    size: int
    confirmed: bool


def resolve_block(available: set[str], partition: str, slot: str) -> str | None:
    candidates = [partition]
    if slot:
        candidates = [f"{partition}_{slot}", partition]
    if slot:
        candidates += [f"{partition}_a", f"{partition}_b"]
    for candidate in candidates:
        if candidate in available:
            return candidate
    return None


def missing_backups(profile: Profile, store: Store) -> list[str]:
    if not profile.backup.required:
        return []
    missing: list[str] = []
    for partition in profile.backup.partitions:
        if store.backup_ok(profile.codename, partition) is None:
            missing.append(partition)
    return missing


SAFE_BLOCK = re.compile(r"^[A-Za-z0-9_.-]+$")


def _available_blocks(runner: Runner, serial: str) -> set[str]:
    for command in (["ls", "/dev/block/by-name"], ["ls", "-1", "/dev/block/by-name"]):
        result = runner.run(["adb", "-s", serial, "shell", *command])
        names = {
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip() and SAFE_BLOCK.match(line.strip())
        }
        if names:
            return names
    raise BackupError("kon /dev/block/by-name niet lezen")


def _require_root(runner: Runner, serial: str) -> None:
    result = runner.run(["adb", "-s", serial, "shell", "su", "-c", "id"])
    if "uid=0" not in result.output:
        raise BackupError(
            "geen root via adb; een backup van de actieve boot-partitie vraagt root. "
            "Start TWRP (fastboot boot) en probeer het opnieuw, of gebruik mtkclient."
        )


def _device_sha(runner: Runner, serial: str, block: str) -> str:
    result = runner.run(
        ["adb", "-s", serial, "shell", f"su -c 'sha256sum /dev/block/by-name/{block}'"]
    )
    for token in result.output.split():
        if len(token) == 64 and all(char in "0123456789abcdef" for char in token.lower()):
            return token.lower()
    return ""


def _dump(runner: Runner, serial: str, block: str, path: Path) -> tuple[str, int]:
    part = path.with_name(path.name + ".part")
    code, data, error = runner.run_bytes(
        [
            "adb",
            "-s",
            serial,
            "exec-out",
            f"su -c 'dd if=/dev/block/by-name/{block} bs=4M 2>/dev/null'",
        ],
        timeout=900,
    )
    if code != 0:
        raise BackupError(f"dd van {block} mislukte: {error.strip() or code}")
    if not data:
        raise BackupError(f"dd van {block} gaf geen bytes")
    part.write_bytes(data)
    part.replace(path)
    return sha256_file(path), len(data)


def create_backup(
    runner: Runner,
    device: Device,
    profile: Profile,
    store: Store,
    backups_root: Path,
    log: Callable[[str], None],
) -> list[BackupResult]:
    if device.mode != "adb":
        raise BackupError(
            f"toestel staat in {device.mode}; backups maken kan alleen in Android of in TWRP "
            "(fastboot boot twrp.img) met adb"
        )
    _require_root(runner, device.serial)
    available = _available_blocks(runner, device.serial)
    slot = device.slot
    if profile.slot and not slot:
        raise BackupError("profiel gebruikt A/B-slots maar de actieve slot is onbekend; start Android op")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = backups_root / profile.codename / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[BackupResult] = []
    meta: dict[str, object] = {
        "profile": profile.id,
        "codename": profile.codename,
        "serial": device.serial,
        "slot": slot,
        "android": device.android,
        "build": device.props.get("ro.build.display.id", ""),
        "created": stamp,
    }
    partitions: dict[str, dict[str, object]] = {}

    for partition in profile.backup.partitions:
        block = resolve_block(available, partition, slot)
        if block is None:
            raise BackupError(f"partitie '{partition}' staat niet in /dev/block/by-name")
        log(f"  dump    {block}")
        path = out_dir / f"{block}.img"
        digest, size = _dump(runner, device.serial, block, path)
        device_digest = _device_sha(runner, device.serial, block)
        if not device_digest:
            path.unlink(missing_ok=True)
            raise BackupError(f"{block}: kon geen sha256 op het toestel berekenen")
        if device_digest != digest:
            path.unlink(missing_ok=True)
            raise BackupError(f"{block}: sha256 op het toestel verschilt van de kopie")
        confirmed = True
        log(f"  ok      {block}.img ({human_size(size)}, sha256 {digest[:12]}…)")
        store.record_backup(
            device=profile.codename,
            partition=partition,
            path=path,
            sha256=digest,
            size=size,
            status="ok",
            serial=device.serial,
            meta={"block": block, "confirmed": confirmed},
        )
        results.append(BackupResult(partition, block, path, digest, size, confirmed))
        partitions[block] = {"sha256": digest, "size": size, "confirmed": confirmed}

    meta["partitions"] = partitions
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "sha256.txt").write_text(
        "".join(f"{item.sha256}  {item.path.name}\n" for item in results), encoding="utf-8"
    )
    return results
