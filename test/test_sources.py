from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.path.join(HERE.parent, "src"))

from flashkit.sources import select_build  # noqa: E402


def build(date: str, names: list[str]) -> dict:
    return {
        "date": date,
        "type": "nightly",
        "files": [
            {"filename": name, "url": f"https://example/{name}", "sha256": "0" * 64, "size": 10}
            for name in names
        ],
    }


PATTERNS = ("lineage-*-signed.zip", "boot.img")


class SelectBuildTest(unittest.TestCase):
    def test_newest_complete_build_wins(self) -> None:
        payload = [
            build("2026-10-27", ["lineage-24-signed.zip"]),
            build("2026-10-20", ["lineage-24-signed.zip", "boot.img"]),
        ]
        chosen = select_build(payload, PATTERNS)
        assert chosen is not None
        self.assertIn("boot.img", chosen)
        self.assertIn("lineage-24-signed.zip", chosen)

    def test_no_complete_build(self) -> None:
        self.assertIsNone(select_build([build("2026-10-27", ["boot.img"])], PATTERNS))

    def test_patterns_pick_first_match(self) -> None:
        payload = [build("2026-10-27", ["lineage-24-signed.zip", "lineage-25-signed.zip", "boot.img"])]
        chosen = select_build(payload, PATTERNS)
        assert chosen is not None
        self.assertIn("lineage-24-signed.zip", chosen)
        self.assertNotIn("lineage-25-signed.zip", chosen)


if __name__ == "__main__":
    unittest.main()
