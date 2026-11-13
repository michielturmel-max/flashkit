from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.path.join(HERE.parent, "src"))
sys.path.insert(0, str(HERE))

from flashkit.runner import Result  # noqa: E402


class FakeRunner:
    def __init__(self, table: dict[tuple[str, ...], Result], default: Result | None = None):
        self.table = table
        self.default = default
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], timeout: float | None = None) -> Result:
        key = tuple(argv)
        self.calls.append(list(argv))
        if key in self.table:
            return self.table[key]
        if self.default is not None:
            return self.default
        return Result(key, 0, "", "")

    def run_bytes(self, argv: list[str], timeout: float | None = None) -> tuple[int, bytes, str]:
        result = self.run(argv, timeout)
        return result.returncode, result.stdout.encode(), result.stderr

    def stream(self, argv: list[str], timeout: float | None = None) -> int:
        return self.run(argv, timeout).returncode


def result(argv: list[str], stdout: str = "", stderr: str = "", code: int = 0) -> Result:
    return Result(tuple(argv), code, stdout, stderr)
