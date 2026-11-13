from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import helpers

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.path.join(HERE.parent, "src"))

from flashkit.artifacts import sha256_file, write_manifest  # noqa: E402
from flashkit.devices import Device, Prober  # noqa: E402
from flashkit.flash import build_plan, execute  # noqa: E402
from flashkit.profiles import load_profile  # noqa: E402
from flashkit.store import Store  # noqa: E402

SERIAL = "S1"


class PlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "db.sqlite")
        self.addCleanup(self.store.close)
        self.image = self.root / "magisk.img"
        self.image.write_bytes(b"patched-boot")
        self.profile = load_profile(self._profile())

    def _profile(self) -> Path:
        path = self.root / "p.json"
        path.write_text(
            """
            {
              "id": "zeus-test", "name": "Zeus", "codename": "zeus", "slot": true,
              "requires_file": true,
              "backup": {"required": true, "partitions": ["boot"]},
              "steps": [
                {"kind": "fastboot", "partition": "boot_{slot}", "artifact": "{file}"},
                {"kind": "reboot", "target": "system"},
                {"kind": "wait", "mode": "adb", "timeout": 5}
              ]
            }
            """,
            encoding="utf-8",
        )
        return path

    def _device(self) -> Device:
        return Device(
            serial=SERIAL,
            mode="fastboot",
            codename="zeus",
            vars={"unlocked": "yes", "current-slot": "b"},
        )

    def test_missing_backup_blocks(self) -> None:
        plan = build_plan(self.profile, self._device(), self.root / "artifacts", self.store, self.image)
        names = [check.name for check in plan.blockers]
        self.assertIn("backup boot", names)

    def test_missing_file_is_a_blocker_not_an_error(self) -> None:
        plan = build_plan(self.profile, self._device(), self.root / "artifacts", self.store, None)
        names = [check.name for check in plan.blockers]
        self.assertIn("image", names)
        self.assertIn("stap 1", names)
        self.assertIsNone(plan.steps[0].command)

    def test_with_backup_plan_resolves_slot(self) -> None:
        self.store.record_backup(
            "zeus", "boot", self.image, sha256_file(self.image), self.image.stat().st_size, "ok"
        )
        plan = build_plan(self.profile, self._device(), self.root / "artifacts", self.store, self.image)
        self.assertEqual([], plan.blockers)
        self.assertIn("boot_b", plan.steps[0].command or [])
        self.assertIn(str(self.image), plan.steps[0].command or [])

    def test_modified_backup_blocks(self) -> None:
        self.store.record_backup(
            "zeus", "boot", self.image, sha256_file(self.image), self.image.stat().st_size, "ok"
        )
        self.image.write_bytes(b"ander-image")
        plan = build_plan(self.profile, self._device(), self.root / "artifacts", self.store, self.image)
        self.assertIn("backup boot", [check.name for check in plan.blockers])

    def test_wrong_device_blocks(self) -> None:
        device = Device(serial=SERIAL, mode="fastboot", codename="komodo", vars={"unlocked": "yes"})
        plan = build_plan(self.profile, device, self.root / "artifacts", self.store, self.image)
        self.assertIn("toestel", [check.name for check in plan.blockers])

    def test_locked_blocks(self) -> None:
        device = Device(serial=SERIAL, mode="fastboot", codename="zeus", vars={"unlocked": "no", "current-slot": "b"})
        plan = build_plan(self.profile, device, self.root / "artifacts", self.store, self.image)
        self.assertIn("ontgrendeld", [check.name for check in plan.blockers])

    def test_missing_artifact_manifest_blocks(self) -> None:
        path = self.root / "q.json"
        path.write_text(
            """
            {
              "id": "q", "name": "Q", "codename": "zeus",
              "steps": [{"kind": "fastboot", "partition": "boot", "artifact": "boot.img"}]
            }
            """,
            encoding="utf-8",
        )
        profile = load_profile(path)
        plan = build_plan(profile, self._device(), self.root / "artifacts", self.store)
        self.assertIn("bestanden", [check.name for check in plan.blockers])

    def test_artifact_plan_uses_verified_file(self) -> None:
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        boot = artifacts / "boot.img"
        boot.write_bytes(b"boot")
        write_manifest(artifacts, {"boot.img": sha256_file(boot)})
        path = self.root / "q.json"
        path.write_text(
            """
            {
              "id": "q", "name": "Q", "codename": "zeus",
              "steps": [{"kind": "fastboot", "partition": "boot", "artifact": "boot.img"}]
            }
            """,
            encoding="utf-8",
        )
        plan = build_plan(load_profile(path), self._device(), artifacts, self.store)
        self.assertEqual([], plan.blockers)
        self.assertEqual(str(boot), plan.steps[0].command[-1])


class ExecuteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _plan(self, code: int = 0):
        artifacts = self.root / "artifacts"
        artifacts.mkdir(exist_ok=True)
        boot = artifacts / "boot.img"
        boot.write_bytes(b"boot")
        write_manifest(artifacts, {"boot.img": sha256_file(boot)})
        profile_path = self.root / "p.json"
        profile_path.write_text(
            """
            {
              "id": "p", "name": "P", "codename": "zeus",
              "steps": [
                {"kind": "fastboot", "partition": "boot", "artifact": "boot.img"},
                {"kind": "reboot", "target": "bootloader"},
                {"kind": "wait", "mode": "fastboot", "timeout": 5}
              ]
            }
            """,
            encoding="utf-8",
        )
        store = Store(self.root / "db.sqlite")
        self.addCleanup(store.close)
        device = Device(serial=SERIAL, mode="fastboot", codename="zeus", vars={"unlocked": "yes"})
        plan = build_plan(load_profile(profile_path), device, artifacts, store)
        self.assertEqual([], plan.blockers)

        flash_result = helpers.result(
            ["fastboot", "-s", SERIAL, "flash", "boot", str(boot)], f"Sending boot\nOKAY\n", code=code
        )
        reboot_result = helpers.result(["fastboot", "-s", SERIAL, "reboot", "bootloader"], "OKAY\n")
        table = {
            ("fastboot", "-s", SERIAL, "flash", "boot", str(boot)): flash_result,
            ("fastboot", "-s", SERIAL, "reboot", "bootloader"): reboot_result,
            ("fastboot", "devices"): helpers.result(["fastboot", "devices"], f"{SERIAL}\tfastboot\n"),
        }
        for name, value in (
            ("product", "zeus"),
            ("serialno", SERIAL),
            ("unlocked", "yes"),
            ("secure", "no"),
            ("current-slot", "a"),
            ("slot-count", "2"),
        ):
            table[("fastboot", "-s", SERIAL, "getvar", name)] = helpers.result(
                ["fastboot", "-s", SERIAL, "getvar", name], "", f"{name}: {value}\n"
            )
        runner = helpers.FakeRunner(table)
        return plan, runner

    def test_execute_success(self) -> None:
        plan, runner = self._plan()
        lines: list[str] = []
        outcome = execute(runner, Prober(runner), plan, lines.append, interactive=False)
        self.assertEqual(0, outcome, "\n".join(lines))
        self.assertTrue(any("flash" in " ".join(call) for call in runner.calls))

    def test_execute_failure_stops(self) -> None:
        plan, runner = self._plan(code=1)
        lines: list[str] = []
        outcome = execute(runner, Prober(runner), plan, lines.append, interactive=False)
        self.assertEqual(1, outcome)
        self.assertIn("FOUT", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
