#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

python3 -m unittest discover -s "$HERE" -v
bash "$HERE/cli.test.sh"
