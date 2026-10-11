#!/usr/bin/env bash
# User-operated validation of the existing, immutable WSL checkpoint.
# No background jobs, polling, retries, constraint edits, commits, or pushes.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
physical=/tmp/forg-physical
design="$physical/mem2reg-synth"
candidate="$design/src/runs/grt-margin6-wire300"
run_id=manual-i2c-wire300-v2
work="$physical/$run_id"
route_run="$design/src/runs/$run_id-route"
check_run="$design/src/runs/$run_id-timing"
signoff_run="$design/src/runs/$run_id-signoff"
top=tt_um_forg_protocol_engine
phase=${1:-help}

if [[ "$phase" == help || "$phase" == --help ]]; then
    echo "Usage: bash scripts/i2c-validation-handoff.sh {route|signoff|precheck|gates}"
    echo "Read docs/i2c-validation-handoff.md first. Each phase is foreground and user-operated."
    exit 0
fi
case "$phase" in route|signoff|precheck|gates) ;; *) echo "Unknown phase: $phase" >&2; exit 2 ;; esac

# This is a checkpoint handoff, not a fresh-build script. Never silently test
# stale RTL if the checkout or the saved source has subsequently changed.
for source_dir in "$repo_dir/src" "$design/src"; do
    printf '%s  %s\n' \
        cc05e92b2cbcc99d535ab45bad5c31d26e86d3d621e2fd98400d065297fc4c4a "$source_dir/protocol_engine.v" \
        b76601cf1d772f4ced8cdffb52ba8517d689e2a5a13759d7fff5ff9ff1b80c14 "$source_dir/protocol_top.v" |
        sha256sum --check --status
done
test -f "$candidate/04-openroad-resizertimingpostgrt/state_out.json"
mkdir -p "$work"

logged() {
    local log=$1
    shift
    echo "Running in foreground; log: $log"
    if "$@" >"$log" 2>&1; then
        echo "Command completed: $log"
    else
        local status=$?
        tail -n 25 "$log" >&2
        return "$status"
    fi
}

state_file() {
    python3 - "$1" "$2" <<'PY'
import pathlib, sys
matches = list(pathlib.Path(sys.argv[1]).glob(f"*-{sys.argv[2]}/state_out.json"))
if len(matches) != 1:
    raise SystemExit(f"Expected one completed {sys.argv[2]} state, found {len(matches)}")
print(matches[0])
PY
}

ll() {
    (
        cd "$design"
        "$physical/venv311/bin/python" -m librelane \
            --docker-mount "$physical" --docker-no-tty --dockerized \
            --manual-pdk --pdk ihp-sg13cmos5l --pdk-root "$physical/pdk" \
            --hide-progress-bar --override-config OPENROAD_THREADS=8 \
            --override-config RUN_POST_GRT_DESIGN_REPAIR=true \
            --override-config RUN_POST_GRT_RESIZER_TIMING=true \
            --override-config GRT_RESIZER_SETUP_SLACK_MARGIN=6.0 \
            --override-config GRT_DESIGN_REPAIR_MAX_SLEW_PCT=50 \
            --override-config GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH=300 \
            "$@" src/config_merged.json
    )
}

case "$phase" in
route)
    # A failed or completed phase is never automatically rerun/overwritten.
    test ! -e "$route_run"
    test ! -e "$check_run"
    cp "$candidate/resolved.json" "$work/input-resolved.json"
    # STAMidPNR occurs four times; its unsuffixed ID resumes before CTS.
    # This checkpoint already has CTS and global routing. Use the unique next
    # routing step, preserving extracted STA and all downstream checkers.
    logged "$work/route.log" ll --run-tag "$run_id-route" \
        --from OpenROAD.DetailedRouting --to OpenROAD.STAPostPNR \
        --with-initial-state "$candidate/04-openroad-resizertimingpostgrt/state_out.json"
    sta_state=$(state_file "$route_run" openroad-stapostpnr)
    # Ending at STA alone does NOT enforce timing. Run the four static checkers.
    logged "$work/timing-check.log" ll --run-tag "$run_id-timing" \
        --from Checker.SetupViolations --to Checker.MaxCapViolations \
        --with-initial-state "$sta_state"
    sha256sum "$sta_state" > "$work/timing-ok.sha256"
    cat "$(dirname "$sta_state")/summary.rpt"
    ;;
