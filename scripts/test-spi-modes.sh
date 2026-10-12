#!/usr/bin/env bash
# Focused firmware/RTL verification in a fresh directory; never touches PnR.
# Requires Python with test/requirements.txt installed, Icarus and make in PATH.
# Usage: bash scripts/test-spi-modes.sh [mode0|mode1|mode2|mode3|compat|all]
set -euo pipefail
selection=${1:-all}
case "$selection" in mode[0-3]|compat|all) ;; *) echo "Invalid selection: $selection" >&2; exit 2;; esac
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output=$(mktemp -d "${TMPDIR:-/tmp}/forg-spi-modes.XXXXXX")
echo "SPI audit outputs: $output"
mkdir -p "$output"/{test,scripts,src,firmware}
# Copy current working files, including uncommitted changes, without old builds.
cp "$repo_dir"/test/*.py "$repo_dir"/test/*.v "$repo_dir/test/Makefile" "$output/test/"
cp "$repo_dir"/scripts/*.py "$output/scripts/"
cp "$repo_dir"/src/*.v "$output/src/"
cp "$repo_dir"/firmware/*.hex "$output/firmware/"
git -C "$repo_dir" rev-parse HEAD > "$output/base-commit.txt"
(cd "$output" && sha256sum src/*.v scripts/*.py test/*.py test/*.v test/Makefile firmware/*.hex) > "$output/source-sha256.txt"
export PYTHONDONTWRITEBYTECODE=1
run_rtl() {
    local name=$1 target=$2 filter=${3:-}
    if ! (cd "$output/test" && make -B TARGET="$target" COCOTB_TEST_FILTER="$filter" > "$output/$name.log" 2>&1); then
        test ! -f "$output/test/results.xml" || cp "$output/test/results.xml" "$output/$name.xml"
        tail -n 45 "$output/$name.log"
        return 1
    fi
    cp "$output/test/results.xml" "$output/$name.xml"
    if test -f "$output/test/tb.fst"; then mv "$output/test/tb.fst" "$output/$name.fst"; fi
    python "$output/scripts/check-test-results.py" "$output/$name.xml"
    if [[ "$name" == mode[0-3] ]]; then
        python - "$output/$name.xml" "${name#mode}" <<'PY'
import sys
import xml.etree.ElementTree as ET
cases = list(ET.parse(sys.argv[1]).iter("testcase"))
selected = [case for case in cases if case.find("skipped") is None]
assert len(selected) == 8, f"Expected eight selected SPI tests, got {len(selected)}"
assert all(case.attrib["name"].endswith("/mode=" + sys.argv[2]) for case in selected)
print(f"Confirmed exactly eight executed tests for mode {sys.argv[2]}")
PY
    fi
}
for mode in 0 1 2 3; do
    if [[ "$selection" == all || "$selection" == "mode$mode" ]]; then
        run_rtl "mode$mode" spi_firmware "mode=$mode"
    fi
done
if [[ "$selection" == all || "$selection" == compat ]]; then
    (cd "$output" && python -m unittest discover -s scripts -p 'test_*.py' -v > "$output/python.log" 2>&1) || {
        tail -n 45 "$output/python.log"; exit 1;
    }
    tail -n 4 "$output/python.log"
    run_rtl core core
    run_rtl uart_firmware uart_firmware
    run_rtl legacy legacy
fi
echo "Verified results retained in $output"
