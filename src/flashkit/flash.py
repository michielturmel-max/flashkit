from __future__ import annotations

import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .artifacts import ArtifactError, match, read_manifest, sha256_file, verify
from .config import BOOT_TIMEOUT, SIDELOAD_TIMEOUT
from .devices import Device, Prober
from .profiles import Profile, Step
from .runner import Runner
from .store import Store


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    message: str
    blocking: bool = True


@dataclass
class Planned:
    index: int
    step: Step
    command: list[str] | None = None
    note: str = ""

    @property
    def title(self) -> str:
        return self.step.title

    @property
    def display(self) -> str:
        if self.command:
            return shlex.join(self.command)
        return self.note


@dataclass
class Plan:
    profile: Profile
    device: Device
    artifact_dir: Path
    steps: list[Planned] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def blockers(self) -> list[Check]:
        return [check for check in self.checks if check.blocking and not check.ok]


class FlashError(Exception):
    pass


def _needs_slot(profile: Profile) -> bool:
    return any("{slot}" in step.partition for step in profile.steps)


def _slot_of(device: Device) -> str:
    slot = device.slot.strip()
    return slot if slot in {"a", "b"} else ""


def _artifact_for(
    step: Step,
    profile: Profile,
    artifact_dir: Path,
    manifest: dict[str, str] | None,
    file_path: Path | None,
) -> Path:
    if step.artifact == "{file}":
        if file_path is None:
            raise FlashError("dit profiel vraagt --file met een eigen image")
        return file_path
    if manifest is None:
        return artifact_dir / step.artifact
    return match(artifact_dir, step.artifact, manifest)


def build_plan(
    profile: Profile,
    device: Device,
    artifact_dir: Path,
    store: Store,
    file_path: Path | None = None,
) -> Plan:
    plan = Plan(profile=profile, device=device, artifact_dir=artifact_dir)

    matches = profile.matches(device.codename) or profile.matches(device.model)
    plan.checks.append(
        Check(
            "toestel",
            matches,
            f"{device.codename or device.name} is {profile.id}" if matches else f"verwacht {profile.codename}, kreeg {device.codename or '?'}",
        )
    )
    if device.mode == "fastboot":
        plan.checks.append(
            Check("ontgrendeld", device.unlocked, "bootloader is open" if device.unlocked else "bootloader is dicht")
        )
    if _needs_slot(profile):
        slot = _slot_of(device)
        plan.checks.append(
            Check("slot", bool(slot), f"actieve slot {slot}" if slot else "actieve slot onbekend")
        )

    manifest: dict[str, str] | None = None
    if any(step.artifact and step.artifact != "{file}" for step in profile.steps):
        try:
            manifest = read_manifest(artifact_dir)
        except ArtifactError as exc:
            plan.checks.append(Check("bestanden", False, str(exc)))
        else:
            problems = verify(artifact_dir, manifest)
            if problems:
                detail = "; ".join(f"{item.filename}: {item.message}" for item in problems)
                plan.checks.append(Check("bestanden", False, detail))
            else:
                plan.checks.append(Check("bestanden", True, f"{len(manifest)} bestanden gecontroleerd"))

    if profile.requires_file:
        exists = file_path is not None and file_path.is_file() and file_path.stat().st_size > 0
        message = str(file_path) if file_path else "geen --file opgegeven"
        if exists:
            assert file_path is not None
            message = f"{file_path.name} ({file_path.stat().st_size} bytes, sha256 {sha256_file(file_path)[:12]}…)"
        plan.checks.append(Check("image", exists, message))

    if profile.backup.required:
        for partition in profile.backup.partitions:
            row = store.backup_ok(profile.codename, partition)
            plan.checks.append(
                Check(
                    f"backup {partition}",
                    row is not None,
                    str(row["path"]) if row is not None else "geen geldige backup; draai eerst: flashkit backup",
                )
            )

    slot = _slot_of(device)
    for index, step in enumerate(profile.steps, start=1):
        command: list[str] | None = None
        note = ""
        partition = step.partition.replace("{slot}", slot or "<slot>")
        serial = device.serial

        if step.kind in {"fastboot", "fastboot-boot", "sideload"}:
            try:
                artifact = _artifact_for(step, profile, artifact_dir, manifest, file_path)
            except FlashError as exc:
                plan.checks.append(Check(f"stap {index}", False, str(exc)))
                note = str(exc)
            else:
                if step.kind == "fastboot":
                    command = ["fastboot", "-s", serial, "flash", partition, str(artifact)]
                elif step.kind == "fastboot-boot":
                    command = ["fastboot", "-s", serial, "boot", str(artifact)]
                else:
                    command = ["adb", "-s", serial, "sideload", str(artifact)]
        elif step.kind == "reboot":
            target = step.target
            if device.mode == "fastboot":
                command = ["fastboot", "-s", serial, "reboot"] + ([target] if target and target != "system" else [])
            else:
                command = ["adb", "-s", serial, "reboot"] + ([target] if target in {"bootloader", "recovery"} else [])
        elif step.kind == "wait":
            note = f"wacht maximaal {int(step.timeout or BOOT_TIMEOUT)}s op {step.mode}"
        elif step.kind == "manual":
            note = step.text or "handmatige stap op het toestel"
        elif step.kind == "verify":
            command = ["adb", "-s", serial, "shell", step.command]
            note = f"verwacht '{step.expect}'"
        plan.steps.append(Planned(index=index, step=step, command=command, note=note))

    return plan


