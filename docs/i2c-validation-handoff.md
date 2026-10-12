# I2C validation handoff — user-operated WSL commands

The assistant prepares and analyzes changes; the user runs validation. Nothing
below is run automatically. The first user-operated route attempt failed as
described below; the corrected commands have not been executed. No push or CI
dispatch is needed.
The standing policy is recorded in [AGENTS.md](../AGENTS.md).

## Reviewed state

- Branch: `feature/i2c-firmware`; reviewed HEAD: `3e9ffea`.
- Complete I2C firmware supports 1..15-byte writes, reads and combined transfers,
  repeated START, target ACK/NACK, controller ACK/final NACK, clock stretching,
  open-drain GPIO, runtime address/payload RAM and completion/error status.
  Code occupies 127/128 words. No dedicated I2C controller was added.
- Existing evidence for the unchanged RTL: 83 RTL executions and 34 Python tests
  passed; the synthesized netlist passed core 16, SPI 6 and I2C 8 tests.
- **Physical signoff is not complete.** The last completed extracted report
  (`corners8`) has slow-corner setup **−1.606 ns**, 288 setup violations and
  11 slew violations; hold is +0.135 ns with zero violations. Routing DRC is
  zero. This does not establish 50 MHz operation.
- The latest candidate `grt-margin6-wire300` completed post-global-route repair,
  but not final detailed routing/extracted timing. It preserves the 20 ns clock,
  all three PnR/resizer corners and all-corner setup/hold/slew/capacitance checks.
  Its internal repair settings are 6 ns setup margin, 50% slew margin and a
  300 µm maximum wire length. **The 300 µm setting is an experimental override,
  not yet in `src/config.json`.** The commands explicitly carry this override.
- Slow-only resizer experiments are not used: they would change fast-corner hold
  optimization and cannot be silently substituted into this all-corner flow.

## Corrected resume point after the first route failure

The original helper used `--from OpenROAD.STAMidPNR`. Classic contains four
instances of this step; its unsuffixed ID selects the first, before CTS. The
post-global-route checkpoint already contains a clock tree. The failed run
therefore repeated placement repair and CTS, reported `CTS-0105` (existing clock
buffers), then crashed with signal 11 in `TritonCTS::separateMacroRegSinks`.
This was a handoff-script resume error; that run did not reach detailed routing
or establish a new extracted timing result.

The corrected helper starts at the unique `OpenROAD.DetailedRouting` step using
the original `grt-margin6-wire300` checkpoint. It retains extraction, post-route
STA and the timing checkers. It does not reuse the failed run's modified state.
New output directories use `manual-i2c-wire300-v2`; the original log at
`/tmp/forg-physical/manual-i2c-wire300/route.log` and its run directory are preserved.
Only static checks have been performed on this correction. The user must execute
the corrected route command before proceeding to later phases.

## Working directory and dependencies

Run each command in a WSL Bash terminal from:

```bash
cd /mnt/d/FoRG/FoRG-protocol-emulator
```

These commands use the existing local installation and checkpoints, not a fresh
checkout setup. They require:

- Docker running, with the installed LibreLane `3.1.0.dev3` image; recorded
  digest `sha256:d109140b8f17fc54f4fca998beb8124f4949404ec52e339eebd2250854a18b5a`.
- `/tmp/forg-physical/venv311/bin/python` and the pinned IHP PDK under
  `/tmp/forg-physical/pdk` (`2bbec755dc67ca3db0261c3d6163e15735d66710`).
- Tiny Tapeout tools under `/tmp/forg-physical/tt`, revision
  `d66cf179e7bc4d296362ab7e2e3b344dc3c4f665`.
- The saved design `/tmp/forg-physical/mem2reg-synth`, specifically
  `src/runs/grt-margin6-wire300/04-openroad-resizertimingpostgrt/state_out.json`.
- For precheck: `/tmp/forg-physical/precheck-venv/bin/python` with the existing
  `gdstk`, KLayout Python and YAML dependencies; the existing Docker KLayout
  launcher `/tmp/forg-physical/bin/klayout`.
- For gate simulation: `make`, Icarus Verilog 12 (`iverilog`/`vvp`) and
  `/tmp/forg-regression/venv/bin/python` with Cocotb 2.0.1 and `find_libpython`.

`/tmp` is temporary storage. If WSL restarted or any dependency/checkpoint is
missing, stop and report that; these commands do not silently regenerate it.
The helper checks that both saved and checkout RTL match the validated hashes.
It refuses to overwrite an existing phase directory, so it cannot silently retry
an identical run. If a phase fails, provide its summary before attempting another.

## Commands and acceptance criteria

Execute phases separately. Each stays in the foreground, saves verbose output to
a log and prints only a completion message or the last 25 lines on failure.
Runtime estimates reflect this machine's earlier runs and are not guarantees.

### 1. Detailed routing, extraction and all-corner timing

Expected runtime: **15–45 minutes**, eight OpenROAD workers.

```bash
bash scripts/i2c-validation-handoff.sh route
```

This resumes the completed post-global-route state, without repeating synthesis
or placement. It runs routing, routing DRC/connectivity checks, extraction and
STA, then separately invokes the setup/hold/slew/capacitance checkers. Stopping
at STA alone would not enforce timing failure detection.

- Logs: `/tmp/forg-physical/manual-i2c-wire300-v2/{route,timing-check}.log`.
- Results: `/tmp/forg-physical/mem2reg-synth/src/runs/manual-i2c-wire300-v2-route/`.
- Timing report: `*-openroad-stapostpnr/summary.rpt` under that directory;
  per-corner `max.rpt` and `checks.rpt` are alongside it.
