# I2C firmware: address-write probe on M2

This document describes the retained **legacy probe** and its original M2
constraints. For multi-byte write/read and repeated-START transactions on the
extended core, use [the complete I2C master](i2c-master.md). The probe remains
unchanged as a compatibility regression.

This experiment implements a **complete address-only write probe**, not a complete
I2C data-transfer controller. It uses the unchanged programmable M2 engine and
shared `scripts/pio_firmware.py` builder. No RTL, ISA, or dedicated protocol
hardware was added. The 32-word image occupies all instruction memory.

## Supported scope and prerequisites

Implemented: START, configurable seven-bit address followed by W=0, MSB-first
transmission, a released ninth ACK slot, actual ACK/NACK sampling, and STOP.
Every SCL release, including STOP preparation, polls the resolved input and waits
indefinitely if another participant holds SCL LOW. Both ACK and NACK finish with
STOP. R3 retains 0 for ACK or 1 for NACK after a fault-free HALT.

**Not implemented:** data-byte writes, reads, master ACK/NACK after reads,
repeated START, multi-byte transactions, runtime payload loading, arbitration,
bus-clear pulses, timeout, or autonomous bus acquisition. There is no `read` or
`payload` API that silently substitutes a probe. Address+R=1 is deliberately not
offered: an acknowledging target could immediately drive the first read bit LOW,
preventing this program from generating a valid STOP without completing a read.
All 128 address encodings can be generated, but reserved codes retain their
special I2C meanings; use ordinary device addresses (0x08..0x77) for probing.
A target need not assign useful application semantics to a zero-data write.

Mandatory host/bus contract:

1. Use a single-controller bus, external pull-ups, and a cooperating I2C target.
2. Load the program while stopped. **Isolate the programming host from the I2C
   bus during loading.** M2 receives instruction bytes through the same `uio_in`
   pins used by the bus. Driving arbitrary host bytes onto a connected bus would
   generate unwanted edges and potentially drive HIGH against a target. The
   testbench explicitly models an isolated programming path; this mux/isolation
   does not exist inside the M2 RTL. A physical integration must provide it, or
   preload before connecting the bus. Repeated loading is not streaming I2C.
3. After reconnecting/releasing the bus, observe **both physical lines HIGH
   continuously for at least 4.7 us** before setting RUN. Restart that interval
   whenever either line is LOW. The firmware does not perform this qualification.
   The test host enforces and tests this precondition, including stuck lines.
4. Read HALT/fault status and R3 while RUN remains set. Clearing RUN or loading
   another image clears registers. HALT releases both pins; allow SDA to rise
   and qualify bus-free time again before another probe.

A target may stretch any clock's LOW phase. An indefinitely stuck SCL leaves the
engine polling, with no false completion. A stuck SDA can prevent STOP; HALT
alone does not prove that an abnormal physical bus reached idle. The host must
observe the bus. Reset or RUN=0 releases outputs but is an abort, not a guaranteed
legal STOP or a recovery sequence. Pausing `ena` retains direction/data and
extends elapsed timing; this is compatible with this untimed-target test model,
not a promise about target-specific watchdogs or SMBus timeouts.

## Pins, electrical behavior, and program

| Resource | Use |
| --- | --- |
| `uio[0]` | SDA, bidirectional open drain |
| `uio[1]` | SCL, open drain with resolved-input stretch polling |
| `uio[7:2]` | Inputs; unrelated input noise is masked out |
| R0 | Inverted address/W shift register |
| R1 | Bit extraction and SCL polling scratch |
| R2 | Remaining phases: 9 address/ACK clocks, then 0 for STOP |
| R3 | Previously sampled SDA; final ACK/NACK result |

Stopped mode initializes the output latch and registers to zero. The program
never executes SET or OUT: DIR=0/1/2/3 releases or pulls lines LOW, never drives
HIGH. R0 starts as `~(address << 1) & 0xff`. A set MSB means drive SDA LOW. After
eight shifts R0 is zero, so the same bit loop naturally releases the ninth ACK
slot. No expected ACK, per-bit waveform table, or address-specific control flow
is compiled into the image.

Each falling-edge path preserves the previously observed SDA level while
asserting SCL LOW. A 16-cycle WAIT separates this from changes to the next bit,
ACK ownership, or STOP preparation. This matters particularly after NACK:
asserting both LOW simultaneously could accidentally create START while physical
SCL was still falling. When taking over an observed ACK=0, both participants may
briefly pull SDA LOW during SCL fall; the bus value remains unchanged.

SCL polling takes READ, ANDI, ADDI, JNZ. Only after an observed HIGH does the
high-phase delay begin. The same polling path handles STOP, then HALT releases
SDA. HALT therefore supplies both the STOP transition and a retained result,
saving the separate release instruction and idle loop needed by driven-idle UART.

