#!/usr/bin/env bash
set -uo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/.." && pwd)"
BIN="$ROOT/bin/flashkit"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

export FLASHKIT_HOME="$WORK/state"
export FLASHKIT_MIN_FREE_GIB=0
export PATH="$WORK/bin:$PATH"
mkdir -p "$WORK/bin" "$FLASHKIT_HOME"
cp "$HERE/fixtures/fake-adb" "$WORK/bin/adb"
cp "$HERE/fixtures/fake-fastboot" "$WORK/bin/fastboot"
chmod +x "$WORK/bin/adb" "$WORK/bin/fastboot"

PROPS="$WORK/props"
cat >"$PROPS" <<'EOF'
[ro.product.device]: [zeus]
[ro.product.model]: [Xiaomi 12 Pro]
[ro.build.version.release]: [16]
[ro.boot.slot_suffix]: [_b]
[ro.boot.carrierid]: []
[ro.boot.flash.locked]: [0]
[ro.build.display.id]: [lineage_zeus-userdebug]
[ro.vendor.build.security_patch]: [2026-09-01]
EOF
export FAKE_PROPS="$PROPS"
export FAKE_OEM_ALLOWED=1
export FAKE_ROOT="uid=0(root) gid=0(root)"
export FAKE_BLOCKS=$'boot_a\nboot_b\ndtbo_a\ndtbo_b\n'
export FAKE_BLOCK_DATA="flashkit-test-block"
export FAKE_UNLOCK_ABILITY=1

pass=0
fail=0
ok() { pass=$((pass + 1)); printf 'ok   %s\n' "$*"; }
no() { fail=$((fail + 1)); printf 'FAIL %s\n' "$*"; }
assert_contains() {
  case "$1" in *"$2"*) ok "$3" ;; *) no "$3 (mist: $2)"; printf '%s\n' "$1" | sed 's/^/     /' ;; esac
}
assert_rc() {
  if [ "$1" -eq "$2" ]; then ok "$3"; else no "$3 (rc $1, verwacht $2)"; fi
}

fastboot_device() {
  export FAKE_ADB_DEVICES=""
  export FAKE_FASTBOOT_DEVICES=$'S1\tfastboot'
  export FAKE_FASTBOOT_VARS="product=zeus,serialno=S1,unlocked=yes,secure=no,current-slot=b,slot-count=2"
}

adb_device() {
  export FAKE_FASTBOOT_DEVICES=""
  export FAKE_ADB_DEVICES=$'S1  device usb:1-1 product:zeus model:Xiaomi_12_Pro device:zeus'
}

out=$("$BIN" profiles)
assert_contains "$out" "komodo-lineage" "profiles toont komodo-lineage"
assert_contains "$out" "zeus-magisk" "profiles toont zeus-magisk"

fastboot_device
out=$("$BIN" scan)
assert_contains "$out" "zeus" "scan herkent codename"
assert_contains "$out" "zeus-magisk" "scan koppelt een profiel"

out=$("$BIN" status --serial S1)
assert_contains "$out" "klaar" "status: open bootloader is klaar"
assert_contains "$out" "unlocked" "status toont fastboot-vars"

adb_device
out=$("$BIN" status --serial S1)
assert_contains "$out" "klaar" "status via adb met open bootloader"

sed 's/\[ro.boot.carrierid\]: \[\]/[ro.boot.carrierid]: [42]/' "$PROPS" >"$WORK/props-carrier"
FAKE_PROPS="$WORK/props-carrier" "$BIN" status --serial S1 >"$WORK/status-carrier"
out=$(cat "$WORK/status-carrier")
assert_contains "$out" "geblokkeerd" "carrier-id blokkeert ontgrendelen"

art="$FLASHKIT_HOME/artifacts/komodo-lineage"
mkdir -p "$art"
printf 'boot-image' >"$art/boot.img"
printf 'zip-data' >"$art/lineage-24.0-20261027-nightly-komodo-signed.zip"
(cd "$art" && sha256sum boot.img lineage-24.0-20261027-nightly-komodo-signed.zip >manifest.txt)
out=$("$BIN" verify komodo-lineage)
assert_contains "$out" "alles klopt" "verify keurt kloppende bestanden goed"
printf 'corrupt' >"$art/boot.img"
"$BIN" verify komodo-lineage >"$WORK/verify-bad" 2>&1
assert_rc "$?" 1 "verify faalt op een corrupt bestand"
printf 'boot-image' >"$art/boot.img"

adb_device
out=$("$BIN" backup zeus-magisk --serial S1)
assert_contains "$out" "1 partitie(s) bewaard" "backup bewaart boot"
out=$("$BIN" backup --list)
assert_contains "$out" "boot" "backup --list toont de partitie"

fastboot_device
printf 'patched' >"$WORK/magisk.img"
out=$("$BIN" plan zeus-magisk --file "$WORK/magisk.img")
assert_rc "$?" 0 "plan met backup en image is uitvoerbaar"
assert_contains "$out" "boot_b" "plan kiest de actieve slot"

out=$("$BIN" plan zeus-magisk 2>&1)
assert_rc "$?" 1 "plan zonder --file wordt geweigerd"
assert_contains "$out" "image" "plan noemt het ontbrekende image"

"$BIN" flash zeus-magisk --file "$WORK/magisk.img" >"$WORK/flash-noyes" 2>&1
assert_rc "$?" 2 "flash zonder --yes weigert"
assert_contains "$(cat "$WORK/flash-noyes")" "--yes" "weigering noemt --yes"

mkdir -p "$WORK/profiles" "$FLASHKIT_HOME/artifacts/simple"
cat >"$WORK/profiles/simple.json" <<'EOF'
{
  "id": "simple", "name": "Simpel", "codename": "zeus",
  "steps": [{"kind": "fastboot", "partition": "boot", "artifact": "boot.img"}]
}
EOF
printf 'simple-boot' >"$FLASHKIT_HOME/artifacts/simple/boot.img"
(cd "$FLASHKIT_HOME/artifacts/simple" && sha256sum boot.img >manifest.txt)
out=$(FLASHKIT_PROFILES="$WORK/profiles" "$BIN" flash simple --serial S1 --yes --non-interactive)
assert_rc "$?" 0 "flash voert een eenvoudig plan uit"
assert_contains "$out" "Klaar." "flash meldt klaar"

out=$(FLASHKIT_PROFILES="$WORK/profiles" "$BIN" history)
assert_contains "$out" "flash" "history onthoudt de flash"

out=$(FLASHKIT_PROFILES="$WORK/profiles" "$BIN" doctor)
assert_rc "$?" 0 "doctor zonder blokkades"
assert_contains "$out" "adb" "doctor ziet adb"

printf '\n%d ok, %d fail\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
