# Program Loader physical validation — 2026-10-12

Source: `feat/runtime-program-loader` at
`f909b48ef6613b0158580c2aacd9e7a4df1a55ce`.
RTL, ISA, 6x4 tile allocation and timing constraints are unchanged.
This report supplements the earlier synthesis and functional results in
[program-loader-results.md](program-loader-results.md).

## Reproduction and evidence

The persistent WSL workspace is
`/home/administrator/builds/forg-loader-physical-20261012` (called `WORK` below).
The physical run is `WORK/src/runs/loader-physical` (called `RUN`).
It uses the installed official TT tools at
`d66cf179e7bc4d296362ab7e2e3b344dc3c4f665`, LibreLane `3.1.0.dev3`,
and the installed `ihp-sg13cmos5l` PDK.
The PDK `SOURCES` file identifies IHP-Open-PDK revision
`2bbec755dc67ca3db0261c3d6163e15735d66710` (copied to `WORK/pdk-sources.txt`).
`WORK/docker-image.json`, `tt-revision.txt`, `source-commit.txt`, and
`input-sha256.txt` record the precise local inputs.

Command executed from `/mnt/d/CS4444/FoRG-protocol-emulator`:

```bash
bash scripts/program-loader-physical.sh
```

The script copies the existing, matching loader synthesis checkpoint and resumes
the official Classic flow at `OpenROAD.Floorplan`. It does not reuse the old I2C
physical layout. The only command-line configuration override is
`OPENROAD_THREADS=8`. The merged configuration retains `CLOCK_PERIOD=20`, die
area `0 0 1289.28 710.64`, the 6x4 pin template, all three PnR corners, and the
existing post-global-route repair settings. No exceptions or relaxed constraints
were introduced. The script refuses to overwrite an existing workspace; a new
authorized run can use a different `FORG_PHYSICAL_WORK` path.

The full invocation is saved in `WORK/run-physical.sh`; the flow log is
`WORK/physical.log`. The initial synthesis evidence was copied into
`WORK/synthesis-evidence`. These files are outside volatile `/tmp`; the scripts
still depend on the pinned tools and PDK currently installed under
`/tmp/forg-physical`.

## Physical repair and placement timing

`RUN/20-openroad-repairdesignpostgpl/openroad-repairdesignpostgpl.log`
records 18 input buffers and 2,319 repair buffers across 470 nets.
`RUN/25-openroad-resizertimingpostcts/openroad-resizertimingpostcts.log`
records another 2,781 hold buffers.
`RUN/32-openroad-resizertimingpostgrt/openroad-resizertimingpostgrt.log`
records 11 additional buffers and 77 upsized instances. At this stage there are
5,129 timing-repair buffers, 249 clock buffers and 115 clock inverters.
This is direct evidence of physical repair, rather than an inference from slack.

Supplemental read-only STA loads the saved stage-25 ODB and SDC, all three
library corners, and placement-estimated parasitics. Commands and Tcl are in
`WORK/placement-postcts-analysis/{command.json,report.tcl}`; its launcher is
`WORK/analysis-scripts/postcts-report.py`. It writes no design checkpoints and
changes no constraints.

| Stage / corner | Worst setup slack (ns) | Maximum-capacitance violations |
|---|---:|---:|
| Synthesis, slow 1.08 V / 125 C | -23.708755 | 29 |
| Placement after CTS and hold repair, fast | +9.306886 | 0 |
| Placement after CTS and hold repair, typical | +7.994376 | 0 |
| Placement after CTS and hold repair, slow | +4.409355 | 0 |

Placement reports are `WORK/placement-postcts-analysis/nom_<corner>-max.rpt`
and `nom_<corner>-cap.rpt`. The slow placement critical path is `_28312_/Q`
(program-memory read-address bit 1) to `_28095_/D`
(`engine.data_mem[26][0]`): arrival 16.021650 ns, required 20.431005 ns.
It traverses the address buffer tree, program read/decode and data-memory write
logic. Its data portion has maximum local fanout 8 and maximum reported load
0.102455 pF. Clock-tree fanout is separate from these data-path figures.

