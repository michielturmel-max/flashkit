from __future__ import annotations

import unittest

import helpers

from flashkit.devices import FASTBOOT_VARS, Prober

SERIAL = "TESTSERIAL"


def fastboot_runner(codename: str = "komodo", unlocked: str = "yes", slot: str = "b") -> helpers.FakeRunner:
    table = {
        ("fastboot", "devices"): helpers.result(["fastboot", "devices"], f"{SERIAL}\tfastboot\n"),
    }
    values = {
        "product": codename,
        "serialno": SERIAL,
        "unlocked": unlocked,
        "secure": "no",
        "current-slot": slot,
        "slot-count": "2",
    }
    for name in FASTBOOT_VARS:
        table[("fastboot", "-s", SERIAL, "getvar", name)] = helpers.result(
            ["fastboot", "-s", SERIAL, "getvar", name],
            "",
            f"{name}: {values[name]}\nFinished. Total time: 0.002s\n",
        )
    return helpers.FakeRunner(table)


class FastbootTest(unittest.TestCase):
    def test_probe_reads_vars(self) -> None:
        device = Prober(fastboot_runner()).probe(SERIAL)
        assert device is not None
        self.assertEqual("fastboot", device.mode)
        self.assertEqual("komodo", device.codename)
        self.assertEqual("b", device.slot)
        self.assertTrue(device.unlocked)

    def test_locked_device(self) -> None:
        device = Prober(fastboot_runner(unlocked="no")).probe(SERIAL)
        assert device is not None
        self.assertFalse(device.unlocked)


class AdbTest(unittest.TestCase):
    def _runner(self, state: str = "device", props: str = "") -> helpers.FakeRunner:
        table = {
            ("adb", "devices", "-l"): helpers.result(
                ["adb", "devices", "-l"],
                f"List of devices attached\n{SERIAL}  {state} usb:1-1 product:komodo model:Pixel_9_Pro_XL device:komodo\n",
            ),
            ("adb", "-s", SERIAL, "shell", "getprop"): helpers.result(
                ["adb", "-s", SERIAL, "shell", "getprop"], props
            ),
            ("fastboot", "devices"): helpers.result(["fastboot", "devices"], ""),
        }
        return helpers.FakeRunner(table)

    def test_probe_reads_props(self) -> None:
        props = (
            "[ro.product.device]: [komodo]\n"
            "[ro.product.model]: [Pixel 9 Pro XL]\n"
            "[ro.build.version.release]: [17]\n"
            "[ro.boot.slot_suffix]: [_a]\n"
        )
        device = Prober(self._runner(props=props)).probe(SERIAL)
        assert device is not None
        self.assertEqual("adb", device.mode)
        self.assertEqual("komodo", device.codename)
        self.assertEqual("Pixel 9 Pro XL", device.model)
        self.assertEqual("a", device.slot)

    def test_sideload_has_no_props(self) -> None:
        device = Prober(self._runner(state="sideload")).probe(SERIAL)
        assert device is not None
        self.assertEqual("sideload", device.mode)
        self.assertEqual("komodo", device.codename)

    def test_multiple_devices_without_serial(self) -> None:
        table = {
            ("fastboot", "devices"): helpers.result(["fastboot", "devices"], "A\tfastboot\nB\tfastboot\n"),
            ("adb", "devices", "-l"): helpers.result(["adb", "devices", "-l"], "List of devices attached\n"),
        }
        self.assertIsNone(Prober(helpers.FakeRunner(table)).probe())


if __name__ == "__main__":
    unittest.main()
