# Remaining single-buffer ECO violations

Read-only analysis of the recommended checkpoint. No EDA executable, simulation,
optimization, placement, routing, DRC, LVS, precheck or GDS generation was run for
this analysis. RTL, physical databases and constraints were not modified.
The selected ODB, netlist and SDC hashes match `recommended-buffer.json`.

## Classification

- Slow corner: 57 max-slew **pin** violations on **8 distinct data/control nets**;
  zero clock-tree slew violations. Of the 57 pins, 8 are drivers, 33 are functional
  receivers and 16 are antenna-diode terminals. Fast and typical corners have no
  slew violations.
- All three corners: 192 max-fanout driver violations = **178 clock leaves +
  14 data/control nets**. All 14 data/control nets have at most 8 functional sinks;
  antenna-diode terminals increase the counted fanout above 8.
- All three corners: zero max-capacitance violations, zero setup/hold violations.
  Selected ECO slow setup slack remains +0.615538 ns; minimum hold slack across
  corners is +0.131007 ns. No new timing calculation or validation was performed.

## Slew offenders (slow, 1.08 V, 125 C)

All pins below have a **2.507400 ns** limit from the Liberty
`default_max_transition`. Loads are functional inputs + antenna-diode pins.
Wire capacitance is extracted SPEF capacitance, **excluding cell pin capacitance**
(`PIN_CAP NONE`, pF). The driver/worst-sink transitions are exact existing
`checks.rpt` values, in ns.

| Net | Driver / cell | Loads | Wire C (pF) | Driver / worst sink slew (ns) | Violating pins |
|---|---|---:|---:|---:|---:|
| `_06077_` | `_22469_/Y` / `nand4_1` | 5 + 0 | 0.174221 | 3.357162 / 3.360450 | 6 |
| `_05901_` | `_22278_/Y` / `nor3_1` | 3 + 1 | 0.152703 | 3.103847 / 3.105790 | 5 |
| `_09461_` | `_15080_/Y` / `nor3_1` | 3 + 9 | 0.135117 | 3.061724 / 3.064666 | 13 |
| `_06010_` | `_22395_/Y` / `nor3_1` | 3 + 0 | 0.139209 | 2.814509 / 2.815735 | 4 |
| `_03997_` | `_20075_/Y` / `a21oi_1` | 7 + 0 | 0.189561 | 2.746955 / 2.748309 | 8 |
| `_09417_` | `_15004_/Y` / `nor3_1` | 3 + 0 | 0.129369 | 2.664774 / 2.665769 | 4 |
| `_09357_` | `_14932_/Y` / `nor4_2` | 3 + 4 | 0.181873 | 2.592951 / 2.598517 | 8 |
| `_08916_` | `_14475_/Y` / `nand3_1` | 6 + 2 | 0.165465 | 2.522585 / 2.527138 | 9 |

The worst pin is `_23184_/B` on `_06077_`: 3.360450 ns, exceeding the limit by
0.853050 ns. Its nand4 driver already has 3.357162 ns slew. Across all eight nets,
the extra driver-to-worst-sink transition degradation is below 0.006 ns:
loading of the output driver is the dominant observed effect, rather than a
large additional slew degradation between driver and receiver.

Static Liberty pin-capacitance estimates place wire capacitance at approximately
88?96% of wire-plus-pin capacitance for these nets. These estimates are not
OpenSTA effective capacitances: rise/fall pin-capacitance ranges and timing-arc
selection can change the exact total. SPEF wire capacitance and report slews are
measured evidence; the weak-driver/wire-load diagnosis is an inference from them.
Only `_09461_` also exceeds the fanout limit. Low fanout alone does not make the
other seven nets electrically light. Diodes add real capacitance and report
entries; do not remove them without antenna verification.

## Fanout offenders

The SDC says `set_max_fanout 10.0000 [current_design]`, but the Liberty has
`default_max_fanout : 8` and `default_fanout_load : 1`. The actual reports enforce
**8**. There is no evidence that changing the SDC to 10 overrides the stricter
library rule. No constraint was changed.

### Clock leaves

All 178 flagged clock drivers are `sg13cmos5l_buf_8` leaves, with fanout 10?18.
Twelve tie for worst fanout 18: leaf IDs 10, 11, 123, 133, 136, 140, 142, 144,
157, 159, 165 and 168. Their output net names are `clknet_leaf_<ID>_clk`.

Representative worst-count net: `clknet_leaf_157_clk`, driven by
`clkbuf_leaf_157_clk/X`, has 18 loads, wire C 0.041158 pF, estimated pin C
0.042685?0.050107 pF, and 0.092547 ns driver transition observed in the saved path
reports. This is comfortably below 2.507400 ns; its driver's Liberty max-cap limit
is 2.4 pF. Other 18-load leaves show 0.080750?0.089013 ns observed transitions.
These observed path values are not an exhaustive all-arc transition sweep.
Existing violation reports nevertheless establish zero clock slew/cap violations.

CTS's original `23-openroad-cts/openroad-cts.log` explicitly states
`Sinks will be clustered based on buffer max cap`. Clock loads include flop CLK
pins and clock balancing cells. This supports a mismatch between capacitance-based
CTS and the blanket fanout-count rule, rather than overloaded clock drivers.

**Sign-off distinction:** these are real violations of the active Liberty rule,
not proof of a failing clock waveform. They cannot be declared waived from these
files. Confirm the intended clock fanout acceptance policy with the PDK/flow owner.
Leave the tree unchanged for this focused repair; if strict fanout <=8 is required,
clock leaf splitting/rebalancing would need later skew and hold validation.
Upsizing alone cannot fix a fanout count limit shared by the cells.

### All 14 data/control nets

