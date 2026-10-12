# SPI Modes 0–3 firmware using the legacy M2 ISA

The original M2 engine supports full-duplex SPI without changing its RTL or ISA.
The generator implements all four CPOL/CPHA modes, with one or two arbitrary eight-bit bytes, MSB first,
with one reusable bit loop. CS stays asserted across both bytes, with no
extra inter-byte clock or gap. Both RX bytes remain available after completion.
The default Mode-0 image is **31 words / 62 bytes**, with **32 system cycles per bit**
and **1.5625 MHz SCLK at 50 MHz**. Timing is configurable at generation time.

The extended core added for complete I2C runs this image in default legacy
mode without firmware changes. Mode 0's original API and every generated word
are preserved; the checked-in `firmware/spi_master.hex` is unchanged. Modes 1–3
also use only legacy instructions, the same four registers, and at most 32 words.
There are no RTL, ISA, pin-map or physical-configuration changes. The original
Mode-0 instruction accounting remains below;
current architecture costs are in [extended-core validation](pio-extended-validation.md).

## Mode selection and supported timing

```python
generate_spi_master(payload, *, half_period_cycles=16, mode=0)
```

`mode` is an integer from 0 through 3; omitting it preserves the original API.
Booleans, floats and strings are rejected. H denotes `half_period_cycles`, in
system clocks, with identical bounds for one- and two-byte transactions.

| Mode | CPOL/CPHA | Idle SCLK | Sample edge | Setup edge | H range | Words at H=16 |
|---|---|---|---|---|---|---|
| 0 | 00 | LOW | Leading/rising | Trailing/falling | 13–269 | 31 |
| 1 | 01 | LOW | Trailing/falling | Leading/rising | 16–272 | 30 |
| 2 | 10 | HIGH | Leading/falling | Trailing/rising | 14–262 | 32 |
| 3 | 11 | HIGH | Trailing/rising | Leading/falling | 15–515 | 30 |

All modes default to H=16: 32 cycles/bit, 1.5625 MHz at 50 MHz. In general,
SCLK frequency is `system_clock_hz / (2*H)`. Unsupported values fail before
writing a CLI destination. These are limits of these balanced firmware loops,
not fundamental limits of the ISA or electrical timing guarantees.

| Mode | Program size by H |
|---|---|
| 0 | H=13: 30; H=14–262: 31; H=263–269: 32 words |
| 1 | H=16: 30; H=17–259: 31; H=260–272: 32 words |
| 2 | H=14: 31; H=15–262: 32 words |
| 3 | H=15: 29; H=16–259: 30; H=260–271: 31; H=272–515: 32 words |

The next H above each maximum would require 33 words. Minimum H is the
receive/dispatch/output work that must fit in the idle half-period. WAIT
segments longer than 256 cycles consume another instruction word.

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

No register-register OR/add is available in the legacy ISA. Appending a one uses `ADDI R0,1`
after SHL clears bit zero; appending zero uses JMP. Both paths take the same
time. Ordinary bits take a four-cycle pad that the longer byte-dispatch path
skips, preserving uniform SCLK timing. This is control-flow balancing, not
an unrolled waveform or payload-specific instruction sequence.

The complete default Mode-0 allocation is 31 words: 4 for initial idle/direction
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
| SCLK | `uio[1]` | Output, idle at CPOL |
| CS | `uio[2]` | Output, active LOW |
| Unused | `uio[6:3]` | Inputs |

The direction mask is `0x86`. MOSI is allocated to bit 7 so an ANDI selects
the current MSB without a seven-instruction shift sequence. This is a firmware
pin allocation, not a restriction of the hardware. Other mappings/encoded
shift directions are future generator experiments; this API does not promise
arbitrary pin assignment. The generator never drives MISO.

