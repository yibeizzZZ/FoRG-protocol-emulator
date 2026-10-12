# Firmware UART RX

`scripts/uart_rx_firmware.py` generates a **56-word extended-mode** program
for finite 8N1 receive batches at a 50 MHz system clock. Default RX is `uio[1]`;
`rx_pin=0..7` / `--rx-pin` selects another existing input. Every GPIO remains
an input. No RTL, ISA, pad or physical configuration change is required for
the digital receive behavior. The original UART TX generator and image are
unchanged; this single engine executes TX or RX, not both simultaneously.

## Loading and RAM contract

Load `firmware/uart_rx.hex` or `generate_uart_rx()` through the existing UI-only
Program Loader with `extended=True, transactional=True`. After commit, write
the requested count to RAM[16], assert RUN, and keep `ena=1`. Start reception
from an idle HIGH line; allow at least 40 system clocks after RUN before the
first START. Do not attach a host GPIO driver to RX for programming.

```python
from scripts.uart_rx_firmware import generate_uart_rx, receive_data
from scripts.pio_host import program_writes, data_writes

for command in program_writes(generate_uart_rx(), extended=True, transactional=True):
    set_ui(command)  # Board-specific transport; each command spans an enabled edge.
for command in data_writes([16], offset=16):
    set_ui(command)
set_ui(0xA0)       # RUN + hardware status; wait for HALT or hardware fault.
# RAM readback while RUN stays set: ui_in = 0x80 | address.
```

`receive_data(count)` optionally constructs a complete 32-byte RAM image.
Writing only RAM[16] is sufficient, even after a fresh transactional load:
firmware initializes its own metadata and stores make received RAM bytes valid.
The same program handles every count from 1 through 16. Subsequent requests
require stopped-mode RAM writes, not program reloading.

| RAM | Meaning |
| --- | --- |
| 0..15 | Committed received bytes, first byte at 0 |
| 16 | Requested count, integer 1..16 |
| 17 | Status: 0 host-ready, 1 busy, 2 success, 3 framing error, 4 invalid count |
| 18 | Number of valid bytes committed in this capture |
| 19 | Assembled byte rejected for bad STOP; zero until an error |
| 20 | Bytes remaining, including a rejected byte; defined for valid captures only |
| 21..31 | Unused |

Host readback does not pause execution. Read final results after hardware HALT
and check hardware fault separately from RAM[17]. A bad STOP halts immediately
without committing that byte; earlier valid bytes and their count remain.
The index of the rejected byte is RAM[18]. Invalid count clears RAM[18]/[19]
and reports status 4, but RAM[20] can retain an earlier value. Buffer bytes beyond
RAM[18] are stale or invalid and must not be interpreted as new data.

## Timing and consecutive bytes

The receive bit interval is **434 enabled clocks = 8680 ns**, equivalent to
**115207.373 baud** (about +0.0064% relative to 115200). R0 assembles the byte,
R1 samples/masks the input, R2 indexes the buffer, and R3 counts eight bits.
SHR followed by conditional ADDI 128 assembles LSB-first bits. The one and zero
paths take the same number of clocks. No CALL stack is used.

Calculated instruction budgets from the READ that first observes START LOW:

| Event | Enabled clocks | Time at 50 MHz |
| --- | ---: | ---: |
| START confirmation | 217 | 4.340 us |
| Data bit i, i=0..7 | 651 + 434*i | 13.020 + 8.680*i us |
| STOP sample | 4123 | 82.460 us |
| First next-START poll | 4133 | 82.660 us |

START polling repeats every three clocks, adding up to 60 ns detection delay.
A candidate that is HIGH at midpoint confirmation is discarded as a false
START. Initial LOW is ignored until HIGH has been observed. This is not an
oversampling or majority-vote receiver, nor a guarantee of glitch rejection.

Zero extra gap beyond the sender's one STOP bit is supported within a batch.
Nominal frames start 86.800 us apart, leaving enough time for RAM stores and
rearming. On the final byte, success is published after the STOP midpoint and
HALT; the receiver does not wait for the entire final STOP bit to elapse.
The sender must still provide its full STOP bit.

Independent RTL probes use 20 ns HIGH pulses at candidate data centers and
infer captured samples from RAM, without inspecting PCs or instruction state.
Across six START phases these locate the first sample in windows spanning
**13.020..13.080 us** after START, with **8.680 us** spacing across all eight
bits. Separate STOP-edge probes verify acceptance/rejection around its center.

Verified mismatch cases include nominal and +/-2%, +/-4% sender baud at five
START phases. Boundary workloads additionally verify **16 consecutive bytes**
at **8280 ns/bit (120772.947 baud)** and **9160 ns/bit (109170.306 baud)**, each
at six phases. These are measured passing endpoints, not an exhaustive claim
over all jitter, payloads and edge alignments.

The outside-boundary tests intentionally receive framing errors:

- 8260 ns/bit: long zero-gap bursts accumulate latency because a 82.600 us
  frame is shorter than the 82.660 us detect-to-rearm path. A bit-center-only
  tolerance calculation would miss this limit.
