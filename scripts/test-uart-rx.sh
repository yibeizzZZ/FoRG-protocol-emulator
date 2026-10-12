#!/usr/bin/env bash
# Isolated RTL snapshot. Usage: test-uart-rx.sh [uart_rx|uart_firmware|core|loader] [payloads|timing|control|filter]
set -euo pipefail
target=${1:-uart_rx}
case "$target" in uart_rx|uart_firmware|core|loader) ;; *) exit 2;; esac
filter=${2:-}
expected=0
if test "$target" = uart_rx; then
    case "${2:-control}" in
        payloads) filter='test_payloads'; expected=1 ;;
        timing) filter='test_(baud|sampling)'; expected=3 ;;
        control) filter='test_(count|framing|false|reset|checked|initial|real_)'; expected=7 ;;
    esac
elif test -z "$filter" && test "$target" = uart_firmware; then
    expected=5
fi
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output=$(mktemp -d "${TMPDIR:-/tmp}/forg-uart-rx.XXXXXX")
echo "UART audit outputs: $output"
mkdir -p "$output"/{test,scripts,src,firmware}
cp "$repo_dir"/test/*.py "$repo_dir"/test/*.v "$repo_dir/test/Makefile" "$output/test/"
cp "$repo_dir"/scripts/*.py "$output/scripts/"
cp "$repo_dir"/src/*.v "$output/src/"
cp "$repo_dir"/firmware/*.hex "$output/firmware/"
git -C "$repo_dir" rev-parse HEAD > "$output/base-commit.txt"
(cd "$output" && sha256sum src/*.v scripts/*.py test/*.py test/*.v test/Makefile firmware/*.hex) > "$output/source-sha256.txt"
printf '%s\n' "target=$target filter=$filter" > "$output/config.txt"
export PYTHONDONTWRITEBYTECODE=1
if ! (cd "$output/test" && make -B TARGET="$target" COCOTB_TEST_FILTER="'$filter'" > "$output/rtl.log" 2>&1); then
    test ! -f "$output/test/results.xml" || cp "$output/test/results.xml" "$output/results.xml"
    tail -n 55 "$output/rtl.log"
    exit 1
fi
cp "$output/test/results.xml" "$output/results.xml"
python "$output/scripts/check-test-results.py" "$output/results.xml"
python - "$output/results.xml" "$expected" <<'PY'
import sys
import xml.etree.ElementTree as ET
executed = [case for case in ET.parse(sys.argv[1]).iter('testcase') if case.find('skipped') is None]
if int(sys.argv[2]):
    assert len(executed) == int(sys.argv[2]), f'Expected {sys.argv[2]} tests, got {len(executed)}'
PY
echo "Verified results retained in $output"
