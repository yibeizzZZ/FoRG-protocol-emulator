# Extended PIO implementation and validation

Measured on 2026-10-10, against the original 32-word core at `e220bfb`.
The extended RTL under test has `src/protocol_engine.v` SHA-256
`4689c43033c53b7927c0d0188402a2809ff4ba30ad6e5145ff800f75d054dfac`.
The top-level wrapper and fixed UART reference are unchanged.

## Functional evidence

All 34 Python tooling tests pass. The complete RTL regression has **83 passing
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
tests and the seven detailed I2C master tests. Gate simulation uses the matching PDK functional cell models without
SDF delays; static timing is a separate physical-flow result.

## Area comparison

The baseline comes from [the successful `e220bfb` GDS run](https://github.com/yibeizzZZ/FoRG-protocol-emulator/actions/runs/37952701964).
The new design uses the same LibreLane configuration, 6x4 tile allocation,
20 ns clock constraint, standard-cell library and pinned PDK.

| Metric | Original core | Extended core |
| --- | ---: | ---: |
| Program words | 32 x 16 | 128 x 16 |
| Data bytes | 0 | 32 |
| Working registers | 4 x 8 | 4 x 8 |
| Return stack | None | 4 x 7 |
| WAIT counter bits | 8 | 12 |
| Synthesized cells | 2,847 | 16,109 |
| Synthesized cell area | 59,239.026 µm² | 257,216.564 µm² |

The mapped area increases by **197,977.538 µm², or 4.342x total**. This is an
aggregate cost for the complete architecture change, not an isolated estimate
of any one opcode. Sequential cells account for 133,592.458 µm² in the new
design. Memories map to standard-cell registers and selection/write logic;
there is no SRAM macro. Mapping structure and memory access logic should be
measured in future ablations before attributing the increase to individual
features.

The same external die remains 1289.28 x 710.64 µm, with a 902,417 µm² placement
core. Synthesized logic area excludes later buffering, clock tree and fill.
It must not be confused with routed standard-cell area or die area.

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

For a local run, use the pinned tool/PDK revisions above and the action's
`GDS_logs/src/config_merged.json`, preserving its companion `tt` support
directory. Copy this revision's `src/*.v` into that configuration's source
directory. With the PDK installed and Docker available:

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
