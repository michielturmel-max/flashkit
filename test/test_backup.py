from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

import helpers

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.path.join(HERE.parent, "src"))

from flashkit.backup import BackupError, create_backup, resolve_block  # noqa: E402
from flashkit.devices import Device  # noqa: E402
from flashkit.profiles import load_profile  # noqa: E402
from flashkit.store import Store  # noqa: E402

SERIAL = "S1"


class ResolveBlockTest(unittest.TestCase):
    def test_slot_wins(self) -> None:
        self.assertEqual("boot_b", resolve_block({"boot_a", "boot_b"}, "boot", "b"))

    def test_without_slot(self) -> None:
        self.assertEqual("boot", resolve_block({"boot"}, "boot", ""))

    def test_missing(self) -> None:
        self.assertIsNone(resolve_block({"dtbo"}, "boot", "a"))


class BackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile_path = self.root / "p.json"
        self.profile_path.write_text(
            """
            {
              "id": "t", "name": "T", "codename": "zeus", "slot": true,
              "backup": {"required": true, "partitions": ["boot"]},
              "steps": [{"kind": "manual", "text": "x"}]
            }
            """,
            encoding="utf-8",
        )
        self.profile = load_profile(self.profile_path)
        self.store = Store(self.root / "db.sqlite")
        self.addCleanup(self.store.close)

    def _runner(self, root_ok: bool = True) -> helpers.FakeRunner:
        data = b"boot-image-bytes"
        digest = hashlib.sha256(data).hexdigest()
        table = {
            ("adb", "-s", SERIAL, "shell", "su", "-c", "id"): helpers.result(
                ["adb", "-s", SERIAL, "shell", "su", "-c", "id"],
                "uid=0(root)" if root_ok else "uid=2000(shell)",
            ),
            ("adb", "-s", SERIAL, "shell", "ls", "/dev/block/by-name"): helpers.result(
                ["adb", "-s", SERIAL, "shell", "ls", "/dev/block/by-name"], "boot_a\nboot_b\n"
            ),
            ("adb", "-s", SERIAL, "shell", "su -c 'sha256sum /dev/block/by-name/boot_b'"): helpers.result(
                ["adb", "-s", SERIAL, "shell", "su -c 'sha256sum /dev/block/by-name/boot_b'"],
                f"{digest}  /dev/block/by-name/boot_b",
            ),
        }
        runner = helpers.FakeRunner(table)
        original = runner.run_bytes

        def run_bytes(argv: list[str], timeout: float | None = None) -> tuple[int, bytes, str]:
            if argv[:4] == ["adb", "-s", SERIAL, "exec-out"]:
                return 0, data, ""
            return original(argv, timeout)

        runner.run_bytes = run_bytes  # type: ignore[method-assign]
        return runner

    def test_backup_with_root(self) -> None:
        runner = self._runner()
        device = Device(serial=SERIAL, mode="adb", codename="zeus", props={"ro.boot.slot_suffix": "_b"})
        results = create_backup(
            runner, device, self.profile, self.store, self.root / "backups", log=lambda line: None
        )
        self.assertEqual(1, len(results))
        self.assertEqual("boot_b", results[0].block)
        self.assertTrue(results[0].confirmed)
        self.assertTrue(results[0].path.is_file())
        self.assertIsNotNone(self.store.backup_ok("zeus", "boot"))
        meta = results[0].path.parent / "meta.json"
        self.assertTrue(meta.is_file())

    def test_backup_without_root(self) -> None:
        device = Device(serial=SERIAL, mode="adb", codename="zeus")
        with self.assertRaises(BackupError):
            create_backup(
                self._runner(root_ok=False),
                device,
                self.profile,
                self.store,
                self.root / "backups",
                log=lambda line: None,
            )

    def test_backup_requires_adb(self) -> None:
        device = Device(serial=SERIAL, mode="fastboot", codename="zeus")
        with self.assertRaises(BackupError):
            create_backup(
                self._runner(),
                device,
                self.profile,
                self.store,
                self.root / "backups",
                log=lambda line: None,
            )


if __name__ == "__main__":
    unittest.main()