The original problematic `_28311_/Q` / `_00000_` net now drives just
`fanout2200/A`, rather than 1,501 direct loads. Its reported physical total
capacitance range is 0.003223–0.003964 pF. The complete net report is
`WORK/placement-postcts-analysis/program-address-net.rpt`; the slow worst path
from this source has +5.949683 ns slack.

**Reporting caveat:** intermediate `state_out.json` files inherit old corner
metrics. The flat mid-PnR STA report evaluates the fast corner only, despite
the loaded multi-corner libraries. The old -23.708755 ns value in inherited
slow-corner fields is not a fresh placement result. An earlier supplemental
stage-22 report is retained in `WORK/placement-analysis`; its saved SDC propagates
the still-unbuffered pre-CTS clock, producing unrealistic clock load/delay.
The table therefore explicitly uses the post-CTS placed snapshot with a real
clock tree and the original propagated-clock SDC.

## Routed timing and physical verification

**50 MHz slow-corner timing closure was not achieved.** The official extracted
STA at `RUN/45-openroad-stapostpnr` reports:

| Corner | Worst setup slack (ns) | Worst hold slack (ns) | Setup endpoints failing | Max-cap violations | Max-slew violations |
|---|---:|---:|---:|---:|---:|
| Fast, 1.32 V / -40 C | +8.631746 | +0.131007 | 0 | 0 | 0 |
| Typical, 1.20 V / 25 C | +6.603197 | +0.325863 | 0 | 0 | 0 |
| Slow, 1.08 V / 125 C | **-0.419801** | +0.673325 | 8 | 0 | 57 |

Slow setup TNS is -2.854675 ns. Each corner reports 192 max-fanout violations
design-wide (178 clock-tree drivers and 14 other drivers); this is separate from
the repaired 1,501-load root and from the maximum-capacitance check. The library's
`default_max_fanout` is 8, stricter than the saved SDC's design limit of 10.
No hold violations remain in these three corners.
These are the configured `nom_*` RC/library combinations, not a claim covering
additional unconfigured RC extremes.

Primary reports:

- `RUN/45-openroad-stapostpnr/summary.rpt`
- `RUN/45-openroad-stapostpnr/nom_slow_1p08V_125C/max.rpt`
- `RUN/45-openroad-stapostpnr/nom_slow_1p08V_125C/checks.rpt`
- Per-corner `ws.max.rpt`, `ws.min.rpt` and `violator_list.rpt` in that stage.
- `RUN/44-openroad-rcx/nom/tt_um_forg_protocol_engine.nom.spef`
- `WORK/routed-critical-path.rpt` is a compact copy of the worst path.

The routed worst path is `_28313_/Q` (program-memory read-address bit 2) to
`_28704_/D` (`engine.registers[1][3]`). Arrival is 21.117653 ns and required time
20.697851 ns. Launch clock arrival is 1.184600 ns, so the launch-clock-pin to
data-pin delay is 19.933053 ns. This includes 0.621177 ns clock-to-Q, 35
combinational cells (12 buffers), and about 0.316986 ns of reported interconnect
arc delay. The large capacitive loads also increase **cell** delay, so the small
interconnect-arc total does not mean wiring is unimportant.
The eight failing endpoints are bits 0 and 3 of each of R0–R3; their reported
worst paths all launch from `_28313_/Q`, with slack from -0.419801 to -0.301286 ns.

The path traverses the address buffer tree, program-read/decode and M2 execution
logic, then register writeback. Its maximum local data fanout is 8 and maximum
reported data load is 0.213836 pF. It is not a loader packet-input or commit-state
path. Mapping uses the preserved synthesis JSON and netlist; the sequential cell
count is unchanged at 2,746.

Selected slow-path stages:

