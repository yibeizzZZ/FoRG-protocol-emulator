<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

This project is a programmable general-purpose protocol emulator ASIC for the Jane Street Protocol Emulator ASIC Competition.

The current Week 1 implementation is an infrastructure placeholder used to verify the Tiny Tapeout CMOS5L simulation, CI, synthesis, and physical-design flow. The final design will replace the placeholder logic with a programmable protocol execution engine capable of implementing multiple digital protocols in firmware.

Initial target protocols include UART, SPI, and I2C.

## How to test

Run the RTL simulation from the `test` directory:

```bash
make clean
make
```

## External hardware

No external hardware is required for the current Week 1 simulation and CI setup.
