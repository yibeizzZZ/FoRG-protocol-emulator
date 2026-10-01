# FoRG Protocol Emulator

An experimental programmable I/O engine for the Jane Street Protocol Emulator ASIC Competition, targeting Tiny Tapeout IHP CMOS5L, 6x4 tiles.

| Target | Sources | Purpose |
|---|---|---|
| core (default) | protocol_engine.v, protocol_top.v | M2: 32x16 program memory, four registers, GPIO, waits, branches, shifts |
| uart | uart_tx.v, uart_baseline.v | M1: dedicated 8N1 UART transmitter and independent area/timing baseline |
| legacy | project.v | Original 8-byte SET/WAIT/JMP engine and its regression |

The programmable engine does not yet include complete UART/SPI/I2C firmware. UART firmware is M3; the fixed UART baseline is not part of the core's ASIC.

## Local simulation on macOS

Prerequisites: Homebrew Icarus Verilog, uv, and make. The launcher creates a temporary Python 3.11 environment and handles checkout paths containing spaces. Initial setup requires downloads; subsequent runs can reuse uv's cache.

```bash
brew install icarus-verilog
bash scripts/test-local.sh
bash scripts/test-local.sh TARGET=uart UART_CLKS_PER_BIT=4
bash scripts/test-local.sh TARGET=legacy
```

Outputs are `test/results.xml` and `test/tb.fst`. Each run overwrites them. The launcher fails on simulation errors or failed assertions.

For an existing Python environment and a checkout without spaces:

```bash
pip install -r test/requirements.txt
cd test
make
make -B TARGET=uart UART_CLKS_PER_BIT=4
make -B TARGET=legacy
```

## ASIC verification

The gds workflow builds the programmable core. The uart-baseline workflow builds the independent UART at 434 clock cycles per bit. Both run physical precheck and gate-level functional simulation. Published viewer deployment is restricted to the default branch so development branches cannot overwrite it. GitHub Pages requires repository-administrator setup.

Gate simulation uses the matching PDK revision and a zero-delay functional model; it is not SDF timing verification. Static timing is reported by the physical design flow. Passing tests do not establish exhaustive correctness or physical-device validation.

## Design and status

- [ISA and host interface](docs/isa.md)
- [Fixed UART contract and tests](docs/uart-baseline.md)
- [CI repair evidence and limitations](docs/ci-repair.md)
- [Milestone roadmap](MILESTONES.md)

All inputs must be synchronous to the design clock. No cloud FPGA access or external board is needed for local tests.
