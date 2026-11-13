from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass
from pathlib import Path

MANIFEST = "manifest.txt"


class ArtifactError(Exception):
    pass


@dataclass(frozen=True)
class Problem:
    filename: str
    message: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(directory: Path) -> dict[str, str]:
    path = directory / MANIFEST
    if not path.is_file():
        raise ArtifactError(f"{MANIFEST} ontbreekt in {directory}")
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ArtifactError(f"ongeldige regel in {path}: {line}")
        digest, filename = parts[0], parts[1].lstrip("*")
        entries[filename] = digest.lower()
    if not entries:
        raise ArtifactError(f"{path} bevat geen bestanden")
    return entries


def write_manifest(directory: Path, entries: dict[str, str], header: str = "") -> Path:
    lines = [line for line in header.splitlines() if line]
    lines = [f"# {line.lstrip('# ')}" for line in lines]
    lines += [f"{digest}  {name}" for name, digest in sorted(entries.items())]
    path = directory / MANIFEST
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def verify(directory: Path, manifest: dict[str, str] | None = None) -> list[Problem]:
    entries = manifest if manifest is not None else read_manifest(directory)
    problems: list[Problem] = []
    for filename, expected in entries.items():
        path = directory / filename
        if not path.is_file():
            problems.append(Problem(filename, "ontbreekt"))
            continue
        actual = sha256_file(path)
        if actual != expected:
            problems.append(Problem(filename, f"sha256 ongelijk (verwacht {expected[:12]}…, kreeg {actual[:12]}…)"))
    return problems


def match(directory: Path, pattern: str, manifest: dict[str, str] | None = None) -> Path:
    entries = manifest if manifest is not None else read_manifest(directory)
    hits = sorted(name for name in entries if fnmatch.fnmatchcase(name, pattern))
    if not hits:
        raise ArtifactError(f"geen bestand voor patroon '{pattern}' in {directory}")
    if len(hits) > 1:
        exact = [name for name in hits if name == pattern]
        if len(exact) == 1:
            return directory / exact[0]
        raise ArtifactError(
            f"patroon '{pattern}' is niet uniek: {', '.join(hits[:5])}"
            + (" …" if len(hits) > 5 else "")
        )
    return directory / hits[0]


def size_of(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GiB"