- Success: exit 0; all three corners have nonnegative setup/hold slack and zero
  setup, hold, slew and capacitance violations; route DRC/connectivity checks pass.
- Failure: any nonzero exit, missing report/corner or remaining violation.
  **Stop here and share the summary. Do not change the clock or automatically retry.**

### 2. GDS, Magic DRC and Netgen LVS

Run only after phase 1 passes. Expected runtime: **40–120 minutes**; earlier
Magic DRC alone took about 30 minutes.

```bash
bash scripts/i2c-validation-handoff.sh signoff
```

- Log: `/tmp/forg-physical/manual-i2c-wire300-v2/signoff.log`.
- Results: `/tmp/forg-physical/mem2reg-synth/src/runs/manual-i2c-wire300-v2-signoff/`.
- GDS, LEF, netlists and metrics: that run's `final/` directory.
- Detailed reports: its `*-magic-drc/`, `*-netgen-lvs/` and checker directories.
- Success: exit 0; GDS/LEF exist; Magic DRC is zero; Netgen LVS matches; no
  illegal overlaps, checker failures or timing violations.
- A GDS file existing is not sufficient evidence of passing signoff.

### 3. Independent Tiny Tapeout precheck

Run only after phase 2 passes. Expected runtime: **20–90 minutes**, potentially
longer for the expanded design's connectivity checks.

```bash
bash scripts/i2c-validation-handoff.sh precheck
```

- Log: `/tmp/forg-physical/manual-i2c-wire300-v2/precheck.log`.
- Reports: `/tmp/forg-physical/manual-i2c-wire300-v2/precheck/reports/`.
- Success: exit 0 and all **nine applicable IHP checks** pass, including full
  KLayout DRC, pin/power and boundary checks.
- The flow configuration disables integrated KLayout DRC/XOR. This separate
  precheck supplies the full IHP KLayout DRC; XOR equivalence is not claimed.
- Precheck receives the **powered netlist (`pnl`)** with VPWR/VGND. Passing the
  power-pin-free gate-simulation netlist here would fail the power-pin check.

### Recovery for the missing reports directory in the first precheck attempt

The initial helper excluded old `reports` during the tool copy but failed to
create an empty replacement. KLayout completed its computation (about 33 minutes)
but could not save `drc_sg13cmos5l.xml`; the runner then also failed to save
`results.xml`. This attempt does not establish a passing precheck. Its in-memory
results were lost, so creating a summary alone cannot recover the result.
The helper now creates the directory before launching any checks.

For the existing failed workspace only, use the following command instead of
rerunning the helper (which intentionally refuses existing phase directories).
This preserves the original `precheck.log`, creates the missing report directory,
and runs all applicable checks on the same signoff artifacts. It does not repeat
route or signoff. Dependencies are the same as phase 3; allow **20–90 minutes**.
This recovery has not been executed by the assistant.

```bash
cd /mnt/d/FoRG/FoRG-protocol-emulator
(
    set -euo pipefail
    set -o noclobber
    cd /tmp/forg-physical/manual-i2c-wire300-v2/precheck
    mkdir -p reports
    env PATH="/tmp/forg-physical/bin:$PATH" \
        PDK_ROOT=/tmp/forg-physical/pdk PDK=ihp-sg13cmos5l \
        /tmp/forg-physical/precheck-venv/bin/python precheck.py \
        --gds ../submission/tt_um_forg_protocol_engine.gds \
        --tech ihp-sg13cmos5l > ../precheck-reports-fixed.log 2>&1
    python3 /mnt/d/FoRG/FoRG-protocol-emulator/scripts/check-test-results.py reports/results.xml
    cat reports/results.md
)
```

The new log is `/tmp/forg-physical/manual-i2c-wire300-v2/precheck-reports-fixed.log`.
Reports are in the existing `precheck/reports/` directory. Success requires exit 0
and all nine applicable checks passing (zero errors/failures/skips), including
full KLayout DRC. Any nonzero exit or missing report is a failure: share the log
and available report, and do not retry automatically. `noclobber` prevents a
second execution from overwriting this recovery log.

### 4. Full routed-netlist core/SPI/I2C functional checks

Run after phase 1 passes; this phase is independent of the long layout checks.
Expected total runtime: **20–40 minutes** (core roughly 20 seconds, SPI 2–4
minutes, complete I2C roughly 15–30 minutes).

```bash
bash scripts/i2c-validation-handoff.sh gates
```

- Logs: `/tmp/forg-physical/manual-i2c-wire300-v2/gates/{core,spi_firmware,i2c_master}.log`.
- XML, FST and other outputs: separate `gates/<target>/test/` directories.
- Success: core **16/16**, SPI **6/6**, I2C **8/8**, zero failures/errors/skips.
  XML is checked after each test run; Make's exit status alone is insufficient.
- Uses the exact routed **unpowered netlist (`nl`)**, with matching PDK functional
  cell models. It is not SDF simulation; timing is established separately in phase 1.
- UART's existing RTL/reference tests already passed on this unchanged RTL.
  The UART dual-reference testbench does not support `GATES=yes`; no routed UART
  waveform test is claimed. No unchanged RTL regression is automatically rerun.

## Results to return

Send the timing `summary.rpt`, the final 25 lines of any failed phase log,
Magic DRC and Netgen LVS summaries, precheck summary, and the three gate XML files.
For a timing failure, include the failing corner's first path in `max.rpt` and
the violated checks from `checks.rpt`. Full multi-megabyte logs are unnecessary.

The helper contains no background jobs, waiting loops, automated retries, Git
operations or CI dispatch. Changes to repository configuration and any push that
would trigger expensive CI require a separate decision after these results.