Every row's fanout limit is 8. Transitions below are maxima observed in the saved
max/min/checks path listings, not exhaustive per-net worst transitions. Missing
values are explicitly marked; no new STA was run to fill them. Except `_09461_`,
none of these nets appears in the complete slow-corner slew violation list.

| Net | Driver / cell | Functional + diode loads | Wire C (pF) | Observed driver slew (ns) |
|---|---|---:|---:|---:|
| `net960` | `fanout960/X` / `buf_1` | 8 + 6 = 14 | 0.206123 | 1.240623 |
| `net969` | `fanout969/X` / `buf_1` | 4 + 9 = 13 | 0.141422 | 0.869494 |
| `_09461_` | `_15080_/Y` / `nor3_1` | 3 + 9 = 12 | 0.135117 | 3.061724 |
| `net1467` | `fanout1467/X` / `buf_1` | 8 + 4 = 12 | 0.144267 | Not in inspected paths |
| `net922` | `fanout922/X` / `buf_1` | 8 + 4 = 12 | 0.116375 | Not in inspected paths |
| `net1932` | `fanout1932/X` / `buf_2` | 6 + 5 = 11 | 0.138140 | 0.518161 |
| `_06072_` | `_22463_/X` / `or2_1` | 5 + 5 = 10 | 0.162239 | Not in inspected paths |
| `_06426_` | `_22868_/Y` / `nor2_1` | 7 + 3 = 10 | 0.116763 | Not in inspected paths |
| `_09691_` | `_15502_/Y` / `nand2_1` | 8 + 1 = 9 | 0.060652 | Not in inspected paths |
| `_05940_` | `_22319_/Y` / `nand2b_1` | 8 + 1 = 9 | 0.100713 | Not in inspected paths |
| `_05943_` | `_22322_/Y` / `nand2_1` | 8 + 1 = 9 | 0.108849 | Not in inspected paths |
| `engine\.pc\[5\]` | `_28619_/Q` / `dfrbpq_1` | 8 + 1 = 9 | 0.078805 | Not in inspected paths |
| `net33` | `fanout33/X` / `buf_1` | 8 + 1 = 9 | 0.069527 | 0.598876 |
| `net898` | `fanout898/X` / `buf_1` | 8 + 1 = 9 | 0.102828 | Not in inspected paths |

`engine\.pc\[5\]` is SPEF's escaped spelling of `engine.pc[5]`.
The most heavily loaded data buffer is `fanout960/X`: 8 functional inputs plus
6 diode terminals, wire C 0.206123 pF, observed slew 1.240623 ns. It passes the
slew limit despite fanout 14. The same distinction applies to the other diode
inflated fanout counts; those loads are physically real even when the count rule
is conservative.

## Smallest recommended actions (not implemented or validated)

1. Keep the selected `eco_setup_buffer7876` ECO and clock tree as the baseline.
2. Address the eight slew nets individually. For `_05901_`, `_09461_`, `_06010_`
   and `_09417_`, the PDK provides a same-function `nor3_2` replacement for
   `nor3_1`; this is the first small candidate to investigate. For `_03997_`,
   `a21oi_2` exists. A prior separate resize experiment cleared its eight slew
   entries (57 -> 49), but that result is **not** validation of stacking the
   resize on this single-buffer checkpoint.
3. `_06077_` (`nand4_1`) and `_08916_` (`nand3_1`) have no larger same-function
   variant in the loaded standard-cell library. `_09357_` already uses the
   largest available `nor4_2`. A local non-inverting buffer to isolate/split the
   long capacitive branch is the smallest plausible physical correction for
   these nets. Choose location and load partition from the routed topology;
   no untested buffer strength/location is claimed sufficient here.
4. If fanout <=8 must be satisfied literally, split only the affected branches
   (including diode loads in the budget). Resizing will not reduce pin count.
   Do not blanket-repair the entire design or remove antenna diodes. Resolve the
   sign-off interpretation first for electrically passing clock/data networks.
5. Preserve 20 ns timing and the selected hold margin. Any later authorized ECO
   must recheck extracted setup/hold at all three corners: upsizing also loads
   its input and can shorten minimum paths; clock edits can alter skew. Static
   analysis cannot guarantee the proposed fixes retain +0.615538/+0.131007 ns.

## Evidence and reproducibility

`WORK = /home/administrator/builds/forg-loader-physical-20261012`

- Selected state: `WORK/incremental/recommended-buffer-state.json`
- Physical ODB/netlist/SDC: `WORK/src/runs/eco/3-openroad-fillinsertion/tt_um_forg_protocol_engine.{odb,nl.v,sdc}`
- SPEF: `WORK/src/runs/eco/4-openroad-rcx/nom/tt_um_forg_protocol_engine.nom.spef`
- Slow reports: `WORK/src/runs/eco/5-openroad-stapostpnr/nom_slow_1p08V_125C/{checks,max,min}.rpt`
  (`checks.rpt`: slew starts line 202; fanout follows.)
- Library: `/tmp/forg-physical/pdk/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_slow_1p08V_125C.lib`
- CTS log: `WORK/src/runs/loader-physical/23-openroad-cts/openroad-cts.log`
- Full static classification: `WORK/incremental/remaining-violations-analysis/static-analysis.json`

Executed static parser command, from `D:\CS4444\FoRG-protocol-emulator`:

```powershell
python build/program-loader/analyze-remaining.py
```

The parser reads existing SPEF connectivity, Liberty, and timing reports and writes
only analysis JSON. Its copy is preserved alongside this report in
`WORK/incremental/remaining-violations-analysis/analyze-remaining.py`.
Other commands were `Get-Content`, `Select-String`, directory listings, and Python
read-only summaries/hash checks. No physical database was rewritten.
