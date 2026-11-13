from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STEP_KINDS = {"fastboot", "fastboot-boot", "reboot", "wait", "sideload", "manual", "verify"}
REBOOT_TARGETS = {"", "bootloader", "recovery", "system"}
WAIT_MODES = {"adb", "fastboot", "sideload", "recovery"}


class ProfileError(Exception):
    pass


def _require(raw: dict[str, Any], key: str, where: str) -> Any:
    if key not in raw or raw[key] in ("", None):
        raise ProfileError(f"{where}: veld '{key}' ontbreekt")
    return raw[key]


@dataclass(frozen=True)
class Step:
    kind: str
    title: str
    partition: str = ""
    artifact: str = ""
    target: str = ""
    mode: str = ""
    timeout: float = 0.0
    command: str = ""
    expect: str = ""
    text: str = ""


@dataclass(frozen=True)
class Source:
    type: str
    device: str = ""
    files: tuple[str, ...] = ()
    entries: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Backup:
    required: bool
    partitions: tuple[str, ...]


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    codename: str
    path: Path
    vendor: str = ""
    description: str = ""
    notes: str = ""
    slot: bool = False
    match: tuple[str, ...] = ()
    source: Source | None = None
    backup: Backup = field(default_factory=lambda: Backup(False, ()))
    steps: tuple[Step, ...] = ()
    requires_file: bool = False

    def matches(self, codename: str) -> bool:
        wanted = {self.codename.lower(), *(alias.lower() for alias in self.match)}
        return codename.strip().lower() in wanted


def _load_step(raw: dict[str, Any], where: str) -> Step:
    kind = _require(raw, "kind", where)
    if kind not in STEP_KINDS:
        raise ProfileError(f"{where}: onbekende stap '{kind}'")
    title = raw.get("title") or {
        "fastboot": f"flash {raw.get('partition', '?')}",
        "fastboot-boot": "start image zonder te schrijven",
        "reboot": f"herstart naar {raw.get('target', 'systeem')}",
        "wait": f"wacht op {raw.get('mode', '?')}",
        "sideload": "sideload de zip",
        "manual": "handmatige stap",
        "verify": "controleer resultaat",
    }[kind]

    if kind in {"fastboot", "fastboot-boot"}:
        if not raw.get("partition") and kind == "fastboot":
            raise ProfileError(f"{where}: 'partition' ontbreekt")
        _require(raw, "artifact", where)
    if kind == "sideload":
        _require(raw, "artifact", where)
    if kind == "reboot" and raw.get("target", "") not in REBOOT_TARGETS:
        raise ProfileError(f"{where}: onbekend herstartdoel '{raw.get('target')}'")
    if kind == "wait":
        mode = _require(raw, "mode", where)
        if mode not in WAIT_MODES:
            raise ProfileError(f"{where}: onbekende wachtmodus '{mode}'")
    if kind == "verify":
        _require(raw, "command", where)
        _require(raw, "expect", where)

    return Step(
        kind=kind,
        title=str(title),
        partition=str(raw.get("partition", "")),
        artifact=str(raw.get("artifact", "")),
        target=str(raw.get("target", "")),
        mode=str(raw.get("mode", "")),
        timeout=float(raw.get("timeout", 0) or 0),
        command=str(raw.get("command", "")),
        expect=str(raw.get("expect", "")),
        text=str(raw.get("text", "")),
    )


def _load_source(raw: Any, where: str) -> Source | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ProfileError(f"{where}: 'source' moet een object zijn")
    kind = _require(raw, "type", where)
    if kind == "lineage":
        device = _require(raw, "device", where)
        files = raw.get("files")
        if not isinstance(files, list) or not files:
            raise ProfileError(f"{where}: 'files' moet een niet-lege lijst zijn")
        return Source(type=kind, device=str(device), files=tuple(str(item) for item in files))
    if kind == "url":
        entries = raw.get("files")
        if not isinstance(entries, list) or not entries:
            raise ProfileError(f"{where}: 'files' moet een niet-lege lijst zijn")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ProfileError(f"{where}: elk bestand moet een object zijn")
            for key in ("name", "url", "sha256"):
                _require(entry, key, where)
        return Source(type=kind, entries=tuple(entries))
    raise ProfileError(f"{where}: onbekend brontype '{kind}'")


def load_profile(path: Path) -> Profile:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileError(f"{path}: profiel moet een object zijn")

    where = path.name
    profile_id = str(_require(raw, "id", where))
    name = str(_require(raw, "name", where))
    codename = str(_require(raw, "codename", where))

    backup_raw = raw.get("backup") or {}
    if not isinstance(backup_raw, dict):
        raise ProfileError(f"{where}: 'backup' moet een object zijn")
    partitions = backup_raw.get("partitions") or []
    if not isinstance(partitions, list):
        raise ProfileError(f"{where}: 'backup.partitions' moet een lijst zijn")
    backup = Backup(required=bool(backup_raw.get("required", False)), partitions=tuple(str(p) for p in partitions))
    if backup.required and not backup.partitions:
        raise ProfileError(f"{where}: verplichte backup zonder partities")

    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ProfileError(f"{where}: 'steps' moet een niet-lege lijst zijn")
    steps = tuple(_load_step(step, f"{where} stap {index + 1}") for index, step in enumerate(steps_raw))

    match = raw.get("match") or [codename]
    if not isinstance(match, list):
        raise ProfileError(f"{where}: 'match' moet een lijst zijn")

    return Profile(
        id=profile_id,
        name=name,
        codename=codename,
        path=path,
        vendor=str(raw.get("vendor", "")),
        description=str(raw.get("description", "")),
        notes=str(raw.get("notes", "")),
        slot=bool(raw.get("slot", False)),
        match=tuple(str(alias) for alias in match),
        source=_load_source(raw.get("source"), where),
        backup=backup,
        steps=steps,
        requires_file=bool(raw.get("requires_file", False)),
    )


def load_all(directory: Path) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    if not directory.is_dir():
        raise ProfileError(f"profielmap ontbreekt: {directory}")
    for path in sorted(directory.glob("*.json")):
        profile = load_profile(path)
        if profile.id in profiles:
            raise ProfileError(f"dubbel profiel-id '{profile.id}' in {path}")
        profiles[profile.id] = profile
    if not profiles:
        raise ProfileError(f"geen profielen in {directory}")
    return profiles


def resolve(profiles: dict[str, Profile], reference: str) -> Profile:
    if reference in profiles:
        return profiles[reference]
    hits = [profile for profile in profiles.values() if profile.matches(reference)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ProfileError(f"onbekend profiel '{reference}'")
    names = ", ".join(profile.id for profile in hits)
    raise ProfileError(f"'{reference}' past bij meerdere profielen: {names}")


def for_codename(profiles: dict[str, Profile], codename: str) -> list[Profile]:
    return [profile for profile in profiles.values() if profile.matches(codename)]