def _write(log: Callable[[str], None], message: str) -> None:
    log(message)


def execute(
    runner: Runner,
    prober: Prober,
    plan: Plan,
    log: Callable[[str], None],
    interactive: bool = True,
) -> int:
    serial = plan.device.serial
    for planned in plan.steps:
        step = planned.step
        _write(log, f"\n[{planned.index}/{len(plan.steps)}] {planned.title}")
        if planned.command:
            _write(log, f"    $ {planned.display}")
        elif planned.note:
            _write(log, f"    {planned.note}")

        if step.kind in {"fastboot", "fastboot-boot"}:
            assert planned.command is not None
            result = runner.run(planned.command, timeout=900)
            if result.output:
                for line in result.output.splitlines():
                    _write(log, f"    {line}")
            if not result.ok:
                _write(log, f"    FOUT: fastboot gaf {result.returncode}")
                return 1
        elif step.kind == "sideload":
            device = prober.probe(serial)
            if device is None or device.mode != "sideload":
                _write(log, "    FOUT: toestel staat niet in sideload; kies op het toestel 'Apply update from ADB'")
                return 1
            assert planned.command is not None
            code = runner.stream(planned.command, timeout=SIDELOAD_TIMEOUT)
            if code != 0:
                _write(log, f"    FOUT: sideload gaf {code}")
                return 1
        elif step.kind == "reboot":
            assert planned.command is not None
            result = runner.run(planned.command, timeout=120)
            if result.output:
                _write(log, f"    {result.output}")
            if not result.ok:
                _write(log, f"    FOUT: herstarten gaf {result.returncode}")
                return 1
        elif step.kind == "wait":
            mode = step.mode
            _write(log, f"    wachten op {mode}…")
            device = prober.wait_for(serial, mode, step.timeout or BOOT_TIMEOUT)
            if device is None:
                _write(log, f"    FOUT: toestel kwam niet in {mode}")
                return 1
        elif step.kind == "manual":
            _write(log, f"    {step.text}")
            if not interactive:
                _write(log, "    FOUT: handmatige stap in niet-interactieve modus")
                return 1
            try:
                input("    Druk op Enter als dit klaar is… ")
            except EOFError:
                _write(log, "    FOUT: geen invoer")
                return 1
        elif step.kind == "verify":
            assert planned.command is not None
            result = runner.run(planned.command, timeout=120)
            output = result.output
            if output:
                _write(log, f"    {output}")
            if step.expect not in output:
                _write(log, f"    FOUT: '{step.expect}' niet gevonden")
                return 1

    _write(log, "\nKlaar.")
    return 0


def new_log_path(logs_dir: Path, profile: Profile) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return logs_dir / f"flash-{profile.id}-{stamp}.log"
