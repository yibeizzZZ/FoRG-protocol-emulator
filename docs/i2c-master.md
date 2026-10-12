# Complete I2C transactions on the general-purpose PIO

The same firmware image performs 7-bit-addressed multi-byte writes, reads, and
write-then-read transactions with repeated START. It executes on the extended
PIO core; there is no I2C controller in RTL. The original UART, SPI, and 32-word
I2C probe firmware continue to execute in default legacy mode.

## Supported transactions

- Write: START, address+W, 1..15 arbitrary data bytes, STOP.
- Read: START, address+R, 1..15 received bytes, STOP. The controller ACKs every
  received byte except the final byte, which receives NACK.
- Combined: START, address+W, 1..15 write bytes, **repeated START without STOP**,
  address+R, 1..15 read bytes with final NACK, STOP. A one-byte register index
  followed by a multi-byte read uses this path.
- Target address/data ACKs are sampled from resolved SDA. NACK aborts further
  payload transmission and produces STOP, with a distinct completion code.
- Every SCL release polls the physical input, including ACK, repeated START,
  and STOP. A configurable timeout releases the bus and reports an error.

Address and payload are runtime **data**, never precomputed pin waveforms or
per-byte program copies. TX and RX reuse the same bit loops and call shared
clock/edge routines. All seven-bit address values can be encoded; reserved I2C
addresses retain their special meanings, which this firmware does not implement.
Normal device transfers should use ordinary seven-bit addresses.

## Host integration

Load `firmware/i2c_master.hex` once, then replace only RAM configuration/payload
between transactions. `scripts/pio_host.py` uses dedicated UI pins for program
and data packets. SDA/SCL stay physically connected and released throughout
loading; the full-master testbench has **no programming mux on the bus inputs**.
This resolves the earlier probe's loading-isolation limitation. The old byte
loader remains available for legacy integrations.

Example, with commands applied by a synchronous host transport:

```python
from scripts.i2c_master import generate_i2c_master, transaction_data
from scripts.pio_host import program_writes, data_writes

# set_ui samples a control byte on at least one rising clock, with ena=1.
for control in program_writes(generate_i2c_master(), extended=True):
    set_ui(control)
for control in data_writes(transaction_data(0x50, write=[0x10], read_count=4)):
    set_ui(control)
set_ui(0xA0)                    # RUN + hardware status readback
# Keep RUN set; wait until uo_out bit6(HALT) or bit7(fault) is asserted.
# Read RAM19 with ui_in=0x93, and buffers with ui_in=0x80|address.
# RAM is retained across stopped mode, so repeat only data_writes for next job.
```

`set_ui` is the board/transport-specific operation, not an included board driver.
No GPIO input pin should be driven by the host for these packets. Full packet
format, strobe semantics, mode selection, readback and fault behavior are in
[the ISA/host contract](isa.md). Reset invalidates code and RAM; stopping clears
working registers/stack but preserves program and data. Hardware fault status
must be checked separately from firmware completion status.

| RAM address | Meaning |
| --- | --- |
| 1..15 | Shared TX/RX buffer, ordered from high index down to 1 |
| 16 | Address shifted left by one; bit0 must be zero |
| 17 | Write length, 0..15 |
| 18 | Read length, 0..15; at least one length must be nonzero |
| 19 | Firmware status |
| 20 | Unacknowledged/untransferred TX bytes remaining |
| 21 | RX bytes not yet completed and stored |
| 22 | Saved bit counter used by clock-wait subroutine |
| Others | Reserved for future firmware use |

For N TX bytes, first byte is RAM[N], last RAM[1]. For M RX bytes, read back
RAM[M], RAM[M-1], ..., RAM[1]. Combined transfers finish consuming TX before RX
reuses the buffer. This layout lets one register act as pointer and remaining
count, avoiding protocol-specific addressing hardware. `transaction_data`
returns a complete initialized 32-byte image. Raw invalid lengths/odd address
bytes are rejected by firmware; missing RAM initialization instead faults in
hardware. The RAM size does not support arbitrary-length streaming.

