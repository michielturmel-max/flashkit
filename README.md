# flashkit

Eén gereedschap voor meerdere Android-toestellen: elk toestel heeft een profiel
met de juiste images, partities en stappen. flashkit controleert eerst wie er
aan de kabel hangt, of de bootloader open is, of de checksums kloppen en of er
een backup is — en flasht pas daarna, stap voor stap.

Geen dependencies: python3 uit de standaardbibliotheek, adb en fastboot uit de
Android platform-tools. De geschiedenis staat in één SQLite-bestand.

## Downloaden en installeren

```bash
git clone https://github.com/michielturmel-max/flashkit.git
cd flashkit
./install.sh                 # flashkit in ~/.local/bin
flashkit doctor              # controleer adb, fastboot, udev, ruimte en profielen
```

Nodig: `python3` (standaardbibliotheek), `adb` en `fastboot` uit de Android
platform-tools, en een udev-regel zodat je telefoon zonder root bereikbaar is.
`flashkit doctor` vertelt wat er ontbreekt.

## Snel starten

```bash
./install.sh                 # flashkit in ~/.local/bin
flashkit doctor              # adb, fastboot, udev, ruimte, profielen
flashkit scan                # wat hangt er nu aan de kabel
flashkit status              # lees het toestel uit, wijzigt niets
```

Een toestel voorbereiden en flashen:

```bash
flashkit fetch komodo-lineage     # images ophalen en sha256 controleren
flashkit backup komodo-lineage    # boot en dtbo bewaren (vraagt root)
flashkit plan komodo-lineage      # laten zien wat er zou gebeuren
flashkit flash komodo-lineage --yes
```

`plan` en `flash` weigeren zolang een controle faalt. `flash` schrijft niets
zonder `--yes`.

## Commando's

| commando | wat het doet |
|---|---|
| `profiles` | toon de bekende toestelprofielen |
| `scan` | aangesloten toestellen met codename en profiel |
| `status [--serial S]` | read-only uitlezing en een oordeel |
| `doctor` | adb/fastboot, udev, vrije ruimte, profielen |
| `fetch <profiel>` | images ophalen volgens de bron van het profiel |
| `verify <profiel>` | sha256 van de opgehaalde images controleren |
| `backup [profiel]` | partities bewaren via adb + root |
| `backup --list` | eerdere backups |
| `plan <profiel>` | controles en stappen, zonder te schrijven |
| `flash <profiel> --yes` | het plan uitvoeren, met logbestand |
| `history [--flashes]` | wat flashkit eerder deed |

Elk commando met een toestel heeft `--serial`; zonder die optie weigert
flashkit bij meer dan één aangesloten toestel.

## Profielen

| id | toestel | bron | backup verplicht |
|---|---|---|---|
| `komodo-lineage` | Pixel 9 Pro XL | LineageOS 24.0 | nee (data wordt toch gewist) |
| `zeus-magisk` | Xiaomi 12 Pro | eigen `magisk_patched-*.img` | ja, `boot` |
| `lamu-lineage` | Moto G15 Power | LineageOS 23.2 | ja, `boot`/`lk`/`vbmeta` |
| `h815-boot-test` | LG G4 | eigen image, `fastboot boot` | ja, `boot` |
| `h815-flash-boot` | LG G4 | eigen image, `fastboot flash boot` | ja, `boot` |

`zeus-magisk` en de H815-profielen vragen `--file pad/naar/image.img`.
Een profiel is een JSON-bestand in `profiles/`; de stappen zijn
`fastboot`, `fastboot-boot`, `reboot`, `wait`, `sideload`, `manual` en
`verify`. `partition` mag `{slot}` bevatten; flashkit vult de actieve
A/B-slot in.

## Waar staat het

| pad | inhoud |
|---|---|
| `~/.local/share/flashkit/flashkit.db` | geschiedenis, backups, flashes |
| `~/.local/share/flashkit/artifacts/<profiel>/` | opgehaalde images + `manifest.txt` |
| `~/.local/share/flashkit/backups/<codename>/<tijd>/` | partitie-images + `meta.json` |
| `~/.local/share/flashkit/logs/` | één log per flash |

## Omgevingsvariabelen

| variabele | standaard | nut |
|---|---|---|
| `FLASHKIT_HOME` | `~/.local/share/flashkit` | alles hierboven verplaatsen |
| `FLASHKIT_PROFILES` | `profiles/` naast de bron | eigen profielmap |
| `FLASHKIT_ADB` | `adb` | ander pad naar adb |
| `FLASHKIT_FASTBOOT` | `fastboot` | ander pad naar fastboot |
| `FLASHKIT_TIMEOUT` | `60` | timeout per commando |
| `FLASHKIT_BOOT_TIMEOUT` | `300` | wachten op een herstart |
| `FLASHKIT_SIDELOAD_TIMEOUT` | `3600` | timeout voor sideload |
| `FLASHKIT_MIN_FREE_GIB` | `2` | waarschuwing in `doctor` |
| `FLASHKIT_LINEAGE_API` | LineageOS API | spiegel of testserver |

## Veiligheid

- `fetch` controleert elke sha256 uit de API; een download met een verkeerde
  hash wordt verwijderd. Een afgebroken download hervat bij de volgende poging.
- `backup` vergelijkt de sha256 van de kopie met `sha256sum` op het toestel.
  Zonder root of kloppende hash is er geen geldige backup.
- `flash` stopt bij de eerste fout, schrijft elke stap naar een logbestand en
  weigert bij een dicht bootloaderslot, een verkeerd codename of een
  ontbrekende verplichte backup.
- `manual`-stappen (data formatteren, recovery-menu) blijven handwerk; flashkit
  drukt af wat je moet doen en wacht.

## Ontwikkelen

```bash
python3 -m unittest discover -s test -v   # unittests
bash test/cli.test.sh                     # end-to-end met nep-adb/fastboot
./test/run-all.sh                         # beide
./dist.sh                                 # dist/flashkit-<versie>.tar.gz
```

De nep-tools staan in `test/fixtures/`; zij lezen `FAKE_*`-variabelen, zodat
de CLI-tests nooit een echte telefoon nodig hebben.

## Grenzen

- Alleen profielen die hier staan; een onbekend codename wordt niet geraden.
- Geen MediaTek BROM-flow: de Moto moet eerst met antumbra of mtkclient
  ontgrendeld worden. `flashkit` controleert daarna gewoon `unlocked: yes`.
- Geen fabrieksimages van Google of GrapheneOS; LineageOS komt uit zijn API,
  eigen images gaan via `--file`.
- Een backup via adb vraagt root of TWRP. Op een gesloten, niet-geroote
  telefoon kan flashkit dus niet de partities bewaren — en weigert dan ook
  profielen die een backup verplicht stellen.
- Op sommige toestellen verschillen de adb- en fastboot-serienummers (LG G4).
  Een plan in fastboot gebruikt het fastboot-serienummer; `--serial` matcht
  het nummer van de modus waarin het toestel nu staat.

## Licentie

MIT, zie [LICENSE](LICENSE).
