from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.path.join(HERE.parent, "src"))

from flashkit.profiles import ProfileError, for_codename, load_all, load_profile, resolve  # noqa: E402

PROFILES = HERE.parent / "profiles"


class RealProfilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profiles = load_all(PROFILES)

    def test_all_bundled_profiles_load(self) -> None:
        self.assertEqual(
            {"komodo-lineage", "zeus-magisk", "lamu-lineage", "h815-boot-test", "h815-flash-boot"},
            set(self.profiles),
        )

    def test_resolve_by_id_and_codename(self) -> None:
        self.assertEqual("komodo-lineage", resolve(self.profiles, "komodo-lineage").id)
        self.assertEqual("zeus-magisk", resolve(self.profiles, "zeus").id)

    def test_ambigious_codename(self) -> None:
        with self.assertRaises(ProfileError):
            resolve(self.profiles, "h815")
        self.assertEqual(2, len(for_codename(self.profiles, "h815")))

    def test_komodo_steps_use_slot_where_needed(self) -> None:
        profile = self.profiles["komodo-lineage"]
        self.assertTrue(profile.backup.partitions)
        self.assertEqual("lineage", profile.source.type if profile.source else "")


class ValidationTest(unittest.TestCase):
    def _write(self, payload: dict) -> Path:
        handle, name = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        path = Path(name)
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.addCleanup(path.unlink)
        return path

    def test_missing_step_kind_is_rejected(self) -> None:
        path = self._write({"id": "x", "name": "X", "codename": "x", "steps": [{"kind": "fastboot"}]})
        with self.assertRaises(ProfileError):
            load_profile(path)

    def test_unknown_kind_is_rejected(self) -> None:
        path = self._write(
            {"id": "x", "name": "X", "codename": "x", "steps": [{"kind": "magic", "artifact": "a"}]}
        )
        with self.assertRaises(ProfileError):
            load_profile(path)

    def test_backup_without_partitions_is_rejected(self) -> None:
        path = self._write(
            {
                "id": "x",
                "name": "X",
                "codename": "x",
                "backup": {"required": True, "partitions": []},
                "steps": [{"kind": "manual", "text": "doe iets"}],
            }
        )
        with self.assertRaises(ProfileError):
            load_profile(path)

    def test_url_source_requires_sha256(self) -> None:
        path = self._write(
            {
                "id": "x",
                "name": "X",
                "codename": "x",
                "source": {"type": "url", "files": [{"name": "a.img", "url": "https://example/a.img"}]},
                "steps": [{"kind": "manual", "text": "doe iets"}],
            }
        )
        with self.assertRaises(ProfileError):
            load_profile(path)