| Instruction group | Words |
| --- | ---: |
| Address/count initialization, START and hold | 4 |
| Preserve SDA on falling SCL, fall guard | 5 |
| Phase decrement and dispatch | 2 |
| Drive-zero path / STOP preparation | 4 |
| Bit extraction and branch | 3 |
| Release-one / ACK path | 3 |
| Resolved SCL polling | 4 |
| HIGH delay, dispatch and HALT/STOP | 3 |
| SDA capture, mask, shift and loop | 4 |
| **Total** | **32 / 32 (100%)** |

## Timing and measurements

At 50 MHz a cycle is 20 ns. `half_period_cycles=P` accepts integers 250..267;
P is a budget, not an exact duty-cycle promise. WAIT consumes one issue cycle
plus its immediate; the shared builder receives the total desired delay.
The four delay slots cost 215 (START), 16 (fall guard), P-20 (each LOW path),
and P-11 (HIGH). P=268 would need two words for the HIGH delay, exceeding capacity.
Values below 250 are rejected to preserve the supported Standard-mode envelope.

Measured with ideal digital edges, without stretching or pause:

| Interval | System cycles |
| --- | --- |
| START to first SCL assertion | 217 |
| Each address/ACK LOW | P+4 minus previous SDA (0/1); initial previous SDA=0 |
| Address-bit HIGH | P+1 minus transmitted bit (0/1) |
| Ninth ACK HIGH | P, for either ACK or NACK |
| Address-to-address/ACK rising-edge period | 2P+5 for previous bit 0; 2P+3 for bit 1 |
| STOP preparation LOW | P+1 minus sampled ACK bit |
| Actual STOP-setup HIGH, ideal edges | P-4 |

Default data clock periods are **503/505 cycles**, giving **99,403.58/99,009.90 Hz**,
with LOW=253/254 and HIGH=250/251 cycles. P=267 gives periods 537/539 cycles,
or 93,109.87/92,764.38 Hz. The ninth rising edge to the extra SCL release used
for STOP is 500/501 cycles at default timing; it is not a tenth transmitted bit.
Actual frequency depends on address bits, bus delays, stretching, and pauses.
Timing is explicitly counted rather than labeled as an exact 100 kHz square wave.

