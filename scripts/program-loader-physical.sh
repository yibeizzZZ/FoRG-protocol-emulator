#!/usr/bin/env bash
# Authorized physical validation of the unchanged Program Loader RTL.
# Requires the local pinned WSL tools/checkpoint; no automatic retries.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tools=/tmp/forg-physical
prior="$tools/runtime-loader"
work=${FORG_PHYSICAL_WORK:-/home/administrator/builds/forg-loader-physical-20261012}
test ! -e "$work"
test -f "$prior/src/runs/loader-synth/12-openroad-staprepnr/state_out.json"
cmp "$repo/src/protocol_engine.v" "$prior/src/protocol_engine.v"
cmp "$repo/src/protocol_top.v" "$prior/src/protocol_top.v"
mkdir -p "$work/src" "$work/tmp"
cp "$repo/src/"*.v "$work/src/"
cp "$repo/src/config.json" "$work/src/config.json"
cp "$repo/info.yaml" "$work/info.yaml"
cp "$prior/src/config_merged.json" "$work/src/config_merged.json"
cp -r "$prior/src/runs/loader-synth" "$work/synthesis-evidence"
cp "$0" "$work/run-physical.sh"
ln -s "$tools/tt" "$work/tt"
python3 - "$repo" "$work" <<'PY'
import json, pathlib, sys
repo, work = map(pathlib.Path, sys.argv[1:])
config = json.loads((work / 'src/config_merged.json').read_text())
source = json.loads((repo / 'src/config.json').read_text())
assert all(config.get(k) == v for k, v in source.items()), 'Configuration changed since synthesis'
assert config['CLOCK_PERIOD'] == 20
assert config['DIE_AREA'] == '0 0 1289.28 710.64'
state = json.loads((work / 'synthesis-evidence/12-openroad-staprepnr/state_out.json').read_text())
for key in ('nl', 'json_h'):
    old = pathlib.Path(state[key])
    relative = old.relative_to('/tmp/forg-physical/runtime-loader/src/runs/loader-synth')
    state[key] = str(work / 'synthesis-evidence' / relative)
    assert pathlib.Path(state[key]).is_file()
(work / 'initial-state.json').write_text(json.dumps(state, indent=2))
PY
git -C "$repo" rev-parse HEAD > "$work/source-commit.txt"
git -C "$tools/tt" rev-parse HEAD > "$work/tt-revision.txt"
docker image inspect ghcr.io/librelane/librelane:3.1.0.dev3 > "$work/docker-image.json"
cd "$work"
sha256sum src/*.v src/config*.json info.yaml > input-sha256.txt
export TMPDIR="$work/tmp"
set +e
"$tools/venv311/bin/python" -m librelane \
    --docker-mount "$tools" --docker-mount "$work" --docker-no-tty --dockerized \
    --manual-pdk --pdk ihp-sg13cmos5l --pdk-root "$tools/pdk" \
    --run-tag loader-physical --hide-progress-bar --override-config OPENROAD_THREADS=8 \
    --from OpenROAD.Floorplan --with-initial-state "$work/initial-state.json" \
    src/config_merged.json > "$work/physical.log" 2>&1
result=$?
set -e
printf '%s\n' "$result" > "$work/physical-exit-code.txt"
tail -n 35 "$work/physical.log"
exit "$result"
