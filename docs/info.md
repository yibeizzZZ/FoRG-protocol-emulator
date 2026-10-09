## How it works

A minimal programmable I/O engine with 32 16-bit instructions, four 8-bit registers, byte-wide GPIO and direction control, deterministic waits, conditional and unconditional branches, shifts, and debug readback. The clock target is 50 MHz; actual timing must be checked in build reports.

M3 UART TX firmware sends one or two 8N1 bytes at 434 cycles per bit on uio[0],
using the unchanged M2 core. See docs/uart-firmware.md for loading and timing.
SPI Mode-0 firmware also exchanges one or two bytes full duplex, with
configurable clock timing; see docs/spi-firmware.md for pin mapping and the
one-system-cycle MISO capture offset. I2C firmware is a later milestone.
The dedicated M1 UART is an
independent baseline, not a block in this design.

## Host interface

All inputs must be synchronous to clk. rst_n is synchronous active-low reset; reset invalidates the program and requires reloading. ena pauses sequential state. GPIO is released while reset, stopped, halted, or faulted.

With ui_in[7]=0, ui_in[6] enables writing uio_in to program memory. ui_in[4:0] selects a word; ui_in[5] selects its low/high byte. Load both bytes of each instruction before running. Setting ui_in[7]=1 runs from PC 0.

During execution, ui_in[6:5] selects uo_out readback: 00=GPIO data, 01={fault,halted,wait_active,PC[4:0]}, 10=register selected by ui_in[1:0], 11=direction mask. uio_in is GPIO input; uio_out/oe provide output data and enables.

Instruction encoding and cycle semantics are documented in docs/isa.md.

## How to test

Run `bash scripts/test-local.sh` from the repository root. The regression uses pin-level readback to compare execution with a Python ISA model, including waits, pause, reset, invalid fetches, reprogramming and PC wrap. The same core tests run on the generated gate netlist.

## External hardware

No external hardware is needed for simulation. Real pin-level and protocol validation remains future work. External devices require suitable electrical interfaces and synchronized inputs; this design is not an I2C PHY.