The timing checker applies Standard-mode minima: LOW 4.7 us, HIGH and START
hold/STOP setup 4.0 us, data setup 250 ns, and clock rate at most 100 kHz.
The falling-edge guard ensures SDA does not change before SCL falls. These
requirements follow [NXP UM10204 revision 7, Table 11 and footnote 2](https://www.nxp.com/docs/en/user-guide/UM10204.pdf).
The worst tested 300 ns SCL fall leaves STOP LOW at least 235 cycles; the worst
300 ns SDA fall leaves START hold at least 202 cycles. Delayed rises extend LOW;
SCL polling keeps the subsequent HIGH delay intact (polling adds quantization).

The Verilog harness resolves separate master/target drivers with `tri1` pull-ups.
Optional independent rise/fall delays apply to the resolved signals fed back to
M2 and the target model. Tests include ideal edges, a 300 ns SCL-only fall, and
1000 ns rises with a 300 ns SDA-only fall. These are digital threshold-delay
experiments, **not analog RC, pad, metastability, gate-level, or board signoff**.
Physical integration must check voltage, pull-ups, capacitance and pad edges.

## Capacity evidence and general-purpose changes to evaluate

This is the largest complete reusable transaction developed here, not a proof
that no more compact M2 program can exist. A straightforward extension to one
data byte illustrates the capacity bottleneck. Set the initial count to 19,
then replace the final JMP with the following dispatch after SDA capture/shift:

```text
MOV  R1, R2
ADDI R1, 246           ; first ACK has remaining count 10
JNZ  R1, fall
MOVI R0, inverted_payload
JMP  fall
```

Five words replace one: **36 words even before handling an address NACK**.
Adding `JNZ R3, abort`, with `abort: MOVI R2,1; JMP fall`, makes this construction
39 words and routes a NACK into STOP. It would also require timing re-budgeting.
Initial autonomous bus qualification needs additional checking/delay logic.
A receive path needs shifting sampled bits into a retained result, master
ACK/NACK direction selection and additional phase dispatch. Four registers are
already assigned, though phase-specific reuse is possible. These counts show
why extending this particular correct loop does not fit; they are not universal
lower bounds or evidence that the GPIO/ISA cannot express I2C at larger capacity.

Proposals below are estimates from structural resources, **not synthesized area
or timing results**. None has been implemented or approved as an ISA change.

| General-purpose option | Estimated resource / timing cost | Cross-protocol benefit |
| --- | --- | --- |
| 64-word instruction store | +512 instruction bits, +64 valid bits, +1 PC bit; an extra selection level and wider decode. Loading needs a sixth address bit or bank mechanism too. | Space for I2C phases; more UART/SPI payload dispatch without waveform unrolling. |
| Register-sourced DIR / masked GPIO update | A DIR source selection can cost about eight 2:1 muxes plus decode if register read logic is shared; masked updates need per-bit selection. No new direction state is necessary. | Remove duplicated SDA branch paths; generic bidirectional buses, turnaround and I2C/SPI pin packing. |
| Extend WAIT immediate/counter from 8 to 12 bits | +4 counter flip-flops and wider decrement/zero logic; use currently unused instruction bits with an approved ISA definition. | Longer UART delays and slower SPI/I2C rates without extra words. |
| Generic masked input condition / bit branch | Mask/test and branch-control logic, potentially on the branch critical path; no required new state. | Collapse four-instruction SCL polling and reduce input handshakes in other protocols. |
| Small runtime FIFO | One 8x8 FIFO: 64 data bits plus roughly 10 pointer/count bits and mux/control logic; TX+RX roughly doubles this. A real host interface is also needed. | Data separate from code, RX retention, fewer program reloads and bus-isolation operations for all protocols. |
| Synchronization / input capture | Two stages on eight GPIO inputs add 16 flip-flops and approximately 2 cycles (40 ns) latency; event capture adds control/state. | Defined asynchronous input handling for SPI/I2C and future UART RX; all timing must include latency. |

Measure these alternatives against the same protocol workloads before choosing
an architectural revision. Memory expansion helps first but does not itself
solve runtime loading, asynchronous input handling, or programming-pin sharing.

| Existing workload | Words | Timing at 50 MHz | Runtime data/result limitation |
| --- | ---: | --- | --- |
| UART TX, one/two bytes | 27 | 434 cycles/bit, 115,207.37 baud | Embedded MOVI payload; driven idle loop |
| SPI Mode 0, one/two full-duplex bytes | 31 default (30..32) | 32 cycles/bit default, 1.5625 MHz; 26-cycle minimum | Embedded TX; final R0/R3 retain RX |
| I2C address-write probe | 32 | 503/505 cycles per address/ACK period default, about 99 kHz | Embedded address; R3 retains ACK; host qualifies idle and isolates loading |

## Verification and reproduction

Final local validation passed **26 Python unit tests and 48 RTL cases**, with
zero failures or skips: I2C 5 cases x 3 bus configurations, core 5, firmware UART
5, SPI 6, legacy 2, and fixed UART 3 x 5 divisors. Every result XML passed the
shared validator. Independent code review found no blocking issues. These are
local RTL results; physical implementation/signoff is not claimed.

Five Cocotb cases run on each of three digital bus configurations. They cover
all 128 address encodings, matching and nonmatching targets, ACK/NACK, image
reproduction, successive host-reprogrammed probes, four timing budgets, delayed
edges, stretching of every clock and STOP, unbounded stuck-clock waiting,
reset/RUN abort, enable pause, missing STOP from stuck SDA, and busy-bus host
rejection. No write/read,
repeated-START or multi-byte coverage is claimed for unsupported operations.
The edge-driven target knows only its address, response policy and observed
wires. It never reads firmware PCs, words, instruction delays or internal state.
Unused GPIO input noise verifies masking. Cycle-derived timing formulas are
checked separately in the ideal-bus tests.

Eight RTL mutations exercise wrong address data, short LOW/HIGH phases, ACK
ownership, ACK sampling, active HIGH drive, missing SCL polling, and premature
SDA release. The brief-release mutation is required on the ideal bus; slow
pull-ups may legitimately filter that pulse, so it is not required to fail in
the delayed configurations. All other mutations run in each configuration.
Measurements are emitted to `test/output/i2c-firmware-<rise>-<scl-fall>-<sda-fall>.json`.
CI validates XML immediately after every run and preserves named XML, waveforms
and JSON alongside all existing core, UART, SPI and legacy regressions.

```bash
python3 scripts/i2c_firmware.py 0x50 --output firmware/i2c_probe.hex
python3 scripts/i2c_firmware.py 0x27 --half-period 267 --output /tmp/probe.hex
python3 -m unittest discover -s scripts -p 'test_*.py' -v
bash scripts/test-local.sh TARGET=i2c_firmware
bash scripts/test-local.sh TARGET=i2c_firmware I2C_SCL_FALL_NS=300
bash scripts/test-local.sh TARGET=i2c_firmware I2C_RISE_NS=1000 I2C_SDA_FALL_NS=300
```

Full existing regression (stop on failure):

```bash
set -e
for target in core uart_firmware spi_firmware legacy; do
  bash scripts/test-local.sh TARGET="$target"
  python3 scripts/check-test-results.py test/results.xml
done
for divisor in 1 2 4 17 434; do
  bash scripts/test-local.sh TARGET=uart UART_CLKS_PER_BIT="$divisor"
  python3 scripts/check-test-results.py test/results.xml
done
```

With dependencies installed, `cd test && make -B TARGET=i2c_firmware` is the
same simulator target. Local runs overwrite `test/results.xml` and
`test/i2c_firmware.fst`; copy them before another run when retaining evidence.
