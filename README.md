![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg) ![](../../workflows/sim/badge.svg)

# FoRG Protocol Emulator

An open-source, general-purpose protocol emulator ASIC: four small,
cycle-exact state machines that run UART, SPI, I2C, JTAG, USB low-speed and
10BASE-T Ethernet as firmware instead of fixed logic. Built for the
[Jane Street protocol emulator ASIC competition](https://blog.janestreet.com/)
on IHP's 130nm CMOS5L process, 6x4 tiles.

## Repository layout

- [sim/](sim/README.md): software simulation, assembler, firmware, timing proofs and the verification suite. Start here.
- [sim/docs/ISA.md](sim/docs/ISA.md): architecture and instruction set specification.
- `src/`: RTL (Verilog).
- `test/`: cocotb RTL testbench.
- [docs/info.md](docs/info.md): project datasheet.
- [info.yaml](info.yaml): project metadata, pinout and tile size used by the chip build.

## Quick start

```sh
cd sim
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m forg check -v
.venv/bin/python -m pytest -q
.venv/bin/python -m forg demo all --vcd-dir /tmp/forg
```

## Building the chip

The chip is fabricated through Tiny Tapeout, the shared-wafer service the
competition uses. On every push, GitHub Actions turns the Verilog in `src/`
into a chip layout (GDS) with [LibreLane](https://www.zerotoasiccourse.com/terminology/librelane/)
and runs the RTL tests. To build locally, see the
[local hardening guide](https://www.tinytapeout.com/guides/local-hardening/).
