# flashkit AGENTS.md

Werktijdregels voor dit project. Kort, want elke regel kost context.

## Bouwen en testen

- `./test/run-all.sh` draait de unittests en de CLI-tests.
- Los: `python3 -m unittest discover -s test -v`, `bash test/cli.test.sh`.
- CLI-tests gebruiken `test/fixtures/fake-adb` en `fake-fastboot` via `PATH`;
  nooit een echte telefoon of netwerk nodig.
- Live bron controleren zonder te downloaden: `python3 -c` met
  `flashkit.sources.select_build` op de LineageOS API.
- Pakket: `./dist.sh` → `dist/flashkit-<versie>.tar.gz`.

## Stijl

- Python: alleen de standaardbibliotheek, `from __future__ import annotations`,
  type-hints op functies. Nederlands voor teksten die een mens leest, Engels
  voor identifiers. Geen comments die de regel erboven herhalen.
- Commando's: argv-lijsten, nooit een shell-string. `Runner` vertaalt de
  logische naam `adb`/`fastboot` naar het ingestelde pad. De enige uitzondering
  is `su -c '…'`: die gaat als één token met quotes naar `adb shell`, anders
  splitst de device-shell het commando.
- Uitvoer: tabellen met `table()`, fouten met `flashkit: ...` op stderr en
  exitcode 1; een geweigerde bevestiging is exitcode 2.
- Nieuwe profielen zijn JSON in `profiles/` en komen met een test die het
  profiel laadt.

## Regels voor dit domein

- Lees eerst, schrijf later: `scan` en `status` mogen niets wijzigen.
- Weiger bij meer dan één toestel zonder `--serial`; raad nooit een codename.
- Een commando dat schrijft vraagt `--yes`. `plan` en `--dry-run` schrijven
  niets, ook niet naar de database.
- Een image zonder kloppende sha256 is geen image. `fetch` verwijdert een
  mislukte download; `flash` weigert zonder `manifest.txt`.
- Een backup telt alleen als het bestand bestaat én de sha256 op het toestel
  gelijk is. Verwijderde of gewijzigde backups blokkeren een flash.
- Stop bij de eerste fout. Elke flash krijgt een logbestand en een rij in
  `flashes`; een mislukte flash blijft zichtbaar in `history`.
- Geen tweede waarheid over de actieve slot: lees `current-slot` (fastboot) of
  `ro.boot.slot_suffix` (adb).

## Grenzen

- Niets installeren, udev aanpassen of flashen zonder dat de mens daarom
  vraagt. `install.sh` en `flash` zijn de laatste stap van een mens.
- Geen BROM-, mtkclient- of fabrieksimage-flow in dit gereedschap.
- De database en backups horen bij de machine: `FLASHKIT_HOME` voor testen.
