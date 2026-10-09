# FoRG Protocol Emulator

An experimental programmable I/O engine for the Jane Street Protocol Emulator ASIC Competition, targeting Tiny Tapeout IHP CMOS5L, 6x4 tiles.

| Target | Sources | Purpose |
|---|---|---|
| core (default) | protocol_engine.v, protocol_top.v | M2: 32x16 program memory, four registers, GPIO, waits, branches, shifts |
| uart_firmware | M2 core plus isolated testbench reference | M3: firmware 8N1 TX, compared cycle-by-cycle with M1 |
| i2c_firmware | protocol_engine.v, protocol_top.v | Address-write probe, ACK/NACK and clock stretching; isolated host programming and bus-idle qualification required |
| spi_firmware | protocol_engine.v, protocol_top.v | Full-duplex Mode-0 SPI firmware and independent pin-level slave model |
| uart | uart_tx.v, uart_baseline.v | M1: dedicated 8N1 UART transmitter and independent area/timing baseline |
| legacy | project.v | Original 8-byte SET/WAIT/JMP engine and its regression |

M3 firmware transmits one or two UART bytes on the unchanged M2 engine at
434 cycles/bit. SPI firmware exchanges one or two bytes with configurable
clock timing on the same engine. I2C firmware adds a 32-word address-write
probe with ACK/NACK and clock stretching; data reads/writes remain unsupported.
The fixed UART baseline is an independent reference and is not part of the core's ASIC.

## Local simulation on macOS

Prerequisites: Homebrew Icarus Verilog, uv, and make. The launcher creates a temporary Python 3.11 environment and handles checkout paths containing spaces. Initial setup requires downloads; subsequent runs can reuse uv's cache.

```bash
brew install icarus-verilog
bash scripts/test-local.sh
bash scripts/test-local.sh TARGET=uart_firmware
bash scripts/test-local.sh TARGET=spi_firmware
bash scripts/test-local.sh TARGET=i2c_firmware
bash scripts/test-local.sh TARGET=uart UART_CLKS_PER_BIT=4
bash scripts/test-local.sh TARGET=legacy
```

Results are written to `test/results.xml`; waveforms use `test/tb.fst` or the
firmware target's named `.fst` file. Repeated runs overwrite these files. The launcher fails on simulation errors or failed assertions.

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
- [Firmware UART, timing and measurements](docs/uart-firmware.md)
- [Full-duplex SPI firmware and architecture evaluation](docs/spi-firmware.md)
- [I2C address probe, host preconditions and architecture limits](docs/i2c-firmware.md)
- [CI repair evidence and limitations](docs/ci-repair.md)
- [Measured verification results](docs/results.md)
- [Contribution workflow](CONTRIBUTING.md)
- [Milestone roadmap](MILESTONES.md)

All inputs must be synchronous to the design clock. No cloud FPGA access or external board is needed for local tests.
