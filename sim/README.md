# FoRG software simulation

A cycle-accurate golden model, assembler, static timing prover and
verification kit for the FoRG protocol emulator ASIC (Jane Street / Tiny
Tapeout CMOS5L competition, 6x4 tiles).

The idea is to settle the architecture in software first. Every protocol
we want to claim runs as firmware on this model and is checked against an
independent reference model before any RTL exists. The RTL then has a
golden model to match cycle for cycle.

## Quick start

```sh
cd sim
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m forg check -v            # assemble all firmware, prove timing
.venv/bin/python -m pytest -q                # unit + constrained-random tests
.venv/bin/python -m forg demo all --vcd-dir /tmp/forg   # waveforms for GTKWave/Surfer
.venv/bin/python -m forg asm firmware/uart_tx.fasm --hex uart_tx.hex
.venv/bin/python -m forg area                # pre-synthesis area budget
```

Reproduce a random test failure with `FORG_SEED=<seed> pytest -q`; the seed
is printed by every randomized test.

## What is modelled

- 4 state machines sharing a 64 x 16-bit instruction memory, one instruction
  per tick, fractional clock dividers, side-set, delays, wrap, autopush and
  autopull, IRQ flags, stall timeouts with trap vectors.
- A per-SM line unit (serializer and deserializer with NRZI, bit stuffing,
  Manchester, differential output, SE0 detection, edge-aligned clock
  recovery) and a configurable bit-serial CRC.
- Tiny Tapeout pin map with output-only and input-only pins, open-drain pins,
  pull-ups, 2-flop input synchronizers (with bypass), registered outputs,
  wired-AND net resolution and bus contention detection.
- Full spec: [docs/ISA.md](docs/ISA.md).

## Firmware and verification status

| firmware            | protocol                       | checked against                          |
|---------------------|--------------------------------|------------------------------------------|
| `uart_tx`           | UART 8N1                       | `UartMonitor` (edge timing error)        |
| `uart_rx`           | UART 8N1, framing errors       | `UartDriver` with +-3% baud error        |
| `spi_cpha0/1`       | SPI modes 0-3, any length      | `SpiTarget` (mode checks)                |
| `i2c_master`        | I2C std/fast, clock stretching, repeated START | `I2cTarget` (timing checker) |
| `usb_ls_tx`         | USB 1.1 low speed, CRC16 in hardware | `UsbLsMonitor` (CRC5/16, stuffing, EOP) |
| `usb_ls_rx`         | USB low-speed receive/sniff    | `UsbLsDriver` with +-1.5% clock error    |
| `eth10_tx`          | 10BASE-T frames + link pulses  | `Eth10Monitor` (zlib CRC32 FCS)          |
| `jtag`              | IEEE 1149.1                    | `JtagTap` (IDCODE, IR, user DR)          |
| `logic_analyzer`    | run-length capture             | unit tests                               |

The reference models in `forg/devices` are written from the protocol specs
and never call into the chip model, so the two check each other. The USB CRCs
are cross-checked against published check values, and the Ethernet FCS
against `zlib.crc32`.

## Verification methodology

1. **Static timing proofs.** FoRG instructions have fixed cycle costs, so
   `.assert_cycles` in firmware is checked by enumerating every
   control-flow path. A bit-banging bug where one branch is a cycle longer
   fails at assembly time, not on the bench.
2. **Constrained-random protocol tests.** Random data, clock dividers,
   baud/clock errors, SPI modes, I2C addresses and clock stretching, USB
   packet types and Ethernet frames, all scored by the reference models.
3. **Negative tests.** Framing errors, NAKs, stuffing violations, bus
   contention and a deliberately mistimed program must all be detected.
4. **ISA round-trip fuzzing.** Random legal instruction words are
   disassembled, re-assembled and compared.
5. **Concurrency.** UART, SPI, I2C and the logic analyzer run at once from
   one shared instruction memory, with SPI relocated to other pins purely
   through configuration.

## Next steps toward silicon

- Write the RTL (Verilog or Hardcaml) against `docs/ISA.md` and load the same
  `.hex` images produced by `forg asm --hex`.
- Lockstep co-simulation: run cocotb with the RTL and this model side by side
  on identical stimulus and compare pins and SM state every cycle.
- Replace `forg area` estimates with Yosys `stat` on the CMOS5L cells, then
  trim (FIFO depth, X/Y width, SM count, SRAM imem) to close on 6x4 tiles.
- Bring up on an FPGA using the same firmware and BFMs as host-side
  test vectors.