| Driver | Cell | Fanout | Path cap (pF) | Slew (ns) | Cell arc (ns) |
|---|---|---:|---:|---:|---:|
| `_28313_/Q` | `dfrbpq_1` | 3 | 0.085342 | 0.439302 | 0.621177 |
| `_20033_/Y` | `o21ai_1` | 2 | 0.100725 | 1.520199 | 1.267868 |
| `_20075_/Y` | `a21oi_1` | 7 | 0.213836 | 2.003894 | 1.600617 |
| `_24534_/Y` | `nand4_1` | 2 | 0.129877 | 2.316847 | 1.866233 |

All cell names have the `sg13cmos5l_` prefix. A path's transition is not necessarily
the worst transition used by the separate slew-violation checker.

Read-only extracted STA was also run to query specific nets, using the same
netlist, saved SDC, slow libraries and SPEF. Its worst slack exactly reproduces
the official -0.419801 ns result. Invocation and Tcl are retained under
`WORK/routed-analysis`, with the launcher in
`WORK/analysis-scripts/routed-report.py`.

The original `_00000_` root now has fanout **1**, total slow-corner capacitance
0.004358–0.004733 pF, and clock-to-Q 0.307293 ns on its reported worst path.
This compares with fanout 1,501, 5.670761 pF and 26.076761 ns before physical
repair. Its own worst endpoint is now also `registers[1][3]`, at -0.034127 ns.
Repair removed the enormous root load but did not close every downstream path.

## Remaining bottleneck and smallest proposed correction

Verified evidence from `WORK/routed-analysis/*-net.rpt` and
`RUN/40-odb-reportwirelength/wire_lengths.csv`:

- `_03997_`, driven by `_20075_/Y`, has 1,288.6 um total routed length and
  0.189554 pF wire capacitance, versus 0.019517–0.025461 pF pin capacitance.
- `_07797_`, driven by `_24534_/Y`, has 669.515 um routed length and
  0.119309 pF wire capacitance, versus 0.009252–0.010956 pF pin capacitance.
- `_03955_`, driven by `_20033_/Y`, has 672.855 um routed length.

The remaining problem is a deep M2 program-read/execution/writeback path with
wire-loaded control nets, rather than the original unbuffered 1,501-load root.
The +4.409355 ns placement estimate became -0.419801 ns after extraction, and
the worst path changed, so their 4.829156 ns difference is **not** the degradation
of one identical path.

The smallest reproducible next experiment should be **physical repair, without
RTL changes**: change only `GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH` from its present 0
to a provisional 300 um, so the existing resizer can buffer long routed nets.
300 um is a candidate to test, not a demonstrated optimum or passing setting.
Keep the current 20 ns target, tile and all timing checks.

For a targeted physical ECO, prioritize local buffering/repositioning on the
long nets above and consider `_20075_` from `a21oi_1` to the available `a21oi_2`.
The loaded library has no larger `nand4` or `o21ai` variants, so blindly asking to
upsize `_24534_` or `_20033_` is not a viable direct cell substitution; use
buffering there. These proposals have not been implemented or shown to close
timing. Check all 57 slew violations; saving 0.42 ns on one path alone would not
prove full closure.
Re-extract and re-run timing, DRC, LVS and precheck after any physical ECO.
Changing clock period, adding path exceptions or changing the ISA is unwarranted.

The user stopped full validation before completion. Magic DRC and the main flow
were interrupted; LVS was not reached. The precheck parent was terminated, but
KLayout did not exit on TERM/INT and was suspended with SIGSTOP to halt computation
without forcibly killing it. Its PID/PGID is 3108098 (container `c6b3da517ad9`).
Neither DRC nor independent precheck has a passing final result. All original
GDS, LEF, ODB, SPEF, logs and partial reports remain in `WORK`.

Subsequent work is restricted to checkpoint-based incremental timing ECOs;
the full Classic flow and physical sign-off checks must not be restarted without
the user's explicit request.
