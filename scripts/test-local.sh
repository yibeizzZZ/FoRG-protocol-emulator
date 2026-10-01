#!/usr/bin/env bash
# Run from any directory, including checkout paths containing spaces.
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v uv >/dev/null
command -v iverilog >/dev/null
run_dir="$(mktemp -d /tmp/forg-sim.XXXXXX)"
trap 'rm -rf "$run_dir"' EXIT
ln -s "$repo_dir" "$run_dir/repo"
uv venv --python 3.11 "$run_dir/venv"
uv pip install --python "$run_dir/venv/bin/python" -r "$repo_dir/test/requirements.txt"
export PATH="$run_dir/venv/bin:$PATH"
export LIBPYTHON_LOC="$("$run_dir/venv/bin/python" -m find_libpython)"
cd "$run_dir/repo/test"
make -B "PWD=$run_dir/repo/test" "$@"
python - <<'PY'
import xml.etree.ElementTree as ET
root = ET.parse("results.xml").getroot()
cases = list(root.iter("testcase"))
failures = list(root.iter("failure")) + list(root.iter("error"))
if not cases or failures:
    raise SystemExit("Simulation failed or no test cases were reported")
print(f"Verified results.xml: {len(cases)} test(s), no failures/errors")
PY
