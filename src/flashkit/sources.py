from __future__ import annotations

import fnmatch
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .artifacts import human_size, sha256_file
from .profiles import Source

LINEAGE_API = os.getenv(
    "FLASHKIT_LINEAGE_API", "https://download.lineageos.org/api/v2/devices/{device}/builds"
).strip()
USER_AGENT = "flashkit/0.1 (+https://github.com/michiel/flashkit)"


class SourceError(Exception):
    pass


@dataclass(frozen=True)
class Fetched:
    filename: str
    path: Path
    sha256: str
    size: int
    url: str


def select_build(payload: list[dict[str, Any]], patterns: tuple[str, ...]) -> dict[str, Any] | None:
    for build in payload:
        files = [entry for entry in build.get("files", []) if entry.get("filename")]
        chosen: dict[str, Any] = {}
        complete = True
        for pattern in patterns:
            hits = sorted(entry["filename"] for entry in files if fnmatch.fnmatchcase(entry["filename"], pattern))
            if not hits:
                complete = False
                break
            name = hits[0]
            chosen[name] = next(entry for entry in files if entry["filename"] == name)
        if complete:
            return chosen
    return None


def _request(url: str, start: int = 0) -> urllib.request.Request:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    if start:
        request.add_header("Range", f"bytes={start}-")
    return request


def http_json(url: str, timeout: float = 60) -> Any:
    try:
        with urllib.request.urlopen(_request(url), timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise SourceError(f"kon {url} niet lezen: {exc}") from exc


def download(
    url: str,
    dest: Path,
    expected_sha: str = "",
    expected_size: int = 0,
    retries: int = 2,
    timeout: float = 120,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.is_file() and (not expected_size or dest.stat().st_size == expected_size):
        if not expected_sha or sha256_file(dest) == expected_sha:
            return

    part = dest.with_name(dest.name + ".part")
    attempt = 0
    while True:
        start = part.stat().st_size if part.is_file() else 0
        try:
            with urllib.request.urlopen(_request(url, start), timeout=timeout) as response:
                if start and getattr(response, "status", 200) != 206:
                    start = 0
                mode = "ab" if start else "wb"
                with part.open(mode) as handle:
                    while chunk := response.read(1 << 20):
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
            break
        except (urllib.error.URLError, OSError) as exc:
            attempt += 1
            if attempt > retries:
                raise SourceError(f"downloaden van {url} mislukte: {exc}") from exc
            time.sleep(1.5 * attempt)

    if expected_size and part.stat().st_size != expected_size:
        actual = part.stat().st_size
        part.unlink(missing_ok=True)
        raise SourceError(f"{dest.name}: {actual} bytes, verwacht {expected_size}")

    actual = sha256_file(part)
    if expected_sha and actual != expected_sha:
        part.unlink(missing_ok=True)
        raise SourceError(f"{dest.name}: sha256 ongelijk (verwacht {expected_sha[:12]}…, kreeg {actual[:12]}…)")

    os.replace(part, dest)


def _fetch_entry(entry: dict[str, Any], dest: Path, log: Callable[[str], None]) -> Fetched:
    filename = str(entry["filename"] if "filename" in entry else entry["name"])
    url = str(entry["url"])
    sha = str(entry.get("sha256") or "")
    size = int(entry.get("size") or 0)
    path = dest / filename
    if path.is_file() and (not size or path.stat().st_size == size) and (not sha or sha256_file(path) == sha):
        log(f"  cached  {filename}")
    else:
        log(f"  get     {filename} ({human_size(size) if size else 'grootte onbekend'})")
        download(url, path, expected_sha=sha, expected_size=size)
        log(f"  ok      {filename}")
    return Fetched(filename=filename, path=path, sha256=sha or sha256_file(path), size=path.stat().st_size, url=url)


def fetch_lineage(source: Source, dest: Path, log: Callable[[str], None]) -> list[Fetched]:
    api = LINEAGE_API.format(device=source.device)
    cache = dest / "builds.json"
    dest.mkdir(parents=True, exist_ok=True)
    try:
        payload = http_json(api)
        cache.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except SourceError:
        if not cache.is_file():
            raise
        log(f"  waarschuwing: api onbereikbaar, gebruik {cache.name} van eerder")
        payload = json.loads(cache.read_text(encoding="utf-8"))

    if not isinstance(payload, list):
        raise SourceError("lineage-api gaf geen lijst met builds")
    chosen = select_build(payload, source.files)
    if chosen is None:
        raise SourceError(f"geen build met alle gevraagde bestanden ({', '.join(source.files)})")

    build = next(
        (item for item in payload if any(entry["filename"] in chosen for entry in item.get("files", []))),
        {},
    )
    log(f"  build   {build.get('date', '?')} ({build.get('type', '?')})")
    return [_fetch_entry(chosen[name], dest, log) for name in sorted(chosen)]


def fetch_urls(source: Source, dest: Path, log: Callable[[str], None]) -> list[Fetched]:
    dest.mkdir(parents=True, exist_ok=True)
    return [_fetch_entry(dict(entry), dest, log) for entry in source.entries]


def fetch(source: Source, dest: Path, log: Callable[[str], None]) -> list[Fetched]:
    if source.type == "lineage":
        return fetch_lineage(source, dest, log)
    if source.type == "url":
        return fetch_urls(source, dest, log)
    raise SourceError(f"onbekend brontype '{source.type}'")
