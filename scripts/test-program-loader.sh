#!/usr/bin/env bash
# Run in an environment with Icarus, make, Python and test/requirements.txt.
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
output="$repo_dir/build/program-loader/rtl"
mkdir -p "$output"
python -m unittest discover -s scripts -p 'test_*.py' > "$output/python.log" 2>&1
cd test
run_test() {
    local name=$1
    shift
    if ! make -B "$@" > "$output/$name.log" 2>&1; then
        tail -n 30 "$output/$name.log"
        return 1
    fi
    cp results.xml "$output/$name.xml"
    python ../scripts/check-test-results.py "$output/$name.xml"
}
for target in core uart_firmware spi_firmware legacy; do
    run_test "$target" TARGET="$target"
done
for divisor in 1 2 4 17 434; do
    run_test "uart-$divisor" TARGET=uart UART_CLKS_PER_BIT="$divisor"
done
for target in i2c_firmware i2c_master; do
    for delays in '0 0 0' '0 300 0' '1000 0 300'; do
        read -r rise scl_fall sda_fall <<< "$delays"
        run_test "$target-$rise-$scl_fall-$sda_fall" TARGET="$target" \
            I2C_RISE_NS="$rise" I2C_SCL_FALL_NS="$scl_fall" I2C_SDA_FALL_NS="$sda_fall"
    done
done
echo "All regressions passed. Reports: $output"
