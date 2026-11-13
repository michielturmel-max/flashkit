from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from . import __version__
from .artifacts import ArtifactError, human_size, read_manifest, verify, write_manifest
from .backup import BackupError, create_backup
from .config import ADB, FASTBOOT, MIN_FREE_GIB, VERSION, Paths, default_home
from .devices import Device, Prober
from .flash import FlashError, build_plan, execute, new_log_path
from .profiles import Profile, ProfileError, for_codename, load_all, resolve
from .runner import Runner
from .sources import SourceError, fetch
from .store import Store


@dataclass
class App:
    paths: Paths
    store: Store
    runner: Runner
    prober: Prober
    profiles: dict[str, Profile]


def die(message: str, code: int = 1) -> NoReturn:
    print(f"flashkit: {message}", file=sys.stderr)
    raise SystemExit(code)


def emit(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=str))


def stamp(seconds: int) -> str:
    return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M")


def table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = ["  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)).rstrip()]
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip())
    return "\n".join(lines)


def _pick_device(app: App, serial: str | None) -> Device:
    devices = app.prober.all_devices()
    if serial:
        for device in devices:
            if device.serial == serial:
                return device
        die(f"toestel {serial} niet gevonden (adb of fastboot)")
    if not devices:
        die("geen toestel gevonden; sluit een telefoon aan met USB-debugging of in de bootloader")
    if len(devices) > 1:
        listing = ", ".join(f"{device.serial} ({device.mode})" for device in devices)
        die(f"meerdere toestellen: {listing}; kies met --serial")
    return devices[0]


def _profile_for_device(app: App, device: Device, explicit: str | None) -> Profile:
    if explicit:
        return resolve(app.profiles, explicit)
    hits = for_codename(app.profiles, device.codename) or for_codename(app.profiles, device.model)
    if not hits:
        die(f"geen profiel voor '{device.codename or device.name}'; zie: flashkit profiles")
    if len(hits) > 1:
        names = ", ".join(profile.id for profile in hits)
        die(f"meerdere profielen voor {device.codename}: {names}; kies met een profielnaam")
    return hits[0]


def _print_checks(checks: list) -> None:
    print("\ncontroles")
    for check in checks:
        mark = "ok" if check.ok else ("!!" if check.blocking else "..")
        print(f"  {mark:2}  {check.name:<20} {check.message}")


def _print_plan(plan) -> None:
    print(f"\nplan voor {plan.profile.id} ({plan.profile.name}) op {plan.device.serial}")
    for planned in plan.steps:
        print(f"  {planned.index:2}. {planned.title:<34} {planned.display}")


def cmd_profiles(app: App, args: argparse.Namespace) -> int:
    if args.json:
        emit(
            [
                {
                    "id": profile.id,
                    "codename": profile.codename,
                    "name": profile.name,
                    "vendor": profile.vendor,
                    "description": profile.description,
                    "source": profile.source.type if profile.source else None,
                    "backup": profile.backup.required,
                    "steps": len(profile.steps),
                }
                for profile in app.profiles.values()
            ],
            True,
        )
        return 0
    rows = []
    for profile in sorted(app.profiles.values(), key=lambda item: item.id):
        rows.append(
            [
                profile.id,
                profile.codename,
                profile.name,
                profile.source.type if profile.source else "-",
                "ja" if profile.backup.required else "nee",
                str(len(profile.steps)),
            ]
        )
    print(table(["id", "codename", "naam", "bron", "backup", "stappen"], rows))
    return 0


def cmd_scan(app: App, args: argparse.Namespace) -> int:
    devices = app.prober.all_devices()
    if args.json:
        emit(
            [
                {
                    "serial": device.serial,
                    "mode": device.mode,
                    "codename": device.codename,
                    "model": device.model,
                    "android": device.android,
                    "slot": device.slot,
                    "unlocked": device.unlocked,
                }
                for device in devices
            ],
            True,
        )
        return 0
    rows = []
    for device in devices:
        hits = for_codename(app.profiles, device.codename) or for_codename(app.profiles, device.model)
        rows.append(
            [
                device.serial,
                device.mode,
                device.codename or "?",
                ", ".join(profile.id for profile in hits) or "?",
                device.name,
            ]
        )
    if rows:
        print(table(["serie", "modus", "codename", "profiel", "model"], rows))
    else:
        print("geen toestel gevonden")
    return 0


def _unlock_ability(app: App, serial: str) -> str:
    result = app.runner.run(["fastboot", "-s", serial, "flashing", "get_unlock_ability"])
    for line in result.output.splitlines():
        if "get_unlock_ability" in line:
            return line.rsplit(":", 1)[-1].strip()
    return ""


