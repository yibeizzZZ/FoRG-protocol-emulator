# Program Loader incremental timing ECOs — 2026-10-12

The original routed design misses slow-corner setup by 0.419801 ns. Both small
checkpoint-based ECOs pass extracted setup timing at the unchanged 20 ns
target. This does **not** constitute physical sign-off: slew violations remain,
and the user explicitly excluded Magic DRC, LVS, precheck and GDS generation.

## Stopping the earlier checks

The original workspace remains intact:
`/home/administrator/builds/forg-loader-physical-20261012` (`WORK`).
The original physical run is `WORK/src/runs/loader-physical` (`BASE`).

Magic DRC and the LibreLane scheduler exited after signals. The precheck Python
parent also exited. KLayout caught TERM and INT but continued computing; it was
then **suspended**, not forcibly killed, with SIGSTOP. Its PID/PGID is 3108098,
container `c6b3da517ad9`. It cannot progress while suspended. No original GDS,
SPEF, ODB, logs or reports were removed. Full-flow exit code 1 records cancellation,
not a completed DRC result; precheck has no completed exit-code record.

Successful stop commands from PowerShell:

```powershell
wsl -d Ubuntu -u root -- /bin/kill -s TERM -- -3092833 -3107853 -3108098
wsl -d Ubuntu -u root -- /bin/kill -s INT -- -3092833 -3108098
wsl -d Ubuntu -u root -- /bin/kill -s STOP -- -3108098
```

The initial `sudo -n` TERM attempt failed because sudo required authentication;
it sent no signals. No SIGKILL was issued. `WORK/incremental/stop-record.txt`
records the stop, and `ps` confirmed KLayout's stopped `Tsl` state.

## Final stop request

The wire300 run had already completed when the stop signal was attempted.
Its log records `Flow complete` at 2026-10-12 05:41:41 UTC and its
`timing-flow.log.exit-code` is 0. The following command returned
`No such process` for both process groups; no live wire300 OpenROAD/STA or
runner processes remained in the subsequent process inventory:

```powershell
wsl -d Ubuntu -u root -- /bin/kill -s TERM -- -3137738 -3131754
wsl -d Ubuntu -- ps -eo pid,ppid,pgid,stat,args
```

This is a completed existing experiment, not a cancelled or restarted one.
Only existing reports were read to finalize this comparison. No new experiment,
physical flow, DRC, LVS, precheck or GDS generation was started after this request.
The earlier KLayout process remains suspended (`Tsl`); it was not resumed.

## Incremental method and scope

All experiments independently load the original stage-43 routed ODB and stage-42
SDC from `BASE/45-openroad-stapostpnr/state_out.json`. Reading the original SPEF
reproduced -0.419801 ns before each modification.

