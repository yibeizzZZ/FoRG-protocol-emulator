# SPI Mode-0 firmware on the current M2 engine

The current engine supports full-duplex SPI without changing RTL or ISA.
This experiment implements one or two arbitrary eight-bit bytes, MSB first,
with one reusable bit loop. CS stays asserted across both bytes, with no
extra inter-byte clock or gap. Both RX bytes remain available after completion.
The default image is **31 words / 62 bytes**, with **32 system cycles per bit**
and **1.5625 MHz SCLK at 50 MHz**. Timing is configurable at generation time.

## Feasibility and design decisions

The resource analysis was done against `protocol_engine.v`, the ISA cycle
contract, and M3 before implementation. A naive allocation of independent
TX, RX, GPIO scratch, bit counter and retained first RX would need five
registers; M2 has four. A combined TX/RX shift register removes that pressure:
its MSB is sent, then it shifts left and receives the sampled MISO bit at
its newly cleared LSB. After eight iterations it contains the received byte.
Incoming RX bits cannot reach the transmitted MSB before the byte finishes.

| Register | Runtime role | At completion |
|---|---|---|
| R0 | Combined TX/RX shift register | Last received byte |
| R1 | MOSI/SCLK pattern, then masked MISO, then boundary scratch | Scratch |
| R2 | Total remaining bits, initialized to 8 or 16 | Zero |
| R3 | Retained first RX byte after the byte boundary | First RX for two bytes; zero for one |

No register-register OR/add is available. Appending a one uses `ADDI R0,1`
after SHL clears bit zero; appending zero uses JMP. Both paths take the same
time. Ordinary bits take a four-cycle pad that the longer byte-dispatch path
skips, preserving uniform SCLK timing. This is control-flow balancing, not
an unrolled waveform or payload-specific instruction sequence.

The complete default allocation is 31 words: 4 for initial idle/direction
and data/count setup; 3 for CS assertion and initial/loop balancing delays;
14 for bit output, sampling and falling edge; 4 for bit counting and looping;
3 for completion/idle; and 3 for retaining RX/loading the second byte/dispatch.
The RTL tests verify the resulting timing and both received bytes. No
architectural change was needed or made.

## Pins and electrical timing contract

| Signal | Pin | Direction |
|---|---|---|
| MOSI | `uio[7]` | Output |
| MISO | `uio[0]` | Input |
| SCLK | `uio[1]` | Output, idle LOW |
| CS | `uio[2]` | Output, active LOW |
| Unused | `uio[6:3]` | Inputs |

The direction mask is `0x86`. MOSI is allocated to bit 7 so an ANDI selects
the current MSB without a seven-instruction shift sequence. This is a firmware
pin allocation, not a restriction of the hardware. Other mappings/encoded
shift directions are future generator experiments; this API does not promise
arbitrary pin assignment. The generator never drives MISO.