def _verdict(app: App, device: Device) -> tuple[str, str]:
    ability = _unlock_ability(app, device.serial) if device.mode == "fastboot" else ""
    carrier = device.props.get("ro.boot.carrierid", "")
    if carrier:
        return "geblokkeerd", f"carrier-id '{carrier}': OEM-ontgrendeling is permanent uitgeschakeld"
    if ability:
        try:
            if int(ability, 0) & 0x1000000:
                return "geblokkeerd", "unlock_ability heeft bit 24: ontgrendelen is permanent uit"
        except ValueError:
            pass
    if device.mode == "fastboot":
        if device.unlocked:
            return "klaar", "bootloader is open"
        return "eerst ontgrendelen", "bootloader is dicht; gebruik de unlock-flow van dit toestel"
    if device.mode == "adb":
        locked = device.props.get("ro.boot.flash.locked", "")
        allowed = app.runner.run(
            ["adb", "-s", device.serial, "shell", "settings", "get", "global", "oem_unlock_allowed"]
        ).output.strip()
        if locked == "0":
            return "klaar", "bootloader is open"
        if allowed == "1":
            return "eerst ontgrendelen", "OEM-ontgrendeling staat aan; herstart naar de bootloader"
        return "eerst ontgrendelen", "zet OEM-ontgrendeling aan in de ontwikkelaarsopties"
    return "onbekend", f"toestel staat in {device.mode}"


def cmd_status(app: App, args: argparse.Namespace) -> int:
    device = _pick_device(app, args.serial)
    verdict, reason = _verdict(app, device)
    hits = for_codename(app.profiles, device.codename) or for_codename(app.profiles, device.model)

    lines = [
        ("serie", device.serial),
        ("modus", device.mode),
        ("codename", device.codename or "?"),
        ("model", device.name),
    ]
    if device.mode == "adb":
        lines += [
            ("android", device.android or "?"),
            ("build", device.props.get("ro.build.display.id", "?")),
            ("patch", device.props.get("ro.vendor.build.security_patch", "?")),
            ("carrier-id", device.props.get("ro.boot.carrierid", "<leeg>")),
            ("flash.locked", device.props.get("ro.boot.flash.locked", "?")),
        ]
    if device.mode == "fastboot":
        lines += [
            ("product", device.vars.get("product", "?")),
            ("slot", device.slot or "?"),
            ("slots", device.vars.get("slot-count", "?")),
            ("unlocked", device.vars.get("unlocked", "?")),
            ("secure", device.vars.get("secure", "?")),
            ("unlock_ability", _unlock_ability(app, device.serial) or "?"),
        ]
    lines += [
        ("profiel", ", ".join(profile.id for profile in hits) or "geen"),
        ("oordeel", f"{verdict}: {reason}"),
    ]
    if args.json:
        emit(
            {
                "serial": device.serial,
                "mode": device.mode,
                "codename": device.codename,
                "model": device.name,
                "android": device.android,
                "slot": device.slot,
                "unlocked": device.unlocked if device.mode == "fastboot" else None,
                "profiles": [profile.id for profile in hits],
                "verdict": verdict,
                "reason": reason,
            },
            True,
        )
        return 0
    width = max(len(name) for name, _ in lines)
    for name, value in lines:
        print(f"  {name:<{width}}  {value}")
    return 0


def cmd_doctor(app: App, args: argparse.Namespace) -> int:
    problems = 0
    print("flashkit doctor")

    for name, binary in (("adb", ADB), ("fastboot", FASTBOOT)):
        path = shutil.which(binary)
        if path is None:
            print(f"  !!  {name:<10} niet gevonden ({binary})")
            problems += 1
            continue
        result = app.runner.run([name, "--version"])
        version = result.stdout.splitlines()[0] if result.stdout else result.stderr.splitlines()[0] if result.stderr else "?"
        print(f"  ok  {name:<10} {version}")

    rules_dir = Path("/etc/udev/rules.d")
    rules = ""
    if rules_dir.is_dir():
        for path in rules_dir.glob("*.rules"):
            try:
                rules += path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
    for vendor, label in (("18d1", "Google/ADB (18d1)"), ("0e8d", "MediaTek (0e8d)")):
        if vendor in rules:
            print(f"  ok  udev       regel voor {label}")
        else:
            print(f"  ..  udev       geen regel voor {label} (alleen nodig bij toegang geweigerd)")

    usage = shutil.disk_usage(app.paths.home)
    free = usage.free / (1 << 30)
    print(f"  {'ok' if free >= MIN_FREE_GIB else '!!'}  ruimte     {free:.1f} GiB vrij in {app.paths.home}")
    if free < MIN_FREE_GIB:
        problems += 1

    for profile in app.profiles.values():
        if profile.source is None and not profile.requires_file:
            print(f"  ..  profiel    {profile.id} heeft geen bron en vraagt geen --file")

    print(f"\n{'geen blokkades' if problems == 0 else f'{problems} blokkade(s)'}")
    return 0 if problems == 0 else 1


