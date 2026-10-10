# Extended PIO implementation and validation

Measured on 2026-10-10, against the original 32-word core at `e220bfb`.
The extended RTL under test has `src/protocol_engine.v` SHA-256
`cc05e92b2cbcc99d535ab45bad5c31d26e86d3d621e2fd98400d065297fc4c4a`
(commit `b12be1b`).
The top-level wrapper and fixed UART reference are unchanged.

## Functional evidence

The [remote test run for `b12be1b`](https://github.com/yibeizzZZ/FoRG-protocol-emulator/actions/runs/38068071702)
passed. Its 15 named XML reports were downloaded and independently recounted:
83 passes, zero skips and zero failures. XML, waveforms and timing JSON are
available in that run's `test-results` artifact.

The RTL at `b12be1b` independently repeated all 83 executions locally,
with zero skips/failures. All 34 Python tooling tests pass. The complete RTL regression has **83 passing
test executions**, with no skips or failures:

| Suite | Executions |
| --- | ---: |
| Core, original ISA plus extended mode and both loaders | 16 |
| UART firmware, including fixed-reference waveform comparison | 5 |
| SPI firmware | 6 |
| Original legacy engine | 2 |
| Fixed UART at divisors 1, 2, 4, 17, 434 | 15 |
| Legacy I2C probe, three bus-delay configurations | 15 |
| Complete I2C master, three bus-delay configurations | 24 |

The I2C configurations are ideal wires; 300 ns SCL falling delay; and 1000 ns
rise plus 300 ns SDA falling delay. Complete transactions are checked by an
independent edge-driven target with persistent register memory, rather than
an instruction-level oracle. This includes write, read, repeated-START register
read, RAM-only reconfiguration, ACK/NACK, stretching, timeout, reset, pause,
invalid configuration and missing STOP. Seven actual firmware mutations must
be rejected, in addition to mutations of captured traces.

The synthesized standard-cell netlist passes all 16 core tests, all six SPI
tests and all eight I2C master tests (the seven detailed tests plus a separate
short write/readback/combined-transfer run). Gate simulation uses the matching
PDK functional cell models without SDF delays; static timing is a separate
physical-flow result.

## Area comparison

The baseline comes from [the successful `e220bfb` GDS run](https://github.com/yibeizzZZ/FoRG-protocol-emulator/actions/runs/37952701964).
The new design preserves the 6x4 tile allocation, 20 ns clock constraint,
standard-cell library and pinned PDK. It explicitly optimizes all three process
corners and checks setup, slew and capacitance at every corner; the baseline
configuration used the typical corner for general PnR and enforced setup
only there. Its dedicated resizer stages already loaded all STA corners.

| Metric | Original core | Extended core |
| --- | ---: | ---: |
| Program words | 32 x 16 | 128 x 16 |
| Data bytes | 0 | 32 |
| Working registers | 4 x 8 | 4 x 8 |
| Return stack | None | 4 x 7 |
| WAIT counter bits | 8 | 12 |
| Synthesized cells | 2,847 | 16,421 |
| Synthesized cell area | 59,239.026 µm² | 257,169.238 µm² |

The mapped area increases by **197,930.212 µm², or 4.341x total**. This is an
aggregate cost for the complete architecture change, not an isolated estimate
of any one opcode. Sequential cells account for 133,592.458 µm² in the new
design. Memories map to standard-cell registers and selection/write logic;
there is no SRAM macro. Mapping structure and memory access logic should be
measured in future ablations before attributing the increase to individual
features.

The same external die remains 1289.28 x 710.64 µm, with a 902,417 µm² placement
core. Synthesized logic area excludes later buffering, clock tree and fill.
It must not be confused with routed standard-cell area or die area.

## Timing failure and correction

The first complete implementation (`64a1c7c`) passed functional tests but
failed extracted slow-corner setup at 20 ns: **−4.543 ns**, 271 violating
endpoints. It also had five slew violations. Typical-corner setup was
+4.613 ns, so the default typical-only checker could misleadingly pass.
This revision is not evidence of 50 MHz closure.

Yosys shared register-read ports using predicates that included the global
fault check. The resulting path serialized program fetch, indirect-register
read, RAM-validity checking, a second register read, and branch evaluation.
Marking the four-register array `mem2reg` removes that unnecessary dependency
without changing state, ISA semantics or instruction cycles. Mapped-netlist
inspection found eight operand selectors depending on data validity before
the change and zero afterward. Sequential area remains unchanged; mapped
area decreases by 47.326 µm².

The pinned LibreLane version defaults PnR to `DEFAULT_CORNER`, despite its
configuration documentation saying otherwise. `src/config.json` therefore
selects all three IHP corners explicitly and enables all-corner setup, slew
and capacitance checks. Hold checks already cover all corners. The dedicated resizer stages already loaded all STA corners; general PnR
corner selection and signoff checking are separate controls.

The `b12be1b` experiment improved extracted slow-corner setup to −1.606 ns
but still failed, with 288 setup and 11 slew violations. Both post-global-route
repair stages were disabled. The next configuration enables design repair
(for slew/capacitance) and timing repair using routed parasitic estimates,
retaining the same RTL. An explicit corner-by-corner comparison showed
+3.351 ns estimated slow-corner slack versus −1.606 ns extracted: the estimate
was 4.958 ns optimistic. Enabling repair alone therefore performed no resizing.
The internal setup repair margin is tightened to 6 ns to cover this observed
gap; the operating/signoff clock remains 20 ns. The two slew-failing nets
had estimated/extracted slews of 1.691/3.063 ns and 1.773/2.857 ns. A 50%
internal slew repair margin allows for the observed worst 1.81x difference;
the library signoff limit remains unchanged at 2.5074 ns. These margins
strengthen optimization targets, not the reported timing requirements. Final
physical results for this correction are pending.

## Physical flow provenance

- LibreLane `3.1.0.dev3`, Yosys `0.66`.
- Container `ghcr.io/librelane/librelane@sha256:d109140b8f17fc54f4fca998beb8124f4949404ec52e339eebd2250854a18b5a`.
- Tiny Tapeout support tools `d66cf179e7bc4d296362ab7e2e3b344dc3c4f665`.
- IHP Open PDK `2bbec755dc67ca3db0261c3d6163e15735d66710`, `ihp-sg13cmos5l`.
- The local physical flow is in progress; final extracted timing, DRC/LVS,
  GDS and precheck results will be recorded after completion.

## Reproduction

The complete RTL commands and firmware generation are in
[the I2C master documentation](i2c-master.md#reproduction-and-verification).
Each CI invocation preserves its XML and waveform and then validates XML;
Make's exit status alone is not treated as proof of passing tests.

To run the repository's physical flow on this branch:

```bash
gh workflow run gds.yaml --ref feature/i2c-firmware
gh run list --workflow gds.yaml --branch feature/i2c-firmware
gh run watch RUN_ID --exit-status
gh run download RUN_ID --dir build/physical
```

For a local run, use the pinned tool/PDK revisions above and generate a fresh
`src/config_merged.json` from **this revision's** `src/config.json` and the
Tiny Tapeout generated user configuration. Alternatively, use the merged
configuration and companion `tt` directory from this revision's GDS artifact.
Do not copy only RTL into an older baseline configuration: that would restore
typical-only optimization/checking. With the PDK installed and Docker available:

```bash
python -m librelane --dockerized --docker-no-tty --manual-pdk \
  --pdk ihp-sg13cmos5l --pdk-root /absolute/path/to/pdk \
  --run-tag validation --hide-progress-bar src/config_merged.json
```

Copy the resulting power-pin-free `final/nl/tt_um_forg_protocol_engine.nl.v`
to `test/gate_level_netlist.v`, then run functional gate tests:

```bash
PDK_ROOT=/absolute/path/to/pdk GATES=yes bash scripts/test-local.sh TARGET=core
PDK_ROOT=/absolute/path/to/pdk GATES=yes bash scripts/test-local.sh TARGET=spi_firmware
PDK_ROOT=/absolute/path/to/pdk GATES=yes bash scripts/test-local.sh TARGET=i2c_master
```

## Architectural consequences

The experiment satisfies multi-byte I2C using reusable firmware operations and
runtime RAM, but has only one free instruction word. Its next bottlenecks are
instruction density and standard-cell memory cost, not a missing I2C state
machine. Candidate experiments include SRAM-backed program/data storage,
compact generic pin-test branches, reducing register spills, and a common
host FIFO for UART/SPI/I2C streaming. These are proposals, not implemented
optimizations. Existing UART and SPI firmware deliberately remain unchanged.

The digital model and physical design flow do not establish analog I2C pad/RC
compliance or board operation. GPIO retains the synchronous-input contract.
There is no multi-controller arbitration, unlimited streaming or fabricated
device validation. Timing at the configured 50 MHz does not establish Fmax.
