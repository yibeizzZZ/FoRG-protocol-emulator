# Program Loader verification — 2026-10-11

Branch: `feat/runtime-program-loader`, based on I2C-capable revision `7b62a02`.
The implementation adds 19 state bits for managed/committed/error and two
8-bit counters. Program/data capacity and the execution datapath are unchanged.
No PnR constraints, tile allocation, ISA encodings or firmware generators changed.

## RTL and host tooling

Commands (WSL Ubuntu, repository `/mnt/d/CS4444/FoRG-protocol-emulator`):

```bash
export PATH=/tmp/forg-regression/venv/bin:/usr/bin:/bin
bash scripts/test-program-loader.sh
```

This uses the installed Icarus and Cocotb test environment. Python tooling has
**35 passing tests**. Baseline core was **16/16** before editing RTL. The first
four loader tests failed against the original implementation (missing transaction
state/protection), then passed after the implementation. The existing reserved
packet rejection case now uses `fc` instead of the newly assigned `fe` command.

The full regression logs and individual checked XML reports are retained in
`build/program-loader/rtl/`. All **89 RTL test executions passed**, with no
failures/errors/skips. Core was rerun after adding the final two loader cases;
the report below is for that final test set.

| Suite | Passed |
|---|---:|
| Core ISA + six loader tests | 22 |
| UART firmware vs independent reference | 5 |
| SPI firmware | 6 |
| Original legacy engine | 2 |
| Fixed UART: divisors 1, 2, 4, 17, 434 | 15 |
| I2C probe: delays (0,0,0), (0,300,0), (1000,0,300) ns | 15 |
| Full I2C master: same three delay sets | 24 |

The transactionally loaded I2C write/readback smoke test passed in each delay
configuration; the other I2C tests continue to exercise the original loader.

## CMOS5L synthesis and pre-route timing

Executed fresh LibreLane `3.1.0.dev3` through `OpenROAD.STAPrePNR` using the
installed `ihp-sg13cmos5l` PDK and TT tools at
`d66cf179e7bc4d296362ab7e2e3b344dc3c4f665`. The official 6x4 template metadata
from the existing tool setup was overlaid with the **current** `src/config.json`;
the prior design's netlist/checkpoints were not used. The copied RTL hashes are
recorded in `/tmp/forg-physical/runtime-loader/source-sha256.txt`.
The synthesized `protocol_engine.v` SHA-256 is
`93b88e9d6e64517663315c4e316b6c33a62baefc5ebc4104c8284d9c54a65f58`,
verified equal to the final checkout used for RTL tests.

The generated config retains die area `0 0 1289.28 710.64`, the 6x4 pin template,
20 ns clock and the three PnR corners. Command, from the fresh physical directory:

```bash
cd /tmp/forg-physical/runtime-loader
/tmp/forg-physical/venv311/bin/python -m librelane \
  --docker-mount /tmp/forg-physical --docker-no-tty --dockerized \
  --manual-pdk --pdk ihp-sg13cmos5l --pdk-root /tmp/forg-physical/pdk \
  --run-tag loader-synth --hide-progress-bar --override-config OPENROAD_THREADS=8 \
  --to OpenROAD.STAPrePNR src/config_merged.json
```

Results under `src/runs/loader-synth/`:

- Flow exited zero; Verilator lint, mapped-cell and synthesis checks completed.
- Yosys `chk.rpt`: **0 problems**; no inferred latches.
- Mapped cells: **17,113**, including **2,746** sequential cells.
- Mapped cell area: **266,623.3584 square micrometres**. This is not placed area
  or proof that the design fits/routs within the tile.
- Generic PnR/signoff SDC fallback warnings were emitted because custom SDC
  files are not specified, as in the existing project configuration.

| Pre-route corner | Worst setup slack (ns) | Worst hold slack (ns) |
|---|---:|---:|
| Fast, 1.32 V, -40 C | +1.1430 | -0.0607 |
| Slow, 1.08 V, 125 C | -23.7088 | +0.1696 |
| Typical, 1.20 V, 25 C | -8.4293 | +0.0254 |

These are **unplaced, unrepaired synthesis-netlist** reports, with setup, hold,
slew and capacitance violations. The flow stopped before physical optimization
and final timing checkers. A successful synthesis command is not timing closure.
No clock relaxation or checker disabling was introduced.

## Synthesized gate functional checks

Copied the newly synthesized `final/nl/tt_um_forg_protocol_engine.nl.v` into an
isolated test directory `/tmp/forg-loader-gates/test`, together with this checkout's
tests, scripts, firmware and sources. Executed:

```bash
cd /tmp/forg-loader-gates/test
PATH=/tmp/forg-regression/venv/bin:/usr/bin:/bin \
  make -B TARGET=core GATES=yes PDK_ROOT=/tmp/forg-physical/pdk
/tmp/forg-regression/venv/bin/python ../scripts/check-test-results.py results.xml
```

**22/22 passed**, no skips, including all six loader tests and UART-to-SPI
replacement without reset. Log: `/tmp/forg-loader-gates/core.log`; preserved XML:
`build/program-loader/gates-core.xml`. Matching PDK functional models were used.
This is zero-delay functional simulation, not SDF or routed-netlist validation.

## Precheck and remaining physical work

Executed the installed official metadata/documentation check:

```bash
/tmp/forg-physical/venv311/bin/python /tmp/forg-physical/tt/tt_tool.py \
  --project-dir /mnt/d/CS4444/FoRG-protocol-emulator --ihp --check-docs
```

It passed. **Physical Tiny Tapeout precheck was not run:** this new RTL has no
fresh GDS/LEF/physical netlist yet. Prior I2C artifacts are a different design.
Placement, routing, extracted multi-corner timing, DRC, LVS and the independent
Tiny Tapeout precheck remain required. The existing official `gds.yaml` workflow
still supplies those stages when the branch is intentionally pushed/built.
No remote branch push, CI dispatch, or physical sign-off is claimed.

Board validation, asynchronous-host adaptation and stronger transfer-integrity
checking remain future work. The new loader guarantees image completeness and
ordering within its synchronous protocol, not arbitrary data corruption detection.
