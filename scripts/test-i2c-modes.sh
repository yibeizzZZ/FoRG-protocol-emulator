#!/usr/bin/env bash
# One focused RTL suite in an isolated snapshot of the current working files.
# Usage: test-i2c-modes.sh standard|fast [SCL-rise SCL-fall SDA-rise SDA-fall] [base|extended|smoke|ram|control|test-filter]
set -euo pipefail
mode=${1:-fast}
case "$mode" in standard|fast) ;; *) echo "Invalid mode: $mode" >&2; exit 2;; esac
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output=$(mktemp -d "${TMPDIR:-/tmp}/forg-i2c-$mode.XXXXXX")
echo "I2C audit outputs: $output"
mkdir -p "$output"/{test,scripts,src,firmware}
cp "$repo_dir"/test/*.py "$repo_dir"/test/*.v "$repo_dir/test/Makefile" "$output/test/"
cp "$repo_dir"/scripts/*.py "$output/scripts/"
cp "$repo_dir"/src/*.v "$output/src/"
cp "$repo_dir"/firmware/*.hex "$output/firmware/"
git -C "$repo_dir" rev-parse HEAD > "$output/base-commit.txt"
(cd "$output" && sha256sum src/*.v scripts/*.py test/*.py test/*.v test/Makefile firmware/*.hex) > "$output/source-sha256.txt"
export PYTHONDONTWRITEBYTECODE=1 I2C_MODE="$mode"
selection=${6:-}
if test -z "$selection"; then
    if test "$mode" = fast; then selection=base; else selection=control; fi
fi
case "$selection" in
    base) filter='[.]test_(?!fast_)'; expected=8 ;;
    extended) test "$mode" = fast; filter='test_fast_'; expected=4 ;;
    smoke) filter='test_(register|address)'; expected=2 ;;
    ram) filter='test_arbitrary'; expected=1 ;;
    control) filter='test_(timing|timeout|reset|reference|real_)'; expected=5 ;;
    *) filter="$selection"; expected=0 ;;
esac
printf '%s\n' "mode=$mode SCL-rise=${2:-0} SCL-fall=${3:-0} SDA-rise=${4:-0} SDA-fall=${5:-0} filter=$filter" > "$output/config.txt"
if ! (cd "$output/test" && make -B TARGET=i2c_master I2C_SCL_RISE_NS="${2:-0}" I2C_SCL_FALL_NS="${3:-0}" I2C_SDA_RISE_NS="${4:-0}" I2C_SDA_FALL_NS="${5:-0}" COCOTB_TEST_FILTER="'$filter'" > "$output/rtl.log" 2>&1); then
    test ! -f "$output/test/results.xml" || cp "$output/test/results.xml" "$output/results.xml"
    tail -n 65 "$output/rtl.log"
    exit 1
fi
cp "$output/test/results.xml" "$output/results.xml"
python "$output/scripts/check-test-results.py" "$output/results.xml"
python - "$output/results.xml" "$expected" <<'PY'
import sys
import xml.etree.ElementTree as ET
count = sum(case.find('skipped') is None for case in ET.parse(sys.argv[1]).iter('testcase'))
if int(sys.argv[2]):
    assert count == int(sys.argv[2]), f'Expected {sys.argv[2]} executed cases, got {count}'
PY
echo "Verified results retained in $output"
