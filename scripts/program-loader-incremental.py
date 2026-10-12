"""Checkpoint-only timing experiments; never run the Classic/sign-off flow.

Run with WSL Python. Each experiment gets a new directory and starts from the
same routed loader checkpoint. Original design artifacts are never overwritten.
"""
import argparse
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('mode', choices=['resize', 'buffer', 'wire300'])
parser.add_argument('--label', help='Fresh output directory name; existing results are never overwritten')
parser.add_argument('--work', type=Path, default=Path(
    '/home/administrator/builds/forg-loader-physical-20261012'))
args = parser.parse_args()
work = args.work.resolve()
run = work / 'src/runs/loader-physical'
label = args.label or args.mode
if Path(label).name != label or label in {'.', '..'}:
    parser.error('--label must be a single directory name')
out = work / 'incremental' / label
out.mkdir(exist_ok=False)
state = json.loads((run / '45-openroad-stapostpnr/state_out.json').read_text())
config = json.loads((run / 'resolved.json').read_text())
scripts = ('/nix/store/6a08ghhhzl5a07nr0ly762yh1ah6m5bj-python3-3.13.13-env/'
           'lib/python3.13/site-packages/librelane/scripts')
image = 'ghcr.io/librelane/librelane:3.1.0.dev3'
libs = '/tmp/forg-physical/pdk/ihp-sg13cmos5l/libs.ref'
corners = [('fast_1p32V_m40C', 'fast_1p32V_3p6V_m40C'),
           ('slow_1p08V_125C', 'slow_1p08V_3p0V_125C'),
           ('typ_1p20V_25C', 'typ_1p2V_3p3V_25C')]
tcl = [f'set ::env(SCRIPTS_DIR) {scripts}',
       f'set ::env(_TCL_ENV_IN) {run}/32-openroad-resizertimingpostgrt/_env.tcl',
       'set ::env(_PNR_EXCLUDED_CELLS) {}']
for i, (std, io) in enumerate(corners):
    tcl += [f'set ::env(_LIB_CORNER_{i}) [list nom_{std} '
            f'{libs}/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_{std}.lib '
            f'{libs}/sg13cmos5l_io/lib/sg13cmos5l_io_{io}.lib]']
tcl += ['source $::env(SCRIPTS_DIR)/openroad/common/io.tcl',
        f'set ::env(CURRENT_ODB) {state["odb"]}',
        f'set ::env(_SDC_IN) {state["sdc"]}', 'read_current_odb',
        'source $::env(SCRIPTS_DIR)/openroad/common/set_rc.tcl',
        'set sta_report_default_digits 6']
for std, _ in corners:
    tcl += [f'read_spef -corner nom_{std} {state["spef"]["nom_*"]}']