The edge definitions follow [Microchip SPI transfer modes](https://onlinedocs.microchip.com/oxy/GUID-A299F4E7-F38C-4DF5-96C0-A87B9F519156-en-US-4/GUID-8A5B8750-B99E-4176-834E-E44E98F4A098.html).
In every mode the single-issue engine produces the sample SCLK edge with OUT
and executes READ on the **following system edge: 20 ns later at 50 MHz**.
It cannot drive SCLK and read MISO in one instruction. The slave must present
MISO before the sample edge and hold it through this additional system edge,
including the final bit. A conventional slave holds until the next setup edge;
the tests also exercise a slave that invalidates data immediately after the
one-cycle hold. This is not a claim of edge-coincident sampling.
The engine has no input synchronizer; device propagation, setup/hold, and
board delays must satisfy the M2 synchronous input contract.

For CPHA=0 (Modes 0/2), MOSI can change during the idle-clock phase, including
at the trailing edge, and remains stable across the leading sample edge and
throughout the active phase. Minimum MOSI setup is two system cycles. MISO's
first bit is prepared after CS assertion, and later bits after trailing edges.
For CPHA=1 (Modes 1/3), MOSI changes only on leading edges while selected,
giving a full H-cycle setup before each trailing sample edge. The slave launches
MISO on leading edges; both wires retain the last bit through its sample edge.

In all modes, CS changes only with SCLK at CPOL and its first leading edge is
H cycles after assertion. There are exactly 16 edges per byte, uniform across
the byte boundary. CS deasserts six cycles after the last trailing edge for
CPHA=0 and ten cycles after it for CPHA=1, after completing RX bookkeeping.
CS-low duration is `16*n*H + (10 if CPHA else 6)` cycles for n bytes.
The active direction persists in the final idle loop, with CS HIGH, SCLK at
CPOL and MOSI LOW. Reset or leaving RUN releases all
outputs; use an external CS pull-up if deselection is required while released.

## Logic, configuration and payload separation

`scripts/pio_firmware.py` is the small shared instruction builder used by
both UART and SPI. It owns only instruction encoding/operand checks,
symbolic labels, branch resolution, exact-cycle WAIT splitting and the
32-word limit. It contains no protocol names, pin mappings, payload policy,
or protocol timing. M3's UART image remains byte-for-byte unchanged.

`generate_spi_master(payload, *, half_period_cycles=16, mode=0)` in
`scripts/spi_firmware.py` owns the SPI state machine and its fixed pin mapping.
`mode` and `half_period_cycles` are configuration. `payload` is separate data; changing
values in a two-byte payload changes only the two MOVI immediates, not the
loop, instruction topology or timing. No payload bit causes generator-side
waveform unrolling. The checked-in example sends `0xA5, 0x3C`.

Runtime host loading was evaluated for the original legacy firmware. The host can write
only instruction bytes while RUN is zero; every stopped edge clears all
registers. This legacy program does not use extended data RAM, and has no
runtime host register-write port or FIFO.
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
python3 scripts/spi_firmware.py 0xA5 0x3C --output /tmp/spi-mode0.hex
python3 scripts/spi_firmware.py 0x37 146 --mode 1 --half-period 32 --output /tmp/spi-mode1.hex
python3 scripts/spi_firmware.py 0xA5 0x3C --mode 2 --half-period 14
python3 scripts/spi_firmware.py 0x81 --mode 3 --half-period 515
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

## Mode-0 instruction and cycle accounting (unchanged)

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

## Modes 1–3 instruction and cycle accounting

Mode 2 uses the Mode-0 loop with an extra `ADDI R1,2` before its idle-phase
MOSI OUT. The leading edge subtracts two from R1, and the trailing SET restores
SCLK HIGH. Thus idle-phase work is 14 cycles before padding rather than 13;
the active-phase budget remains six cycles plus `delay(H-6)`.

For Modes 1/3, the leading OUT launches both MOSI and active SCLK. R1 retains
that output pattern until the trailing OUT; it is then available for READ.
SHL runs during the active phase, before the receive bit is inserted:

- Active phase: SHL + delay(H-3) + ADDI clock transition + OUT = H cycles.
- Idle phase: READ + ANDI + JNZ + (JMP or ADDI) = four receive cycles;
  four count/boundary cycles; four balancing cycles; MOV + ANDI + OUT,
  plus one ADDI for Mode 1. This totals 16 cycles for Mode 1, 15 for Mode 3,
  with `delay(H-minimum)` supplying the remainder.
- At a byte boundary, the four-cycle balance WAIT is replaced by the
  second-byte JNZ + MOV R3,R0 + MOVI R0,tx1 + JMP. No edge or gap is added.
- Initial delay(8) replaces the receive/count work before the first bit.
- Final receive(4) + count(4) + completion JNZ + SET consumes ten cycles
  after the final trailing edge, with no extra SCLK pulse.

Mode 3 needs one fewer word because ANDI already produces its active LOW clock
pattern. This leaves room for longer WAIT padding than Mode 1.

## Mode-0 measured resource and timing results

Icarus/cocotb pin observations at a 20 ns clock; frequencies are computed
from observed periods. Each setting below exercised four full-duplex
two-byte transactions, including unrelated TX/RX data and both branch paths.
The current test also exercises one single-byte transaction at each H and emits
`test/output/spi-mode0-measurements.json`, retained by CI. Modes 1–3 emit
corresponding files with their own tested boundaries.

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

`test/spi_reference.py` implements `SpiSlave`, a mode-aware independent slave
and monitor. It consumes only observed GPIO, OE, cycle numbers and the requested
mode/timing. It imports no generator, opcode, firmware PC or register model.
It launches MISO on the mode's setup edges and decodes MOSI on sample edges;
CPHA=0 preloads the first reply on CS assertion. `Mode0Slave` remains a
compatible wrapper for the existing runtime-loader test. RX is checked through
the public register mux against replies unrelated to TX, with noise on unused
inputs to test MISO masking.

Eight Cocotb tests run for each mode:

1. Fourteen directed/random full-duplex single-byte transactions.
2. Boundary/WAIT-transition timing sweeps, with one single-byte and four
   two-byte transactions at each H.
3. Example and successive transactions without reset (Mode 0 uses the
   unchanged checked-in image).
4. Reset/stop during a transaction, followed by reprogramming.
5. Pause retains outputs/state, followed by abort and a fresh valid frame.
6. Six corrupted firmware images must be rejected: wrong TX, wrong initial
   WAIT, cleared MISO mask, missing READ, premature CS and bypassed RX branch.
7. Minimum/maximum H with one/two bytes: MISO settles one cycle before the
   sample edge and is valid only through the documented one-cycle post-sample
   hold. A seventh mutation, capturing MISO prematurely without changing wire
   timing, must fail specifically on RX data.
8. Each of the other three modes' firmware must be rejected by this mode's
   checker, proving CPOL/CPHA mismatches cannot silently pass.

All valid transfers check both RX registers, exact edge counts and widths,
MOSI setup/hold, uninterrupted CS across bytes, final CS hold, OE=0x86,
idle pin retention and absence of engine faults. Pause/resume wire timing is
not claimed: pausing stretches the frame, and the pause test aborts it.

| Mode | H exercised in RTL | Passed / failed | Completed valid transactions |
|---|---|---|---|
| 0 | 13, 14, 16, 32, 262, 263, 269 | 8 / 0 | 59 |
| 1 | 16, 17, 32, 259, 260, 271, 272 | 8 / 0 | 59 |
| 2 | 14, 15, 16, 32, 261, 262 | 8 / 0 | 54 |
| 3 | 15, 16, 32, 259, 260, 271, 272, 514, 515 | 8 / 0 | 69 |

Each mode additionally rejects seven mutations and three wrong-mode images.
These are **32 passing SPI RTL tests**, not 32 merely defined tests. Results
were read from logs and checked XML with Icarus 12.0 / Cocotb 2.0.1 on
2026-10-12. The separate per-mode runs list 24 filtered-out tests for the other
modes as skipped; none of the eight selected tests was skipped. Compatibility
regressions passed **22 core/loader**, **5 UART firmware**, **2 legacy RTL** and
**39 Python tooling tests**. No physical-design flow was run.

Python tests validate every integer H in each mode's range and reject adjacent
out-of-range values. They cover all 256 payload values for loop independence.
A frozen SHA-256 from the pre-change Mode-0 generator verifies exact output
for all 257 H settings with one/two bytes and all 256 payload values at H=16.
This is generator coverage, not an exhaustive RTL sweep of every payload/H.

Reproduction with installed Icarus, make, and Python containing
`test/requirements.txt` (Cocotb 2.0.1 and pytest 8.4.2):

```bash
# WSL working directory; activate an existing environment or install the
# requirements into a virtual environment first.
cd /mnt/d/CS4444/FoRG-protocol-emulator
export PATH=/tmp/forg-regression/venv/bin:/usr/bin:/bin
bash scripts/test-spi-modes.sh mode0
bash scripts/test-spi-modes.sh mode1
bash scripts/test-spi-modes.sh mode2
bash scripts/test-spi-modes.sh mode3
bash scripts/test-spi-modes.sh compat
```

Each invocation snapshots the current working files into a new
`/tmp/forg-spi-modes.XXXXXX` directory and prints its path. No original results,
sources, or frozen `wire300-v2` artifacts are overwritten. The directory retains
source hashes, base commit, named logs/XML, FSTs, and `test/output/` measurement
JSON. Mode runs take about 5–15 seconds each locally; compatibility about
15 seconds. Success requires exit zero and eight selected passes per mode,
plus the compatibility counts above with no failures/errors. XML can be
rechecked with `python scripts/check-test-results.py /tmp/forg-spi-modes.XXXXXX/mode0.xml`.
The optional `all` selection runs these same focused checks sequentially.

CI's existing `make -B TARGET=spi_firmware` runs all 32 SPI tests without a
mode filter, retaining `results-spi-firmware.xml`, `spi_firmware.fst` and the
measurement JSON. Direct make commands use `test/results.xml` and `test/tb.fst`;
use the isolated script to preserve existing outputs. Gate/board-level SPI
validation is not part of these RTL results.

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

Not provided: arbitrary pin remapping, indefinite streaming, more
than two retained RX bytes, or autonomous repeated transactions without host
loading. None is silently emulated with precomputed waveform tables.

With all four SPI modes independently verified at RTL, the next development
target is **I2C 400 kHz**.