The installed OpenROAD/LibreLane supports ECO routing. An explicit
`detailed_route -incremental` switch is not required: retained detailed wires are
reused, while modified nets are routed again. This is also the mechanism
documented in [LibreLane's official ECO guide](https://librelane.readthedocs.io/en/stable/usage/using_ecos.html)
and its installed `eco_buffer.py` implementation.

The script removes fillers from an in-memory copy, locks original instances,
applies the ECO, legalizes changed/new cells, updates global routing incrementally,
and invalidates detailed wires only on affected nets. It then runs exactly:

```text
OpenROAD.DetailedRouting
OpenROAD.FillInsertion
OpenROAD.RCX
OpenROAD.STAPostPNR
```

No synthesis, floorplan, global placement, CTS, Magic, LVS, KLayout precheck or GDS
stage is included. Automatic post-DRT antenna repair is disabled for these bounded
experiments; antenna sign-off is consequently not claimed. Clock constraints,
RTL, ISA and tile dimensions are unchanged. The new flow configurations and
databases live outside the original physical run.

The resize experiment retained 22,478 of the original 22,482 wired nets and
invalidated only `_03918_`, `_03956_`, `_03995_`, `_03997_`. The buffer experiment
changes two nets. Netlist comparison found only the intended functional changes
plus local filler/decap changes; see `WORK/incremental/netlist-delta.json`.

## Results

All values below are **freshly extracted** slow-library / nominal-RC results at
1.08 V, 125 C. Positive numbers are worst setup slack, not negative-slack-clamped
WNS values.

| Experiment | Functional change | Affected nets | Worst setup slack (ns) | Setup failing endpoints | Slow slew violations | Max-cap violations |
|---|---|---:|---:|---:|---:|---:|
| Original | None | — | **-0.419801** | 8 | 57 | 0 |
| Resize | `_20075_`: `a21oi_1` → `a21oi_2` | 4 | **+0.176621** | 0 | 49 | 0 |
| Target buffer | One `buf_4` near `_24534_/Y`, driving `_07797_` | 2 | **+0.615538** | 0 | 57 | 0 |
| 300 um wire repair | 567 buffers across 363 original nets | 930 | **+1.980249** | 0 | 0 | 0 |

For the resize candidate, fast/typical setup slack is +8.631764/+6.603163 ns;
worst hold slack over all three corners is +0.131007 ns. For the buffer candidate,
fast/typical setup slack is +8.631585/+6.603690 ns and worst hold slack is
+0.131007 ns. Both have zero setup, hold and max-cap violations at every configured
corner, and zero filtered unannotated timing nets. Both retain 192 max-fanout
violations (178 clock-tree drivers and 14 other drivers). The router's local DRC
count is zero; this is **not** Magic/KLayout DRC or LVS sign-off.

The completed wire300 result has fast/typical setup slack
+9.162488/+7.747820 ns and minimum hold slack +0.083254 ns across corners.
It has zero setup, hold, slew and max-cap violations at all three configured
corners, zero filtered unannotated timing nets, and 178 max-fanout violations.
These results were read from its existing final STA state; they do not constitute
physical sign-off. Its much larger change scope (567 buffers, 930 affected nets)
is not selected for this focused repair.

The new worst slow path for both small ECOs is `_28312_/Q` to `_28704_/D`
(program-memory read-address bit 1 to `registers[1][3]`). Resize arrival/required
times are 20.457409/20.634029 ns; buffer arrival/required times are
20.018595/20.634132 ns.

The fixed-wire resize screening result was +0.229877 ns; after re-legalization,
affected-net routing and extraction it became +0.176621 ns. Only the latter is
used in the result table. Intermediate global-route estimates, including reports
with missing-route warnings before detailed routing, are not closure evidence.

## Commands and artifacts

From `D:\CS4444\FoRG-protocol-emulator`, the executed experiment commands were:

```powershell
wsl -d Ubuntu -- python3 /mnt/d/CS4444/FoRG-protocol-emulator/scripts/program-loader-incremental.py resize
wsl -d Ubuntu -- python3 /mnt/d/CS4444/FoRG-protocol-emulator/scripts/program-loader-incremental.py buffer
wsl -d Ubuntu -- python3 /mnt/d/CS4444/FoRG-protocol-emulator/scripts/program-loader-incremental.py wire300
```

The reproducible launcher is [program-loader-incremental.py](../scripts/program-loader-incremental.py).
It refuses to overwrite output directories; `--label fresh-name` allows an
explicitly requested new experiment. Each `WORK/incremental/<mode>/` retains
`commands.json` (exact Docker/OpenROAD/LibreLane argv), `prepare.tcl`, preparation
and flow logs, exit codes, affected-net list, prepared ODB/DEF/netlists, initial
state and resolved experiment config. It requires the existing pinned Docker
image, PDK and original checkpoint; it does not install or rebuild tools.

The two first experiments inherited the original resolved `DESIGN_DIR`, so their
distinct, non-overwriting stage directories are under `WORK/src/runs/eco`.
Copies of their completed stage directories are also preserved separately under
`WORK/incremental/resize/completed-stages` and
`WORK/incremental/buffer/completed-stages`. The launcher now explicitly scopes
`DESIGN_DIR` to each experiment directory; the wire-length experiment uses
`WORK/incremental/wire300/runs/eco`.

Actual extracted reports:

- Original: `BASE/45-openroad-stapostpnr/nom_slow_1p08V_125C/max.rpt`
- Resize: `WORK/src/runs/eco/4-openroad-stapostpnr/nom_slow_1p08V_125C/max.rpt`
- Buffer: `WORK/src/runs/eco/5-openroad-stapostpnr/nom_slow_1p08V_125C/max.rpt`
- Wire300: `WORK/incremental/wire300/runs/eco/4-openroad-stapostpnr/nom_slow_1p08V_125C/max.rpt`

Each STA directory also contains per-corner `checks.rpt`, `violator_list.rpt`,
`ws.max.rpt`, `ws.min.rpt`, and `state_out.json`. Its input state identifies the
exact extracted SPEF and candidate netlist/ODB.

The actual ECO commands were:

```tcl
# Resize experiment
replace_cell _20075_ sg13cmos5l_a21oi_2

# Buffer experiment (the tool appends unique suffixes to the requested names)
insert_buffer -buffer_cell sg13cmos5l_buf_4 -net _07797_ \
    -location {506 555.66} -buffer_name eco_setup_buffer -net_name eco_setup_net

# Wire-length experiment, on the existing global-route parasitic model
repair_design -max_wire_length 300 -slew_margin 50 -cap_margin 0 -verbose
```

The buffer's actual name is `eco_setup_buffer7876`, and its new input net is
`eco_setup_net7875`. It is non-inverting. The wire-length trial is a standalone
checkpoint repair test, not an exact replay of the existing GRT repair stage:
its capacitance repair margin is explicitly 0 versus the original stage's 10%.
Actual timing/capacitance limits in the SDC and libraries were not changed.

## Recommendation and limits

The smallest cell edit is the single driver resize; it clears the setup failure
with +0.176621 ns extracted margin. The single-buffer alternative gives more
setup margin (+0.615538 ns) while touching only two nets, making it the preferred
candidate for the requested setup repair. **The single-buffer ECO is selected
as the preferred candidate, as explicitly requested by the user.** Its improvement
from the original is 1.035339 ns. It remains a separate physical ECO checkpoint;
no RTL changes are needed.

The selected checkpoint is `WORK/incremental/recommended-buffer-state.json`.
`WORK/incremental/recommended-buffer.json` records the selected ODB, netlist and
SDC paths and SHA-256 hashes. The final report and launcher are also copied into
`WORK/incremental/` for preservation. No candidate is promoted to a new GDS.

Slew and fanout issues remain outside this focused setup repair. No post-ECO
Magic DRC, LVS, independent precheck, antenna sign-off or GDS generation was run.
No claim of completed physical sign-off or a fabrication-ready updated GDS is made.
