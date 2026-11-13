from __future__ import annotations

import subprocess
from dataclasses import dataclass

from .config import ADB, FASTBOOT, COMMAND_TIMEOUT


@dataclass(frozen=True)
class Result:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Runner:
    """Voert adb en fastboot uit. De logische naam in argv[0] wordt het pad."""

    def __init__(self, adb: str = ADB, fastboot: str = FASTBOOT, timeout: float = COMMAND_TIMEOUT):
        self.adb = adb
        self.fastboot = fastboot
        self.timeout = timeout

    def _resolve(self, argv: list[str]) -> list[str]:
        head, *rest = argv
        if head == "adb":
            return [self.adb, *rest]
        if head == "fastboot":
            return [self.fastboot, *rest]
        return list(argv)

    def run(self, argv: list[str], timeout: float | None = None) -> Result:
        command = self._resolve(argv)
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
        except FileNotFoundError:
            return Result(tuple(argv), 127, "", f"{command[0]} niet gevonden")
        except subprocess.TimeoutExpired:
            return Result(tuple(argv), 124, "", "timeout")
        return Result(tuple(argv), proc.returncode, proc.stdout or "", proc.stderr or "")

    def run_bytes(self, argv: list[str], timeout: float | None = None) -> tuple[int, bytes, str]:
        command = self._resolve(argv)
        try:
            proc = subprocess.run(command, capture_output=True, timeout=timeout or self.timeout)
        except FileNotFoundError:
            return 127, b"", f"{command[0]} niet gevonden"
        except subprocess.TimeoutExpired:
            return 124, b"", "timeout"
        return proc.returncode, proc.stdout or b"", (proc.stderr or b"").decode("utf-8", "replace")

    def stream(self, argv: list[str], timeout: float | None = None) -> int:
        command = self._resolve(argv)
        try:
            return subprocess.run(command, timeout=timeout or self.timeout).returncode
        except FileNotFoundError:
            return 127
        except subprocess.TimeoutExpired:
            return 124