def cmd_fetch(app: App, args: argparse.Namespace) -> int:
    profile = resolve(app.profiles, args.profile)
    if profile.source is None:
        die(f"profiel {profile.id} heeft geen bron; gebruik --file bij plan/flash")
    dest = app.paths.artifacts / profile.id
    print(f"{profile.id}: ophalen naar {dest}")
    try:
        fetched = fetch(profile.source, dest, log=print)
    except SourceError as exc:
        die(str(exc))
    entries = {item.filename: item.sha256 for item in fetched}
    path = write_manifest(dest, entries, header=f"profiel {profile.id} ({profile.codename})")
    for item in fetched:
        app.store.record_artifact(profile.id, item.filename, item.sha256, item.size, item.url)
    app.store.record_event(
        "fetch",
        f"{profile.id}: {len(fetched)} bestanden opgehaald",
        device=profile.codename,
        detail=str(path),
    )
    print(f"manifest: {path}")
    return 0


def cmd_verify(app: App, args: argparse.Namespace) -> int:
    profile = resolve(app.profiles, args.profile)
    directory = app.paths.artifacts / profile.id
    try:
        manifest = read_manifest(directory)
    except ArtifactError as exc:
        die(f"{exc}; haal eerst bestanden op met: flashkit fetch {profile.id}")
    problems = verify(directory, manifest)
    for filename in sorted(manifest):
        path = directory / filename
        if path.is_file():
            print(f"  ok  {filename:<44} {human_size(path.stat().st_size):>10}  {manifest[filename][:12]}…")
    for problem in problems:
        print(f"  !!  {problem.filename}: {problem.message}")
    print(f"\n{'alles klopt' if not problems else f'{len(problems)} probleem(en)'}")
    return 0 if not problems else 1


def cmd_backup(app: App, args: argparse.Namespace) -> int:
    if args.list:
        rows = [
            [
                stamp(row["at"]),
                row["device"],
                row["partition"],
                human_size(row["size"]),
                row["sha256"][:12] + "…",
                "ja" if Path(row["path"]).is_file() else "weg",
            ]
            for row in app.store.backups(args.limit)
        ]
        print(table(["wanneer", "toestel", "partitie", "grootte", "sha256", "bestaat"], rows) if rows else "geen backups")
        return 0

    device = _pick_device(app, args.serial)
    profile = _profile_for_device(app, device, args.profile)
    if not profile.backup.partitions:
        die(f"profiel {profile.id} noemt geen partities om te bewaren")

    print(f"backup van {profile.id} ({device.serial}, {device.mode})")
    try:
        results = create_backup(
            app.runner,
            device,
            profile,
            app.store,
            app.paths.backups,
            log=print,
        )
    except BackupError as exc:
        die(str(exc))
    directory = results[0].path.parent if results else app.paths.backups
    app.store.record_event(
        "backup",
        f"{profile.id}: {len(results)} partitie(s) bewaard",
        device=profile.codename,
        serial=device.serial,
        detail=str(directory),
    )
    print(f"\n{len(results)} partitie(s) bewaard in {directory}")
    return 0


def _plan_for(app: App, args: argparse.Namespace):
    profile = resolve(app.profiles, args.profile)
    device = _pick_device(app, args.serial)
    dest = app.paths.artifacts / profile.id
    file_path = Path(args.file).expanduser() if args.file else None
    if file_path is not None and not file_path.is_absolute():
        file_path = Path.cwd() / file_path
    try:
        plan = build_plan(profile, device, dest, app.store, file_path)
    except FlashError as exc:
        die(str(exc))
    except ArtifactError as exc:
        die(str(exc))
    return plan


def cmd_plan(app: App, args: argparse.Namespace) -> int:
    plan = _plan_for(app, args)
    payload = {
        "profile": plan.profile.id,
        "serial": plan.device.serial,
        "checks": [
            {"name": check.name, "ok": check.ok, "message": check.message, "blocking": check.blocking}
            for check in plan.checks
        ],
        "steps": [
            {"index": planned.index, "kind": planned.step.kind, "title": planned.title, "command": planned.command, "note": planned.note}
            for planned in plan.steps
        ],
    }
    if args.json:
        emit(payload, True)
        return 0 if not plan.blockers else 1
    _print_checks(plan.checks)
    _print_plan(plan)
    blockers = plan.blockers
    if blockers:
        print(f"\n{len(blockers)} blokkade(s); los die eerst op")
        return 1
    print("\nplan is uitvoerbaar")
    return 0


