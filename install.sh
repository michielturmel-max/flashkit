#!/usr/bin/env bash
# Installatie van flashkit op deze machine. Standaard alleen de CLI; udev is
# een aparte stap omdat daar sudo voor nodig is.
#
#   install     (standaard) leg flashkit in ~/.local/bin
#   udev        installeer regels voor Google (18d1) en MediaTek (0e8d)
#   version     toon de versie van deze map en van de CLI
#   uninstall   verwijder de CLI-symlink; data en backups blijven staan
set -euo pipefail

SRC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
UDEV_RULE="/etc/udev/rules.d/60-flashkit-android.rules"

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
fail() { printf 'installer: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
gebruik: ./install.sh [commando]

commando:
  install     (standaard) leg flashkit in ~/.local/bin
  udev        installeer udev-regels voor Android en MediaTek (sudo)
  version     toon gebundelde en geïnstalleerde versie
  uninstall   verwijder de CLI-symlink
  help        dit overzicht
EOF
}

bundled_version() {
  python3 -c "import json;print(json.load(open('$SRC_DIR/manifest.json'))['version'])"
}

install_cli() {
  command -v python3 >/dev/null 2>&1 || fail "python3 ontbreekt"
  mkdir -p "$BIN_DIR"
  ln -sfn "$SRC_DIR/bin/flashkit" "$BIN_DIR/flashkit"
  say "flashkit staat in $BIN_DIR/flashkit"
  info "in deze sessie nog niet gevonden? hash -r"
  if command -v adb >/dev/null 2>&1 && command -v fastboot >/dev/null 2>&1; then
    info "volgende stap: flashkit doctor"
  else
    info "adb en/of fastboot ontbreken; installeer android-tools"
  fi
}

install_udev() {
  command -v sudo >/dev/null 2>&1 || fail "sudo ontbreekt"
  say "udev-regels naar $UDEV_RULE"
  sudo tee "$UDEV_RULE" >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="0e8d", MODE="0666", TAG+="uaccess"
EOF
  sudo udevadm control --reload-rules
  sudo udevadm trigger
  info "regels actief"
}

uninstall() {
  rm -f "$BIN_DIR/flashkit"
  say "CLI verwijderd"
  info "data, images en backups blijven in ~/.local/share/flashkit"
  info "udev-regel verwijderen: sudo rm $UDEV_RULE"
}

version() {
  say "Versie-overzicht"
  info "gebundeld:      $(bundled_version)"
  if command -v flashkit >/dev/null 2>&1; then
    info "geïnstalleerd:  $(flashkit --version)"
  else
    info "geïnstalleerd:  niet gevonden in PATH"
  fi
}

case "${1:-install}" in
  install|"") install_cli ;;
  udev) install_udev ;;
  version|info) version ;;
  uninstall|remove|rm) uninstall ;;
  help|-h|--help) usage ;;
  *) fail "onbekend commando '$1' (zie: ./install.sh help)" ;;
esac
