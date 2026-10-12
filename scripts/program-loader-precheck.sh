#!/usr/bin/env bash
# Independent TT precheck, only against this loader run's physical artifacts.
set -euo pipefail
tools=/tmp/forg-physical
work=${FORG_PHYSICAL_WORK:-/home/administrator/builds/forg-loader-physical-20261012}
run="$work/src/runs/loader-physical"
test ! -e "$work/precheck"
python3 - "$work" "$tools" "$run" <<'PY'
import json, pathlib, shutil, sys
work, tools, run = map(pathlib.Path, sys.argv[1:])
top = 'tt_um_forg_protocol_engine'
def one(pattern):
    paths = list(run.glob(pattern))
    if len(paths) != 1:
        raise SystemExit(f'Expected one completed artifact {pattern}: {paths}')
    return paths[0]
gds = one(f'*-klayout-streamout/{top}.gds')
lef = one(f'*-magic-writelef/{top}.lef')
assert (gds.parent / 'state_out.json').is_file(), 'GDS stage is incomplete'
assert (lef.parent / 'state_out.json').is_file(), 'LEF stage is incomplete'
state = json.loads(one('*-openroad-stapostpnr/state_out.json').read_text())
pnl = pathlib.Path(state['pnl'])
assert pnl.is_file()
shutil.copytree(tools / 'tt/precheck', work / 'precheck',
                ignore=shutil.ignore_patterns('reports', '__pycache__'))
(work / 'precheck/reports').mkdir()
(work / 'tech').symlink_to(tools / 'tt/tech', target_is_directory=True)
(work / 'submission').mkdir()
shutil.copy2(work / 'info.yaml', work / 'submission/info.yaml')
for ext, source in [('gds', gds), ('lef', lef), ('v', pnl)]:
    shutil.copy2(source, work / 'submission' / f'{top}.{ext}')
PY
mkdir -p "$work/bin"
cat > "$work/bin/klayout" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec docker run --rm -v /tmp/forg-physical:/tmp/forg-physical \
    -v "$FORG_PHYSICAL_WORK:$FORG_PHYSICAL_WORK" -w "$PWD" \
    ghcr.io/librelane/librelane:3.1.0.dev3 klayout "$@"
SH
chmod +x "$work/bin/klayout"
export FORG_PHYSICAL_WORK="$work" TMPDIR="$work/tmp"
cd "$work/precheck"
set +e
env PATH="$work/bin:/usr/bin:/bin" PDK_ROOT="$tools/pdk" PDK=ihp-sg13cmos5l \
    "$tools/precheck-venv/bin/python" precheck.py \
    --gds "$work/submission/tt_um_forg_protocol_engine.gds" --tech ihp-sg13cmos5l \
    > "$work/precheck.log" 2>&1
result=$?
set -e
printf '%s\n' "$result" > "$work/precheck-exit-code.txt"
tail -n 25 "$work/precheck.log"
exit "$result"