| Status | Meaning |
| --- | --- |
| 0 | Host-prepared, not started |
| 1 | Busy, including STOP construction |
| 2 | Completed successfully with physical SDA released for STOP |
| 3 | Write address NACK |
| 4 | Write data NACK |
| 5 | Read address NACK |
| 6 | SCL was not observed HIGH within the polling budget |
| 7 | Bus not idle before START |
| 8 | Invalid configuration |
| 9 | SDA remained LOW after STOP was attempted |

The host waits for hardware HALT before interpreting completion/RAM. NACK codes
are published after STOP; clock timeout supersedes an earlier NACK if STOP
cannot raise SCL. SDA stuck LOW during STOP reports 9. A NACKed write byte is
included in the remaining count; RX remaining decreases only after a whole byte
is stored. Errors do not imply that no target-side write occurred: already
acknowledged bytes can have changed peripheral state. RAM19 remains busy until
STOP is checked; HALT releases all pins and retains results.

## Electrical and Standard-mode timing behavior

SDA is `uio[0]`; SCL is `uio[1]`. Output data is initialized LOW by stopped mode
and never changed by this firmware. Generic OESET/OECLR instructions pull LOW
or release individual lines. External pull-ups supply HIGH. Before changing
SDA, firmware asserts SCL LOW and waits 16 enabled cycles plus call overhead,
covering the tested 300 ns falling delay. SDA is sampled during confirmed SCL
HIGH; it stays stable while SCL is HIGH except for START/repeated START/STOP.

Initial idle is checked, delayed for 300 cycles, and checked again before START.
This is a **single-controller** implementation, not continuous bus arbitration.
A conforming target does not initiate a new transaction during bus-free time.
The repeated START path releases SDA during SCL LOW, raises and observes SCL,
waits the configured HIGH delay, then pulls SDA LOW. ACK ownership changes
also happen during SCL LOW after the falling-edge guard.

At 50 MHz, `half_period_cycles=250` requests minimum software delays of 5 us
in each phase. Instruction/call overhead makes this approximately **94 kHz**
on ideal wires, rather than an exact 100 kHz waveform. Budgets 250..4096 are
accepted; changing timing changes WAIT constants, not address/payload logic.
All delay issue cycles are counted by the shared assembler. The clock routine
samples SCL again after each unsuccessful poll plus a 256-cycle WAIT. With
`stretch_polls=255` the enabled-time timeout is approximately 1.326 ms;
1..255 polls can be selected when generating the image. This is a sampled
timeout: a rise after the final unsuccessful sample does not cancel the error,
which is reported after that sample's delay. A slow pull-up can add one polling
interval even without a target stretch. Pause stops the timeout
counter as well as instruction execution.

Measured digital timing (minimum observed phases across the verification workload):

| Budget | Bus delays (rise / SCL fall / SDA fall) | LOW minimum | HIGH minimum | Maximum clock frequency |
| --- | --- | ---: | ---: | ---: |
| 250 | 0 / 0 / 0 ns | 270 cycles | 259 cycles | 94.34 kHz |
| 250 | 0 / 300 / 0 ns | 255 cycles | 274 cycles | 94.34 kHz |
| 250 | 1000 / 0 / 300 ns | 320 cycles | 469 cycles | 63.29 kHz |
| 267 | 0 / 0 / 0 ns | 287 cycles | 276 cycles | 88.65 kHz |

