# M0-M2 validation evidence

## Local verification

Measured 2026-10-01 for the RTL introduced in commit `add393e`.
Subsequent documentation changes do not change the RTL.

| Design | RTL | Locally mapped CMOS5L gates |
|---|---|---|
| Original engine | 2 pass | Original CI netlist: 1 pass, 1 RTL-internal test skipped |
| Fixed UART | 3 pass; CI sweeps divisors 1, 2, 4, 17, 434 | 3 pass at divisor 434 |
| Programmable engine | 5 pass | Same 5 pass; no internal-name assumptions |

The original engine's gate startup uses a reset after the first full program
load to clear four-state unknown propagation. Its RTL regression retains
the original startup sequence. The new M2 engine does not use this workaround:
it tracks valid program bytes and faults on an incomplete instruction.

Deliberate mutations to WAIT length, JNZ condition and UART payload were
each detected by assertions. All mutated RTL was restored and normal tests
passed again. This is targeted regression evidence, not exhaustive proof.

## Local mapped area estimates

Tool: Yosys 0.69, synth/flatten, dfflibmap, ABC, stat.
Library: IHP CMOS5L typical 1.20 V / 25 C, PDK revision
`2bbec755dc67ca3db0261c3d6163e15735d66710`.

| Design | Mapped standard-cell area (um²) |
|---|---:|
| Fixed UART, 434 cycles/bit | 2,070.117 |
| Programmable core, 32x16 words | 54,696.789 |

These are logic-area estimates, not die size, post-route area, or timing
closure. They exclude physical fill, routing and clock-tree overhead.
The designs are synthesized independently; UART logic is not in the core.

Reproduce with the matching Liberty file:

```bash
python3 scripts/synth-local.py uart --liberty /path/to/sg13cmos5l_stdcell_typ_1p20V_25C.lib
python3 scripts/synth-local.py core --liberty /path/to/sg13cmos5l_stdcell_typ_1p20V_25C.lib
```

Reports and mapped netlists are generated under build/synthesis/TARGET/.
For functional gate tests, copy the matching netlist to
test/gate_level_netlist.v and run:

```bash
PDK_ROOT=/path/to/pdk GATES=yes bash scripts/test-local.sh TARGET=core
PDK_ROOT=/path/to/pdk GATES=yes bash scripts/test-local.sh TARGET=uart
```

PDK_ROOT must contain the matching ihp-sg13cmos5l directory. Gate simulation
uses generated zero-delay models, not SDF timing annotation.

## Physical build evidence

M0 repair build: [run 36895697829](https://github.com/yibeizzZZ/FoRG-protocol-emulator/actions/runs/36895697829), revision c835af9.

- GDS build and gate-level functional regression passed.
- Generated LEF has 33 VGND and 33 VPWR rectangles, each at least 2.100 um wide. The prechecker accepts VPWR as an alias for VDPWR.
- Post-route standard-cell area: 9,162.72 um².
- Worst setup slack: 13.1620 ns; worst hold slack: 0.1362 ns.
- Setup and hold violation counts: both zero.
- All nine physical prechecks passed, including the pin-width check that failed in the original run. Development-branch viewer publication is intentionally skipped.

M1 UART build: [run 36896008643](https://github.com/yibeizzZZ/FoRG-protocol-emulator/actions/runs/36896008643), revision 4f96854.

- Independent GDS build and all three gate-level tests passed.
- Post-route standard-cell area: 3,305.84 um²; 235 standard-cell instances.
- Clock constraint: 20 ns (50 MHz), divisor 434.
- Worst setup slack: 12.9467 ns; worst hold slack: 0.1634 ns.
- Setup, hold, slew and capacitance violation counts: all zero.
- All nine physical prechecks passed.

M2 core build: [run 36896558569](https://github.com/yibeizzZZ/FoRG-protocol-emulator/actions/runs/36896558569), revision add393e.

- GDS build and all five post-route gate tests passed (also reproduced on macOS). Physical precheck is still running.
- Post-route standard-cell area: 73,581.2 um²; 3,895 standard-cell instances.
- Clock constraint: 20 ns (50 MHz).
- Worst setup slack: 7.1685 ns; worst hold slack: 0.1235 ns.
- Setup, hold, slew and capacitance violation counts: all zero.

Physical flow: LibreLane 3.1.0.dev3 with the pinned IHP PDK revision above.
These are results at the configured clock, not an independently measured maximum clock frequency. No real FPGA or fabricated ASIC validation is claimed.