def cmd_flash(app: App, args: argparse.Namespace) -> int:
    plan = _plan_for(app, args)
    _print_checks(plan.checks)
    _print_plan(plan)

    if plan.blockers:
        print(f"\n{len(plan.blockers)} blokkade(s); er is niets geflasht")
        return 1
    if args.dry_run:
        print("\ndroog: niets uitgevoerd")
        return 0
    if not args.yes:
        die("dit schrijft naar het toestel; voeg --yes toe als je het plan hebt gecontroleerd", 2)

    log_path = new_log_path(app.paths.logs, plan.profile)
    handle = log_path.open("w", encoding="utf-8")

    def log(message: str) -> None:
        print(message)
        handle.write(message + "\n")
        handle.flush()

    log(f"flashkit {VERSION}  {stamp(int(time.time()))}")
    log(f"profiel {plan.profile.id} op {plan.device.serial} ({plan.device.codename})")
    log(f"{len(plan.steps)} stappen")
    try:
        outcome = execute(app.runner, app.prober, plan, log, interactive=not args.non_interactive)
    finally:
        handle.close()

    app.store.record_flash(
        plan.profile.codename,
        plan.profile.id,
        "ok" if outcome == 0 else "mislukt",
        len(plan.steps),
        log_path,
        serial=plan.device.serial,
    )
    app.store.record_event(
        "flash",
        f"{plan.profile.id}: {'klaar' if outcome == 0 else 'mislukt'}",
        device=plan.profile.codename,
        serial=plan.device.serial,
        ok=outcome == 0,
        detail=str(log_path),
    )
    print(f"\nlog: {log_path}")
    return outcome


def cmd_history(app: App, args: argparse.Namespace) -> int:
    rows = [
        [stamp(row["at"]), row["kind"], row["device"] or "-", "ok" if row["ok"] else "fout", row["message"]]
        for row in app.store.history(args.limit)
    ]
    print(table(["wanneer", "soort", "toestel", "status", "melding"], rows) if rows else "nog geen geschiedenis")
    if args.flashes:
        rows = [
            [stamp(row["at"]), row["profile"], row["outcome"], str(row["steps"]), row["log_path"]]
            for row in app.store.flashes(args.limit)
        ]
        print("\nflashes")
        print(table(["wanneer", "profiel", "uitkomst", "stappen", "log"], rows) if rows else "geen")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flashkit",
        description="Eén gereedschap voor meerdere Android-toestellen: profielen, checksums, backups en flashen.",
    )
    parser.add_argument("--version", action="version", version=f"flashkit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("profiles", help="toon de bekende toestelprofielen")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_profiles)

    p = sub.add_parser("scan", help="laat zien welke toestellen nu aangesloten zijn")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("status", help="lees een toestel uit zonder iets te wijzigen")
    p.add_argument("--serial")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("doctor", help="controleer adb, fastboot, udev, ruimte en profielen")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("fetch", help="haal de images van een profiel op en controleur ze")
    p.add_argument("profile")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("verify", help="controleer eerder opgehaalde images")
    p.add_argument("profile")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("backup", help="bewaar partities van een toestel met root")
    p.add_argument("profile", nargs="?")
    p.add_argument("--serial")
    p.add_argument("--list", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_backup)

    for name, handler, help_text in (
        ("plan", cmd_plan, "toon wat er zou gebeuren, zonder te schrijven"),
        ("flash", cmd_flash, "voer het plan uit (vraagt --yes)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("profile")
        p.add_argument("--serial")
        p.add_argument("--file", help="eigen image voor profielen met requires_file")
        p.add_argument("--non-interactive", action="store_true")
        if name == "plan":
            p.add_argument("--json", action="store_true")
        else:
            p.add_argument("--yes", action="store_true", help="bevestig dat je het plan hebt gezien")
            p.add_argument("--dry-run", action="store_true")
        p.set_defaults(func=handler)

    p = sub.add_parser("history", help="toon wat flashkit eerder deed")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--flashes", action="store_true")
    p.set_defaults(func=cmd_history)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return _run(args, Paths(default_home()))


def _run(args: argparse.Namespace, paths: Paths) -> int:
    paths.ensure()
    try:
        profiles = load_all(paths.profiles)
    except ProfileError as exc:
        die(str(exc), 2)
    with Store(paths.db) as store:
        runner = Runner()
        app = App(paths=paths, store=store, runner=runner, prober=Prober(runner), profiles=profiles)
        try:
            return int(args.func(app, args))
        except (ProfileError, BackupError, ArtifactError, SourceError, FlashError) as exc:
            die(str(exc))
        except KeyboardInterrupt:
            print("\nflashkit: gestopt", file=sys.stderr)
            return 130
        except BrokenPipeError:
            return 0