signoff)
    sha256sum --check --status "$work/timing-ok.sha256"
    test ! -e "$signoff_run"
    sta_state=$(state_file "$route_run" openroad-stapostpnr)
    logged "$work/signoff.log" ll --run-tag "$run_id-signoff" \
        --from OpenROAD.IRDropReport --with-initial-state "$sta_state"
    touch "$work/signoff-ok"
    echo "GDS, LEF, netlists and metrics: $signoff_run/final"
    ;;
precheck)
    test -f "$work/signoff-ok"
    test ! -e "$work/precheck"
    python3 - "$physical" "$design" "$signoff_run" "$route_run" "$work" "$top" <<'PY'
import json, pathlib, shutil, sys
physical, design, signoff, route, work = map(pathlib.Path, sys.argv[1:6])
top = sys.argv[6]
def one(pattern):
    found = list(signoff.glob(pattern))
    if len(found) != 1:
        raise SystemExit(f"Expected one {pattern}: {found}")
    return found[0]
shutil.copytree(physical / 'tt/precheck', work / 'precheck',
                ignore=shutil.ignore_patterns('reports', '__pycache__'))
# The upstream runner and KLayout require this directory to exist. Exclude
# stale reports above, then recreate their empty destination before any checks.
(work / 'precheck/reports').mkdir()
(work / 'tech').symlink_to(physical / 'tt/tech', target_is_directory=True)
submission = work / 'submission'
submission.mkdir()
shutil.copy2(design / 'info.yaml', submission / 'info.yaml')
state = json.loads(next(route.glob('*-openroad-stapostpnr/state_out.json')).read_text())
# Precheck requires power ports; functional gate simulation below uses nl instead.
for suffix, source in [('gds', one(f'*-klayout-streamout/{top}.gds')),
                       ('lef', one(f'*-magic-writelef/{top}.lef')),
                       ('v', pathlib.Path(state['pnl']))]:
    (submission / f'{top}.{suffix}').symlink_to(source)
PY
    cd "$work/precheck"
    logged "$work/precheck.log" env PATH="$physical/bin:$PATH" \
        PDK_ROOT="$physical/pdk" PDK=ihp-sg13cmos5l \
        "$physical/precheck-venv/bin/python" precheck.py \
        --gds "$work/submission/$top.gds" --tech ihp-sg13cmos5l
    echo "Precheck reports: $work/precheck/reports"
    ;;
gates)
    sha256sum --check --status "$work/timing-ok.sha256"
    test ! -e "$work/gates"
    sta_state=$(state_file "$route_run" openroad-stapostpnr)
    python3 - "$repo_dir" "$work" "$sta_state" <<'PY'
import json, pathlib, shutil, sys
repo, work, state_path = map(pathlib.Path, sys.argv[1:])
netlist = pathlib.Path(json.loads(state_path.read_text())['nl'])
for target in ('core', 'spi_firmware', 'i2c_master'):
    root = work / 'gates' / target
    test = root / 'test'
    test.mkdir(parents=True)
    for name in ('src', 'scripts', 'firmware'):
        (root / name).symlink_to(repo / name, target_is_directory=True)
    for source in (repo / 'test').iterdir():
        if source.name != 'gate_level_netlist.v' and source.is_file() and (
                source.suffix in ('.py', '.v') or source.name == 'Makefile'):
            shutil.copy2(source, test / source.name)
    shutil.copy2(netlist, test / 'gate_level_netlist.v')
PY
    export PATH="/tmp/forg-regression/venv/bin:$PATH"
    export LIBPYTHON_LOC
    LIBPYTHON_LOC=$(python -m find_libpython)
    unset COCOTB_TEST_FILTER COCOTB_TESTCASE TESTCASE
    for target in core spi_firmware i2c_master; do
        cd "$work/gates/$target/test"
        logged "$work/gates/$target.log" make -B TARGET="$target" \
            GATES=yes PDK_ROOT="$physical/pdk"
        python "$repo_dir/scripts/check-test-results.py" results.xml
    done
    ;;
esac
