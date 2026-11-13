from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .runner import Runner

PROP_RE = re.compile(r"^\[(.+?)\]: \[(.*)\]$")
VAR_RE = re.compile(r"^([A-Za-z0-9_.-]+):\s?(.*)$")

FASTBOOT_VARS = ("product", "serialno", "unlocked", "secure", "current-slot", "slot-count")


@dataclass
class Device:
    serial: str
    mode: str
    model: str = ""
    codename: str = ""
    android: str = ""
    props: dict[str, str] = field(default_factory=dict)
    vars: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.model or self.codename or self.serial

    @property
    def slot(self) -> str:
        slot = (self.vars.get("current-slot") or "").strip()
        if slot:
            return slot
        suffix = (self.props.get("ro.boot.slot_suffix") or "").strip()
        return suffix.lstrip("_") if suffix else ""

    @property
    def unlocked(self) -> bool:
        return (self.vars.get("unlocked") or "").strip().lower() == "yes"


@dataclass
class Prober:
    runner: Runner

    def adb_devices(self) -> list[dict[str, str]]:
        result = self.runner.run(["adb", "devices", "-l"])
        found: list[dict[str, str]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            entry = {"serial": parts[0], "state": parts[1]}
            for token in parts[2:]:
                if ":" in token:
                    key, value = token.split(":", 1)
                    entry[key] = value
            found.append(entry)
        return found

    def fastboot_devices(self) -> list[str]:
        result = self.runner.run(["fastboot", "devices"])
        serials: list[str] = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].strip() in {"fastboot", "fastbootd"}:
                serials.append(parts[0])
        return serials

    def getprops(self, serial: str) -> dict[str, str]:
        result = self.runner.run(["adb", "-s", serial, "shell", "getprop"])
        props: dict[str, str] = {}
        for line in result.stdout.splitlines():
            match = PROP_RE.match(line.strip())
            if match:
                props[match.group(1)] = match.group(2)
        return props

    def getvars(self, serial: str, names: tuple[str, ...] = FASTBOOT_VARS) -> dict[str, str]:
        values: dict[str, str] = {}
        for name in names:
            result = self.runner.run(["fastboot", "-s", serial, "getvar", name])
            for line in result.output.splitlines():
                match = VAR_RE.match(line.strip())
                if match and match.group(1) == name:
                    values[name] = match.group(2).strip()
        return values

    def probe(self, serial: str | None = None) -> Device | None:
        fastboot_serials = self.fastboot_devices()
        adb_entries = self.adb_devices()

        if serial is None:
            candidates = len(fastboot_serials) + len(adb_entries)
            if candidates != 1:
                return None
            serial = fastboot_serials[0] if fastboot_serials else adb_entries[0]["serial"]

        if serial in fastboot_serials:
            variables = self.getvars(serial)
            return Device(
                serial=serial,
                mode="fastboot",
                codename=variables.get("product", ""),
                model=variables.get("product", ""),
                vars=variables,
            )

        entry = next((item for item in adb_entries if item["serial"] == serial), None)
        if entry is None:
            return None

        state = entry.get("state", "")
        if state == "device":
            mode = "adb"
        else:
            mode = state or "adb"
        props = self.getprops(serial) if mode == "adb" else {}
        codename = props.get("ro.product.device") or entry.get("device", "")
        model = props.get("ro.product.model") or entry.get("model", "").replace("_", " ")
        return Device(
            serial=serial,
            mode=mode,
            model=model,
            codename=codename,
            android=props.get("ro.build.version.release", ""),
            props=props,
        )

    def all_devices(self) -> list[Device]:
        found: list[Device] = []
        for serial in self.fastboot_devices():
            device = self.probe(serial)
            if device is not None:
                found.append(device)
        for entry in self.adb_devices():
            if any(item.serial == entry["serial"] for item in found):
                continue
            device = self.probe(entry["serial"])
            if device is not None:
                found.append(device)
        return found

    def wait_for(self, serial: str, mode: str, timeout: float) -> Device | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            device = self.probe(serial)
            if device is not None and device.mode == mode:
                return device
            time.sleep(1.0)
        return None