The independent checker enforces Standard-mode setup/hold, START/STOP timing,
clock rate and low/high durations, using [NXP UM10204 Table 11](https://www.nxp.com/docs/en/user-guide/UM10204.pdf).
Digital propagation-delay sweeps are not analog pad/RC or metastability signoff.
The core retains its synchronous-input contract; host signals must meet input
setup/hold, and physical integration must satisfy the GPIO input assumptions
and electrical limits. No fabricated-chip or board validation is claimed.

Reset or RUN=0 aborts by releasing pins; it cannot guarantee a legal STOP during
a stuck clock. Timeout also releases pins and reports an error rather than
pretending to recover the target. Arbitration, multi-controller operation,
10-bit addressing, bus-clear sequences, arbitrary message chains, unlimited
streaming, interrupts, target/slave mode and SMBus-specific watchdog semantics
are not implemented.

## Fast-mode firmware profile (50 MHz)

`generate_i2c_master(mode='fast')` or `scripts/i2c_master.py --mode fast`
selects a separate **127-word** image. The default remains `mode='standard'`;
the checked-in Standard image and its existing 250..4096-cycle delay range are
unchanged. A frozen-output unit test covers every Standard delay at polling
budgets 1 and 255. Fast mode rejects `--half-period`: its fixed timing profile
must be changed as a whole, including instruction overhead.

Both images use the same pins, extended ISA, RAM layout, 1..15-byte write/read
and combined transactions, status codes, and UI-only loading. No synthesizable
RTL or physical configuration changes are needed. Select the image once, then
change requests through RAM only. There is no firmware image reload per byte.

| Fast software delay | Enabled 50 MHz cycles |
| --- | ---: |
| SCL LOW / HIGH WAIT | 60 / 25 |
| START hold WAIT | 45 |
| Falling-edge guard WAIT | 16 |
| Initial bus-free WAIT | 65 |
| Delay before first SCL poll | 13 (READ at release +16 cycles) |
| STOP SDA-rise WAIT | 16 (READ at release +17 cycles) |
| Failed SCL poll WAIT | 256 (samples 260 cycles apart) |

One cycle is 20 ns. WAIT counts include the instruction issue cycle; other
instructions, including CALL, RET and branches, take one enabled cycle.
START tail-calls the falling-edge routine. TX calls that routine after each
shift and before the loop branch, so the first bit after an ACK no longer
executes a second falling-edge guard. This saves one word, paying for the new
first-poll delay: 127/128 words and maximum call depth two remain unchanged.

Calculated ordinary-bit periods are 128 cycles for RX, 132/133 for TX one/zero,
130 for target ACK, and 131/130 for controller ACK/NACK. These are instruction
budget estimates, not frequency measurements; byte bookkeeping extends some
periods. Delaying the first poll permits a 300 ns digital SCL rise without
mistaking it for stretching. With `stretch_polls=N`, measured timeout from the
first SCL release to HALT is `16 + (N-1)*260 + 263` enabled cycles: 279, 539,
and 66319 cycles at N=1,2,255 (the last is 1.32638 ms). Retries and final failed
sample semantics are preserved; Fast's first sample is 13 cycles later than
Standard's. ENA pause suspends execution and timeout progress.

The independent checker consumes only resolved SDA/SCL edges, with nanosecond
timestamps. It enforces Fast-mode LOW >=1300 ns, HIGH >=600 ns, START hold and
repeated-START/STOP setup >=600 ns, SDA setup >=100 ns, nonnegative hold,
data/ACK validity <=900 ns, bus free >=1300 ns and SCL period >=2500 ns.
The functional reference separately checks data, ACK ownership, repeated START,
byte counts, and STOP. Provisional clocks preceding START/STOP are recognized
as control phases rather than ordinary data bits. Negative trace tests prove
the validity check detects late ACK and the first bit after a byte boundary.

Digital delays are independently configurable for SDA/SCL rise and fall. This
model tests threshold propagation, not analog rise/fall slopes, pull-up sizing,
pad thresholds, spike filtering, or metastability. **No claim of exact 400 kHz
or full electrical compliance is made.** The existing single-controller and
bounded-timeout limitations continue to apply. The fixed Fast timing profile
assumes a 50 MHz clock and no more than 300 ns delay on any modeled edge.

Measured RTL results for the recorded unstretched combined/byte-boundary
workloads (Icarus 12, Cocotb 2.0.1; delays in ns):

| SCL rise / fall / SDA rise / fall | LOW min | HIGH min | Period min..max | Measured frequency min..max |
| --- | ---: | ---: | ---: | ---: |
| 0 / 0 / 0 / 0 | 1600 ns | 940 ns | 2560..2820 ns | 354.610..390.625 kHz |
| 0 / 300 / 300 / 300 | 1300 ns | 1240 ns | 2560..2820 ns | 354.610..390.625 kHz |
| 300 / 0 / 300 / 300 | 1900 ns | 640 ns | 2560..2820 ns | 354.610..390.625 kHz |
| 300 / 0 / 0 / 0 | 1900 ns | 640 ns | 2560..2820 ns | 354.610..390.625 kHz |
| 101 / 203 / 299 / 17 | 1498 ns | 1042 ns | 2560..2820 ns | 354.610..390.625 kHz |
| 300 / 300 / 300 / 300 | 1600 ns | 940 ns | 2560..2820 ns | 354.610..390.625 kHz |

These are resolved rising-edge periods within active segments, not transaction
throughput or a sum of independently observed minimum LOW/HIGH times. Across
those records, minimum START hold is 640 ns, repeated-START setup 640 ns, STOP
setup 620 ns, SDA setup 410 ns, and initial bus-free time 1910 ns. Maximum
observed data/ACK validity is 890 ns, including a deliberately slow target.
The 1300 ns LOW corner has no spare margin: faster system clocks or delays
outside the declared model are unsupported. Integer-picosecond arithmetic
ensures an exact boundary passes while one picosecond below it fails.

Stress records separately show 23360..23580 ns periods with stretching on every
clock (42.409..42.808 kHz), a maximum 4520 ns during ENA pause, and a maximum
41292 ns in the stretched-STOP workload (24.218 kHz for that interval). The STOP
measurement includes the test's 2 ns status-read clock gap. These stress cases
must not be presented as nominal Fast frequency. Polling safely extends LOW;
it can add almost one 260-cycle retry interval after a target releases SCL.

Twelve distinct Cocotb tests pass at each of the six Fast delay configurations:
72 test/configuration checks, including every length for pure writes, pure
reads and combined transactions, RAM-only reconfiguration, NACK/error paths,
stretch/pause/abort behavior and negative oracle/firmware mutations. The
measurement JSON and original XML/logs/source snapshots are retained under
`build/i2c-fast-mode-99ra3a7f/`; `summary.json` maps each check to its evidence.
Compatibility verification passed 24 Standard-mode RTL checks (eight at each
of three delay configurations), eight RTL tests for each SPI mode 0..3, and
43 Python unit tests. Filtered cases are not included in these pass counts.
An earlier floating-point checker boundary failure was corrected and the
affected corner rerun; no firmware change was needed for that failure.

## General-purpose architecture and costs

The firmware occupies **127/128 words**. The hardware adds a 128-word opt-in
execution mode while retaining the 32-word legacy window; all PC, branches,
return addresses and program loading span the larger space. Data RAM is 32
bytes. A four-entry return stack supports shared firmware subroutines. Generic
OE bit operations, RAM load/store, zero/decrement branches, input-bit shifting,
register OR/direction and a 12-bit WAIT are available to any protocol.

R0 is the TX/RX shift register or terminal status; R1 is extraction/input scratch;
R2 is the outer byte count/buffer index; R3 is the bit counter. Clock polling
temporarily saves R3 in RAM22 and uses it as its timeout counter. Maximum actual
call depth is two. No register, opcode or RTL FSM represents an I2C protocol phase.

| Workload | Firmware words | Data handling |
| --- | ---: | --- |
| Existing UART TX | 27 in legacy mode | Original one/two-byte embedded payload, unchanged |
| Existing SPI full duplex | 31 default in legacy mode | Original one/two-byte payload and R0/R3 RX, unchanged |
| Earlier I2C probe | 32 in legacy mode | Address-only, retained as regression |
| Complete I2C master | 127 in extended mode | Independent runtime RAM, up to 15 TX + 15 RX bytes |

Program and data storage map to registers and selection logic rather than SRAM;
isolating their individual area costs requires further measurement. The
wider timer and generic input/branch primitives reduce firmware overhead;
RAM separates configuration/payload from code and UI-only packets remove bus
loading interference. UART/SPI can adopt the same RAM and instruction features
in future firmware without adding protocol controllers. Their existing images
remain unmodified for compatibility evidence.

Physical measurements and exact source revisions are recorded in
[extended-core validation](pio-extended-validation.md). The same toolchain and
library are used for baseline and new synthesis so the comparison includes
actual mapped logic rather than flip-flop estimates.

## Reproduction and verification

The focused runner creates a fresh `/tmp/forg-i2c-<mode>.*` snapshot from the
current working files. It preserves XML, simulator log, FST, source hashes and
measurement JSON without touching existing build or physical artifacts.
Dependencies: Python with `test/requirements.txt`, Icarus Verilog and GNU make.
At this checkout the WSL environment is selected with:

```bash
export PATH=/tmp/forg-regression/venv/bin:/usr/bin:/bin
cd /mnt/d/CS4444/FoRG-protocol-emulator
python -B scripts/i2c_master.py --mode fast --output /tmp/forg-i2c-fast.hex
# Fast: run these two bounded suites separately for EACH row in the table.
# Arguments after the mode: SCL-rise SCL-fall SDA-rise SDA-fall, in ns.
bash scripts/test-i2c-modes.sh fast 0 0 0 0 base
bash scripts/test-i2c-modes.sh fast 0 0 0 0 extended
# Standard compatibility: repeat these three groups at 0/0/0/0,
# 0/300/0/0, and 1000/0/1000/300. Fast-only tests are skipped.
bash scripts/test-i2c-modes.sh standard 0 0 0 0 smoke
bash scripts/test-i2c-modes.sh standard 0 0 0 0 ram
bash scripts/test-i2c-modes.sh standard 0 0 0 0 control
python -B -m unittest discover -s scripts -p 'test_*.py' -v
bash scripts/test-spi-modes.sh mode0
bash scripts/test-spi-modes.sh mode1
bash scripts/test-spi-modes.sh mode2
bash scripts/test-spi-modes.sh mode3
```

Fast `base` selects eight existing functional/error/mutation tests; `extended`
selects four Fast-specific tests. Standard `smoke`, `ram`, `control` select
2/1/5 tests, respectively. These groups avoid a long monolithic regression.
Inspect the runner's executed/skipped XML counts; test definitions alone are
not passing evidence. SPI's runner checks eight executed cases per mode.

The older commands below are retained for reference; they write to their
specified firmware/test locations. Use the isolated runner above for audits.

```bash
python3 scripts/i2c_master.py --output firmware/i2c_master.hex \
  --address 0x50 --write 0x10 --read-count 4 \
  --data-output firmware/i2c_register_read.json
python3 -m unittest discover -s scripts -p 'test_*.py' -v
bash scripts/test-local.sh TARGET=i2c_master
bash scripts/test-local.sh TARGET=i2c_master I2C_SCL_FALL_NS=300
bash scripts/test-local.sh TARGET=i2c_master I2C_RISE_NS=1000 I2C_SDA_FALL_NS=300
```

The reference target implements independent register memory, address decoding,
ACK/NACK policy, TX data and read ACK ownership based only on resolved edges.
Tests exercise 1..15 bytes, arbitrary data/address values, multiple RAM-only
transactions on one loaded program, all NACK classes, repeated-START register
reads, delayed edges, stretch including STOP, pause, reset, invalid configuration,
busy/stuck lines and missing STOP. Trace mutations and actual corrupted firmware
exercise data, timing, ACK, input sampling, SCL polling and active-HIGH failures.
A stretched-STOP case checks that firmware status remains busy before completion.

Core tests cover the original ISA unchanged, upper program pages, 127-to-0 wrap,
full-width branches/readback, partial/incomplete loading, RAM validity, stack
faults, long WAIT, pause and both byte and UI-packet host interfaces. Existing
UART, SPI, probe and legacy regressions remain enabled. CI preserves XML,
waveforms and measurements and validates XML after each run. With a routed
netlist and PDK installed, `TARGET=core`, `spi_firmware`, `i2c_firmware` and
`i2c_master` support functional gate simulation using `GATES=yes`.

Measurements are generated in `test/output/i2c-master-*.json`. Full regression:

```bash
set -e
for target in core uart_firmware spi_firmware legacy; do
  bash scripts/test-local.sh TARGET="$target"
done
for divisor in 1 2 4 17 434; do
  bash scripts/test-local.sh TARGET=uart UART_CLKS_PER_BIT="$divisor"
done
for target in i2c_firmware i2c_master; do
  bash scripts/test-local.sh TARGET="$target"
  bash scripts/test-local.sh TARGET="$target" I2C_SCL_FALL_NS=300
  bash scripts/test-local.sh TARGET="$target" I2C_RISE_NS=1000 I2C_SDA_FALL_NS=300
done
```