- 8240 ns/bit: the STOP sample can enter the following START.
- 9180 ns/bit: the STOP sample can still be in data bit 7.

For practical use, budget combined sender/receiver clock error within the
verified +/-4% cases. This is not a guarantee for arbitrary intra-bit jitter.
Longer inter-byte gaps are allowed; waiting for a next START has no timeout.

## Errors, abort and physical limits

- No input timeout, FIFO, interrupt, continuous ring buffer, parity, flow control,
  autobaud or automatic framing-error recovery. Traffic beyond the configured
  batch is ignored after HALT; there is no overrun detector.
- A break after arming yields one framing error and HALT. Initial LOW waits
  indefinitely for HIGH. Starting in the middle of a frame cannot guarantee
  alignment. After an error, stop/reconfigure and rearm on a known idle line.
- RUN=0 aborts and preserves committed RAM. Reset invalidates code and RAM.
  Reprogram after reset. ENA pause during a frame shifts samples and is not
  supported for reception; it can corrupt data without a framing error.
- The existing core READ instruction samples raw pins and its ISA specifies
  synchronous inputs. **Firmware cannot provide an asynchronous-input
  synchronizer or prove metastability safety.** Physical UART integration needs
  suitable external input synchronization or a separately reviewed CDC solution.
  Asynchronous simulation phase sweeps do not prove silicon CDC reliability.
- No hardware, gate-level, analog, noise-immunity or physical-design verification
  is claimed. The digital firmware implementation needs no RTL/ISA change;
  physical asynchronous-input reliability remains an integration requirement.

## Reproduce focused RTL verification

Dependencies: Python with `test/requirements.txt`, Icarus Verilog and GNU make.
Every runner invocation snapshots current working sources to a fresh `/tmp`
directory and retains XML, log, source hashes, FST and measurement JSON.
It neither invokes physical tools nor writes old verification directories.

```bash
export PATH=/tmp/forg-regression/venv/bin:/usr/bin:/bin
cd /mnt/d/CS4444/FoRG-protocol-emulator
python -B scripts/uart_rx_firmware.py --output /tmp/uart_rx.hex --count 16 \
  --data-output /tmp/uart_rx_data.json
python -B -m unittest discover -s scripts -p 'test_*.py' -v
# Separate focused groups; the exhaustive all-byte test is the largest.
bash scripts/test-uart-rx.sh uart_rx payloads
bash scripts/test-uart-rx.sh uart_rx timing
bash scripts/test-uart-rx.sh uart_rx control
bash scripts/test-uart-rx.sh uart_firmware
bash scripts/test-uart-rx.sh core
bash scripts/test-spi-modes.sh mode0
bash scripts/test-spi-modes.sh mode1
bash scripts/test-spi-modes.sh mode2
bash scripts/test-spi-modes.sh mode3
bash scripts/test-i2c-modes.sh standard 0 0 0 0 smoke
bash scripts/test-i2c-modes.sh standard 0 0 0 0 ram
bash scripts/test-i2c-modes.sh standard 0 0 0 0 control
bash scripts/test-i2c-modes.sh fast 0 0 0 0 base
bash scripts/test-i2c-modes.sh fast 0 0 0 0 extended
```

RX tests cover all 256 bytes, all 1..16 counts, zero-gap and longer-gap frames,
RAM-only and count-only requests, independent baud/phase variation, midpoint
probes, STOP failures, break/idle, short false START, invalid counts, host
abort/reset and pin selection. Four actual firmware mutations (shift, delay,
RAM store, STOP validation) must fail the independent receive scoreboard.
The existing UART TX waveform checker and all protocol/core tests are retained.
The named RX groups execute 1/3/7 tests and check those counts in XML. The
default RX selection is `control`; run all three groups for full RX coverage.

## Recorded verification

The current verification bundle is `build/uart-rx-0ndzwht1/`. Its
`summary.json` maps the following final passes to original XML, simulator logs,
waveforms, source snapshots/hashes and UART timing measurement JSON:

| Suite | Passed | Failures |
| --- | ---: | ---: |
| UART RX | 11 | 0 |
| Existing UART TX | 5 | 0 |
| SPI modes 0 / 1 / 2 / 3 | 8 / 8 / 8 / 8 | 0 |
| I2C Standard | 8 | 0 |
| I2C Fast | 12 | 0 |
| Core / Program Loader | 16 / 6 | 0 |
| Python | 46 | 0 |

These are 90 executed RTL tests plus 46 Python tests; filtered skips are
excluded. I2C compatibility runs use zero propagation-delay wires, with the
existing functional, stretch, pause, error and mutation tests. Earlier I2C
delay-matrix verification artifacts remain untouched. Original TX/SPI/I2C
generators, images, tests, and synthesizable RTL are byte-identical to the
starting branch. The only edits to pre-existing tracked files are this
feature's README link and Makefile target.

An exploratory boundary test initially assumed 8260 ns/bit sustained bursts
would pass. Its failure exposed the 10-cycle STOP-to-rearm overhead; the final
boundary test records this as an expected outside-range framing error. The
firmware did not change to conceal or relax that limit. Read-only code review
found no material defect; invalid-count remaining metadata and physical CDC
limitations are documented above.