Mode 0 has idle-low clock, leading rising sample edges and trailing falling
setup edges ([Microchip SPI transfer modes](https://onlinedocs.microchip.com/oxy/GUID-A299F4E7-F38C-4DF5-96C0-A87B9F519156-en-US-4/GUID-8A5B8750-B99E-4176-834E-E44E98F4A098.html)).
Here the single-issue engine raises SCLK with OUT and executes READ on the
**following system edge: 20 ns later at 50 MHz**. It cannot drive SCLK and
read MISO in one instruction. A compatible slave must set MISO after CS
assertion/falling SCLK and keep it stable throughout the high phase. This
delayed high-phase capture is explicit, not a claim of edge-coincident sampling.
The engine has no input synchronizer; device propagation, setup/hold, and
board delays must satisfy the M2 synchronous input contract.

MOSI can change during the low phase, including at falling SCLK, and remains
stable across rising SCLK and throughout the high phase. Minimum MOSI setup
is two system cycles at the fastest supported setting. Initial CS-to-first
rising-edge time equals one configured half-period. CS deasserts six cycles
after the last falling edge. The active direction persists in the final
idle loop, with CS HIGH and SCLK/MOSI LOW. Reset or leaving RUN releases all
outputs; use an external CS pull-up if deselection is required while released.

## Logic, configuration and payload separation

`scripts/pio_firmware.py` is the small shared instruction builder used by
both UART and SPI. It owns only instruction encoding/operand checks,
symbolic labels, branch resolution, exact-cycle WAIT splitting and the
32-word limit. It contains no protocol names, pin mappings, payload policy,
or protocol timing. M3's UART image remains byte-for-byte unchanged.

`generate_spi_master(payload, *, half_period_cycles=16)` in
`scripts/spi_firmware.py` owns the SPI state machine and its fixed pin mapping.
`half_period_cycles` is configuration. `payload` is separate data; changing
values in a two-byte payload changes only the two MOVI immediates, not the
loop, instruction topology or timing. No payload bit causes generator-side
waveform unrolling. The checked-in example sends `0xA5, 0x3C`.

Runtime host loading was evaluated rather than assumed. The host can write
only instruction bytes while RUN is zero; every stopped edge clears all
registers. In RUN there is no host register-write port, FIFO or data RAM.
READ accesses GPIO, whose pins are already shared with the SPI peripheral.
A framed streaming interface on spare GPIO would need its own handshake,
packing and scheduling, with only one word free at the default setting.
This implementation therefore embeds validated payload bytes in MOVI and
requires reloading between separate transactions. This does **not** prove
that streaming or more than two bytes is impossible with every M2 program;
it states the tested generator's scope and remaining resource budget.

## Generate, load and read results

```bash
# Repository root; decimal and 0x hexadecimal payloads are accepted.
python3 scripts/spi_firmware.py 0xA5 0x3C --output firmware/spi_master.hex
python3 scripts/spi_firmware.py 0x37 146 --half-period 32 --output /tmp/spi.hex
python3 scripts/spi_firmware.py 255 --half-period 13
python3 -m unittest discover -s scripts -p 'test_*.py' -v
bash scripts/test-local.sh TARGET=spi_firmware
python3 scripts/check-test-results.py test/results.xml
```

No `--output` prints the hex words to stdout. Empty payloads, more than two
bytes, non-byte values and unsupported half-periods fail without overwriting
an existing destination. Each line is a 16-bit instruction. Use the
[existing M2 loader](isa.md): RUN=0, write both bytes of each word through
`uio_in`/the address and byte-select fields, then RUN=1. Keep `ena=1` and
`rst_n=1` through the transaction. Reset invalidates the loaded image.

After CS returns HIGH, keep RUN set and select the public register readback:

- `ui_in=0xC0`: `uo_out` is R0, the last RX byte.
- `ui_in=0xC3`: `uo_out` is R3, the first RX byte for a two-byte transaction.
- `ui_in=0xA0`: status, including fault/halt/PC, without modifying execution.

Register reads do not clock or modify the engine. Read both results before
leaving RUN, resetting or loading another transaction, which clears them.
Completion is indicated externally by CS deassertion, not a hardware busy
register or interrupt. A one-byte transfer returns only R0; R3 remains zero.

## Instruction and cycle accounting

Default `H=16` program, addresses decimal:

| Address | Instructions | Purpose |
|---|---|---|
| 0–3 | SET 4; DIR 0x86; MOVI R0,tx0; MOVI R2,8*n | Idle, pins and transfer data |
| 4–5 | SET 0; WAIT 3 | Assert CS and initial low-phase balance |
| 6 | WAIT 3 | Ordinary-bit balance; second-byte dispatch skips this |
| 7–9 | MOV R1,R0; ANDI R1,0x80; OUT R1 | MSB to MOSI with clock LOW |
| 10 | WAIT 2 | Configurable low-phase padding |
| 11–12 | ADDI R1,2; OUT R1 | Raise SCLK without changing MOSI |
| 13–15 | READ R1; ANDI R1,1; SHL R0 | Read MISO and vacate RX insertion bit |
| 16–18 | JNZ R1,18; JMP 19; ADDI R0,1 | Balanced receive-zero/one paths |
| 19–20 | WAIT 9; SET 0 | Configurable high padding, then falling edge |
| 21–24 | ADDI R2,255; MOV R1,R2; ANDI R1,7; JNZ R1,6 | Count bits, detect byte boundary |
| 25–27 | JNZ R2,28; SET 4; JMP 27 | Dispatch second byte or finish in idle |
| 28–30 | MOV R3,R0; MOVI R0,tx1; JMP 7 | Preserve RX and reuse the bit loop |

`WAIT n` costs `n+1` cycles. The shared builder emits a delay of N total
cycles with one or more WAITs, or no instruction for a zero delay.

- High phase: READ + ANDI + SHL + JNZ + (JMP or ADDI) + delay(H−6) +
  falling SET = **H** cycles regardless of MISO value.
- Normal low phase: four counter/boundary instructions + delay(4) +
  MOV/ANDI/OUT + delay(H−13) + ADDI/OUT = **H** cycles.
- Inter-byte low phase: four counter/boundary instructions + second-byte
  JNZ + MOV/MOVI/JMP + MOV/ANDI/OUT + delay(H−13) + ADDI/OUT = **H** cycles.
- First low phase: initial delay(4) + ordinary delay(4) + MOV/ANDI/OUT +
  delay(H−13) + ADDI/OUT = **H** cycles after CS assertion.

Thus each bit takes **2H** cycles and a byte takes **16H**, without an
inter-byte penalty. CS-low duration for n bytes is `16*n*H + 6` cycles,
including the final six-cycle hold. At H=16 the two-byte interval is 512
clocking cycles (10.24 us) plus six hold cycles (0.12 us).

## Measured resource and timing results

Icarus/cocotb pin observations at a 20 ns clock; frequencies are computed
from observed periods. Each setting below exercised four full-duplex
two-byte transactions, including unrelated TX/RX data and both branch paths.
The test emits `test/output/spi-firmware-measurements.json`, retained by CI.

| H | Words / bytes | Program utilization | High / low cycles | Cycles/bit | SCLK at 50 MHz |
|---|---|---|---|---|---|
| 13 | 30 / 60 | 93.75% | 13 / 13 | 26 | 1.923077 MHz |
| 14 | 31 / 62 | 96.875% | 14 / 14 | 28 | 1.785714 MHz |
| 16 (default) | 31 / 62 | 96.875% | 16 / 16 | 32 | 1.562500 MHz |
| 32 | 31 / 62 | 96.875% | 32 / 32 | 64 | 781.250 kHz |
| 262 | 31 / 62 | 96.875% | 262 / 262 | 524 | 95.419847 kHz |
| 263 | 32 / 64 | 100% | 263 / 263 | 526 | 95.057034 kHz |
| 269 | 32 / 64 | 100% | 269 / 269 | 538 | 92.936803 kHz |

H=13 needs no low WAIT; H=14..262 uses one WAIT per padding segment.
At H=263 the high padding exceeds 256 cycles and needs another word. At
H=270 the low padding also needs another word: **33 words**, so generation
fails rather than truncating/wrapping the program. H<13 cannot fit this
loop's ordinary/dispatch work into its low phase. These bounds describe
this balanced firmware, not absolute maximum/minimum rates of all possible
M2 firmware. Single-byte images retain the same reusable two-byte-capable
layout, with the second-byte block unreachable.

| Property | M3 UART TX | SPI Mode 0 |
|---|---|---|
| Hardware / ISA | Unchanged M2 | Same unchanged M2 |
| Default words | 27 | 31 |
| Registers | Four (TX, scratch, bit count, frame count) | Four (TX/RX, scratch, bit count, retained RX) |
| Bits | LSB first, TX only | MSB first, full duplex |
| Default cycles/bit | 434 | 32 |
| Timing configuration | Fixed at 434 | H=13..269 |
| Payload per image | One or two bytes | One or two bytes |
| Runtime input/result | No RX | MISO capture and two stable RX bytes |
| Shared code | Encoding, labels, WAIT accounting, capacity checking | Same builder |

No synthesis or physical/FPGA run is claimed by these simulation measurements.
The underlying RTL and its existing physical evidence are unchanged.

## Verification and reproduction

`test/spi_reference.py` is an independent Mode-0 slave/monitor. It consumes
only observed GPIO, OE and cycle numbers, loads reply MSB on CS assertion,
advances reply bits on falling SCLK and decodes MOSI on rising SCLK. It
imports no generator, opcode, firmware PC or register model. Tests also
verify actual RX through the external register mux. No SPI hardware block
or TX-to-MISO loopback substitutes for the slave's unrelated reply data.

Six cocotb tests cover directed/random single bytes, 28 two-byte timing-sweep
transactions, the checked-in image, successive transactions, reset/stop
mid-transfer, reprogramming and pause behavior. They check every SCLK phase,
first-bit setup, high-phase MOSI stability, CS boundaries/hold, unused output
enables, idle retention and both stored RX values. Pausing explicitly stretches
wall-clock timing; its test checks state retention then aborts the frame.

Six firmware mutations must fail: a wrong TX bit, a one-cycle low-phase error,
a cleared MISO mask, missing READ, premature CS, and bypassed RX branching.
Unit tests exercise all 256 payload values, bit-loop topology independence,
capacity boundaries, invalid CLI input, and unchanged UART hex reproduction.
The payload sweep is generator coverage, not an exhaustive RTL sweep.
Local verification passed all **33 RTL tests** (core 5, UART firmware 5,
SPI firmware 6, legacy 2, fixed UART 3 at each of five divisors) and **22
Python unit tests**. Independent review found an import compatibility
regression in the shared-builder refactor; it was reproduced, fixed, and
covered by subprocess tests for CLI, package import and file-based import
from an unrelated directory. All regressions were rerun after that fix.
A separate comparison confirmed all 512 single/complementary-pair UART
images remain identical to M3 commit `bb5d79d`.

```bash
set -e
python3 -m unittest discover -s scripts -p 'test_*.py' -v
for target in core uart_firmware spi_firmware legacy; do
  bash scripts/test-local.sh TARGET="$target"
  python3 scripts/check-test-results.py test/results.xml
done
for divisor in 1 2 4 17 434; do
  bash scripts/test-local.sh TARGET=uart UART_CLKS_PER_BIT="$divisor"
  python3 scripts/check-test-results.py test/results.xml
done
```

CI preserves all existing runs and adds SPI with immediate XML validation,
`results-spi-firmware.xml`, `spi_firmware.fst` and the measurement JSON.
Local SPI simulation produces `test/tb.fst`; copy it before another target
overwrites it. With installed dependencies, `cd test && make -B TARGET=spi_firmware`
is equivalent to the launcher. Gate/board-level SPI validation is not part
of the reported RTL results.

## General-purpose improvements to evaluate later

- A small FIFO or host-visible data register/handshake could separate runtime
  payloads and received results from code for UART, SPI and I2C. It would also
  avoid repeated program loading, whose stopped mode clears all results.
- Generic masked GPIO update or a shift-to-pin operation could reduce MOV/
  ANDI/OUT and pin-allocation pressure. Compare code size, timing and area
  across all three protocols before adding an instruction.
- A register-register combine or configurable shift-in primitive could remove
  the balanced branch used for sampled bits. It helps SPI RX and UART/I2C
  receive paths rather than adding an SPI-specific controller.
- A wider/repeatable delay primitive could reduce WAIT word pressure shared
  by UART bit periods and slow SPI/I2C phases. Alternatively, explore looped
  firmware delays and their register/timing cost first.
- Counted-loop support could reduce boundary bookkeeping; retained-output
  sleep/event waiting could avoid busy idle loops without releasing pins.
  Input capture tied to an output event could address the one-cycle sample
  offset, but requires a separate, general-purpose timing design and approval.
- Firmware-side dispatch/layout/pin encoding experiments should precede RTL
  optimization. The one-word default margin and four occupied registers are
  measurements that motivate experiments, not permission to redesign the ISA.

Not provided: modes 1–3, arbitrary pin remapping, indefinite streaming, more
than two retained RX bytes, or autonomous repeated transactions without host
loading. None is silently emulated with precomputed waveform tables.
