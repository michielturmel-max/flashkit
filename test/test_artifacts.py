from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.path.join(HERE.parent, "src"))

from flashkit.artifacts import (  # noqa: E402
    ArtifactError,
    match,
    read_manifest,
    sha256_file,
    verify,
    write_manifest,
)


class ArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "boot.img").write_bytes(b"boot-image")
        (self.dir / "lineage-1-signed.zip").write_bytes(b"zip")
        self.manifest = {
            "boot.img": sha256_file(self.dir / "boot.img"),
            "lineage-1-signed.zip": sha256_file(self.dir / "lineage-1-signed.zip"),
        }
        write_manifest(self.dir, self.manifest, header="test")

    def test_roundtrip_and_verify(self) -> None:
        entries = read_manifest(self.dir)
        self.assertEqual(self.manifest, entries)
        self.assertEqual([], verify(self.dir, entries))

    def test_corrupt_file(self) -> None:
        (self.dir / "boot.img").write_bytes(b"ander")
        problems = verify(self.dir, self.manifest)
        self.assertEqual(["boot.img"], [problem.filename for problem in problems])

    def test_missing_file(self) -> None:
        (self.dir / "boot.img").unlink()
        problems = verify(self.dir, self.manifest)
        self.assertEqual(["boot.img"], [problem.filename for problem in problems])

    def test_match_glob(self) -> None:
        path = match(self.dir, "lineage-*.zip", self.manifest)
        self.assertEqual("lineage-1-signed.zip", path.name)

    def test_match_ambiguous(self) -> None:
        (self.dir / "lineage-2-signed.zip").write_bytes(b"zip2")
        manifest = dict(self.manifest)
        manifest["lineage-2-signed.zip"] = hashlib.sha256(b"zip2").hexdigest()
        with self.assertRaises(ArtifactError):
            match(self.dir, "lineage-*.zip", manifest)

    def test_manifest_without_files(self) -> None:
        (self.dir / "manifest.txt").write_text("# alleen commentaar\n", encoding="utf-8")
        with self.assertRaises(ArtifactError):
            read_manifest(self.dir)


if __name__ == "__main__":
    unittest.main()