tcl += [f'set output_dir {out}', r'''
proc report_snapshot {label} {
    global output_dir
    foreach corner {nom_fast_1p32V_m40C nom_slow_1p08V_125C nom_typ_1p20V_25C} {
        report_checks -corner $corner -path_delay max -group_path_count 1 \
            -format full_clock_expanded -fields {slew cap input net fanout} \
            > $output_dir/$label-$corner-max.rpt
        report_check_types -corner $corner -max_slew -max_capacitance -max_fanout \
            -violators > $output_dir/$label-$corner-checks.rpt
        puts "SNAPSHOT $label $corner setup=[worst_slack -corner $corner -max] hold=[worst_slack -corner $corner -min]"
    }
}
report_snapshot baseline-extracted
remove_fillers
source $::env(SCRIPTS_DIR)/openroad/common/set_routing_layers.tcl
source $::env(SCRIPTS_DIR)/openroad/common/set_layer_adjustments.tcl
set_macro_extension $::env(GRT_MACRO_EXTENSION)

# Lock unchanged instances, as in LibreLane's official ECO-buffer step.
set saved_status [dict create]
set original_masters [dict create]
set original_locations [dict create]
foreach inst [$::block getInsts] {
    set name [$inst getName]
    dict set saved_status $name [$inst getPlacementStatus]
    dict set original_masters $name [[$inst getMaster] getName]
    dict set original_locations $name [$inst getLocation]
    $inst setPlacementStatus LOCKED
}
set touched_nets [dict create]
proc mark_instance_nets {inst} {
    global touched_nets
    foreach pin [$inst getITerms] {
        set net [$pin getNet]
        if {$net != "NULL" && [$net getSigType] ni {POWER GROUND}} {
            dict set touched_nets [$net getName] 1
        }
    }
}
global_route -start_incremental -allow_congestion
''']
if args.mode == 'resize':
    tcl += [r'''
set target [$::block findInst _20075_]
mark_instance_nets $target
$target setPlacementStatus PLACED
replace_cell _20075_ sg13cmos5l_a21oi_2
# Screening only: same wires/SPEF, before legalizing changed pin geometry.
report_snapshot resized-fixed-wires-screen
''']
elif args.mode == 'buffer':
    tcl += [r'''
dict set touched_nets _07797_ 1
insert_buffer -buffer_cell sg13cmos5l_buf_4 -net _07797_ \
    -location {506 555.66} -buffer_name eco_setup_buffer -net_name eco_setup_net
''']
else:
    tcl += [r'''
# Screen the proposed length repair on the existing global-route model.
# All original cells stay locked; only newly inserted buffers are legalized.
estimate_parasitics -global_routing
report_snapshot wire-limit-before-grt-estimate
repair_design -max_wire_length 300 -slew_margin 50 -cap_margin 0 -verbose
''']
tcl += [r'''
foreach inst [$::block getInsts] {
    set name [$inst getName]
    if {![dict exists $original_masters $name] ||
        [dict get $original_masters $name] != [[$inst getMaster] getName]} {
        mark_instance_nets $inst
        $inst setPlacementStatus PLACED
    }
}
detailed_placement
foreach inst [$::block getInsts] {
    set name [$inst getName]
    if {[dict exists $original_locations $name] &&
        [dict get $original_locations $name] != [$inst getLocation]} {
        mark_instance_nets $inst
    }
}
global_route -end_incremental -allow_congestion -verbose
# Explicitly invalidate only wires incident to modified/new/moved instances.
foreach name [dict keys $touched_nets] {
    set net [$::block findNet $name]
    if {$net != "NULL" && [$net getWire] != "NULL"} {
        odb::dbWire_destroy [$net getWire]
    }
}
set changed [open $output_dir/affected-nets.txt w]
puts $changed [join [lsort [dict keys $touched_nets]] "\n"]
close $changed
puts "ECO_AFFECTED_NETS [dict size $touched_nets]"
if {[dict size $touched_nets] > 1000} {
    error "Experiment exceeds incremental scope: stop without routing"
}
foreach inst [$::block getInsts] {
    set name [$inst getName]
    if {[dict exists $saved_status $name]} {
        $inst setPlacementStatus [dict get $saved_status $name]
    }
}
estimate_parasitics -global_routing
report_snapshot prepared-grt-estimate
source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl
write_db $output_dir/prepared.odb
write_def $output_dir/prepared.def
write_verilog $output_dir/prepared.nl.v
write_verilog -include_pwr_gnd $output_dir/prepared.pnl.v
''']
(out / 'prepare.tcl').write_text('\n'.join(tcl) + '\n')

commands = []

def execute(cmd, logfile):
    commands.append(cmd)
    (out / 'commands.json').write_text(json.dumps(commands, indent=2))
    with (out / logfile).open('w') as log:
        result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    (out / (logfile + '.exit-code')).write_text(str(result.returncode) + '\n')
    if result.returncode:
        print('\n'.join((out / logfile).read_text().splitlines()[-25:]))
        raise SystemExit(result.returncode)

docker = ['docker', 'run', '--rm', '-v', '/tmp/forg-physical:/tmp/forg-physical',
          '-v', f'{work}:{work}', image]
execute(docker + ['openroad', '-exit', '-threads', '2', str(out / 'prepare.tcl')],
        'prepare.log')
for key, suffix in [('odb', 'odb'), ('def', 'def'), ('nl', 'nl.v'), ('pnl', 'pnl.v')]:
    state[key] = str(out / f'prepared.{suffix}')
state.pop('spef', None)
state.pop('sdf', None)
state.pop('lib', None)
(out / 'initial-state.json').write_text(json.dumps(state, indent=2))
steps = ['OpenROAD.DetailedRouting', 'OpenROAD.FillInsertion',
         'OpenROAD.RCX', 'OpenROAD.STAPostPNR']
config['meta'] = {'version': 2, 'flow': steps}
config['DESIGN_DIR'] = str(out)
config['DRT_ANTENNA_REPAIR_ITERS'] = 0  # Keep the experiment limited to the ECO.
config['OPENROAD_THREADS'] = 2
(out / 'config.json').write_text(json.dumps(config, indent=2))
execute(docker + ['python3', '-m', 'librelane', '--manual-pdk', '--pdk',
        'ihp-sg13cmos5l', '--pdk-root', '/tmp/forg-physical/pdk',
        '--run-tag', 'eco', '--hide-progress-bar',
        '--with-initial-state', str(out / 'initial-state.json'),
        '--to', 'OpenROAD.STAPostPNR', str(out / 'config.json')], 'timing-flow.log')
print(f'Completed incremental experiment: {out}')
