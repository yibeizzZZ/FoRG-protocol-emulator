# M3: firmware-based UART TX

The unchanged M2 engine transmits UART 8N1 on `uio[0]`: start LOW, eight
data bits LSB first, no parity, stop HIGH. The generator accepts any one or
two byte values. Both bytes are embedded in one program and transmit without
host writes between them. Only `uio[0]` is enabled; all other GPIO outputs
remain disabled. The core does not instantiate or use `uart_tx.v`.

## Generate and run

Generate the checked-in `0x55, 0xAA` example, or substitute your payload:

```bash
python3 scripts/uart_firmware.py 0x55 0xAA --output firmware/uart_tx.hex
python3 scripts/uart_firmware.py 0x00 0xFF --output /tmp/uart_tx.hex
python3 scripts/uart_firmware.py 85
python3 -m unittest discover -s scripts -p 'test_*.py' -v
bash scripts/test-local.sh TARGET=uart_firmware
python3 scripts/check-test-results.py test/results.xml
```

The CLI accepts decimal or `0x` hexadecimal bytes. With no `--output`, it
prints the hex image to stdout. Empty payloads, more than two bytes, and
values outside 0..255 are rejected before writing a file. The Python API is
`generate_uart_tx(payload) -> list[int]` in `scripts/uart_firmware.py`.
Each hex line is one 16-bit instruction, not a byte.
The generator now shares encoding, label resolution, exact-cycle WAITs and
capacity checks with SPI through `scripts/pio_firmware.py`. Its public API,
instruction image and UART timing remain unchanged.

For hardware loading, use the existing [M2 host interface](isa.md). Hold
RUN low; for each word address, write its low byte and then high byte using
`ui_in[6]=1`, `ui_in[5]` as byte select, and `uio_in` as data. Then assert
RUN and keep `ena=1`, `rst_n=1`, with a 50 MHz clock throughout transmission.
The first driven TX value is HIGH for exactly 434 cycles before the start
bit. TX is released during reset/loading, so an external pull-up is needed
if the receiving circuit requires an idle HIGH level during those periods.
After the last byte, TX remains actively HIGH until RUN is cleared or reset
is asserted. Loading a new image restarts execution without requiring reset.

With dependencies already installed, `cd test && make -B TARGET=uart_firmware`
also runs the regression. Its waveform is `test/uart_firmware.fst`.

## Program and timing

R0 holds the shifting payload, R1 the masked LSB, R2 the remaining data bits,
and R3 the remaining frames. Addresses below are decimal. For a single byte,
R3 starts at 1 and the second-byte block is unreachable; image size is unchanged.

| Address | Instruction | Purpose |
|---|---|---|
| 0 | MOVI R3, 2 | One or two frames |
| 1 | MOVI R0, 0x55 | First payload |
| 2 | SET 1 | Set idle before enabling the pin |
| 3 | DIR 1 | Drive TX only |
| 4–5 | WAIT 255; WAIT 175 | Initial idle delay |
| 6 | MOVI R2, 8 | Start of shared frame routine |
| 7 | SET 0 | Start bit |
| 8–9 | WAIT 255; WAIT 174 | Start-bit delay |
| 10–12 | MOV R1, R0; ANDI R1, 1; OUT R1 | Emit the current LSB |
| 13 | SHR R0 | Prepare the next bit |
| 14–15 | WAIT 255; WAIT 171 | Data-bit delay |
| 16–17 | ADDI R2, 255; JNZ R2, 10 | Eight-bit loop |
| 18 | WAIT 1 | Balance the final-bit path |
| 19 | SET 1 | Stop bit |
| 20–21 | WAIT 255; WAIT 171 | Stop-bit delay |
| 22–23 | ADDI R3, 255; JNZ R3, 25 | Select another frame or finish |
| 24 | JMP 24 | Keep TX driven HIGH indefinitely |
| 25–26 | MOVI R0, 0xAA; JMP 6 | Second payload, same frame routine |

Every ordinary instruction takes one enabled cycle. `WAIT n` takes one
issue cycle plus **n extra cycles**; therefore WAIT 255 takes 256 cycles.
Intervals between the pin-changing clock edges are:

| Interval | Cycles after the first edge, including the next changing edge |
|---|---|
| DIR to start | 256 + 176 + MOVI(1) + SET(1) = **434** |
| Start to data bit 0 | 256 + 175 + MOV(1) + ANDI(1) + OUT(1) = **434** |
| Data bit to next data bit | SHR(1) + 256 + 172 + ADDI(1) + JNZ(1) + MOV(1) + ANDI(1) + OUT(1) = **434** |
| Data bit 7 to stop | SHR(1) + 256 + 172 + ADDI(1) + JNZ(1) + WAIT 1(2) + SET(1) = **434** |
| Stop to next start | 256 + 172 + ADDI(1) + JNZ(1) + MOVI(1) + JMP(1) + MOVI(1) + SET(1) = **434** |

JNZ costs one cycle whether taken or not. Masking and output take the same
path for both data values, so payload bits do not change timing. The final
stop level extends into indefinite idle; there is no extra falling edge.
HALT is deliberately unused because M2 HALT releases output enables.

## Measurements and comparison

Measured with Icarus/cocotb at a 20 ns testbench clock on 2026-10-08; baud
and durations below are calculated from the verified cycle counts.

| Metric | M3 firmware on M2 | Independent M1 UART |
|---|---|---|
| Instruction image | 27 / 32 words = 54 bytes | No firmware |
| Cycles per bit | 434, including execution overhead | 434 at divisor 434 |
| Bit duration at 50 MHz | 8.68 us | 8.68 us |
| Achieved baud | 115207.373 baud | 115207.373 baud |
| Error relative to 115200 | +0.0064004% | +0.0064004% |
| Frame duration | 4340 cycles = 86.8 us | 4340 cycles = 86.8 us |
| Consecutive start interval | 4340 cycles (one stop bit) | Minimum 4341 cycles: start accepted on the next idle edge |
| Data path | Existing M2 GPIO, registers, shifts and branches | Dedicated `uart_tx.v` |

Seven of eight data-bit intervals execute 8 instructions including the two
WAIT issues, plus 426 stalled cycles. The last data bit executes 7
instructions plus 427 stalled cycles because WAIT 1 balances loop exit.
The register/branch overhead is small at this baud but matters at higher rates.
There is no RTL change or additional synthesized UART hardware in M2.
Existing area/timing evidence remains in [results.md](results.md); this
milestone does not claim a new physical build, FPGA test, or measured Fmax.

## Verification and CI

`test/tb_uart_firmware.v` instantiates the M2 top and a separate fixed UART
only as a reference. There is no reference-to-M2 signal connection. Cocotb
first captures the entire firmware stream using GPIO pins, then exercises
the reference and compares each 4340-cycle frame. A separate symbol check
tests every clock sample and decodes the byte at bit centers. Reference
inter-frame idle is excluded from frame comparison and documented above.

The five firmware tests cover:

- Single bytes 0x00, 0x55, 0xAA, 0xFF; eight directed/random two-byte streams.
- The checked-in image, full startup idle, exact consecutive frame spacing,
  upper GPIO isolation, active OE throughout transmission and final idle.
- Stop/reset during transmission, program invalidation, and reprogramming.
- Deliberately adding one cycle to the start WAIT or flipping a payload bit;
  both must be rejected by the waveform checker.

Generator tests cover all 256 byte values as both single-byte and
complementary two-byte inputs, image capacity, CLI validation, and reproducibility.
This generator sweep checks image validity; it is not an exhaustive RTL sweep.
Local verification passed all 27 RTL tests (M2 core 5, M3 firmware 5, legacy 2,
and M1 UART 3 at each of five divisors) and all 8 Python unit tests (5 generator,
3 XML-validator tests with 17 XML fixtures). The independent code review found
no actionable issues.
CI runs these unit tests and the new firmware target, then checks its XML
with the shared validator. Existing core, all five fixed-UART divisors, and
legacy tests remain enabled. Named XML and waveform artifacts are preserved.

Reproduce the full regression from the repository root:

```bash
set -e
python3 -m unittest discover -s scripts -p 'test_*.py' -v
for target in core uart_firmware legacy; do
  bash scripts/test-local.sh TARGET="$target"
  python3 scripts/check-test-results.py test/results.xml
done
for divisor in 1 2 4 17 434; do
  bash scripts/test-local.sh TARGET=uart UART_CLKS_PER_BIT="$divisor"
  python3 scripts/check-test-results.py test/results.xml
done
```

The comparison target is RTL-only; the existing `TARGET=core GATES=yes`
path remains available for the independent M2 gate regression.

## Architectural limitations and next experiments

- This generator fixes timing at 434 cycles/bit and accepts one or two bytes
  per image. It is not a streaming UART API. Payloads live in MOVI immediates;
  changing them requires stopping and loading the program again. M2 has no
  FIFO or data RAM, and RUN excludes program writes. A future generator could
  spend the five free words on more payload dispatch, or explore synchronous
  input handshakes; no fundamental two-byte limitation is claimed for the ISA.
- WAIT is only eight bits, so each long delay needs two instructions. A
  longer timer or repeat primitive could reduce the 27-word footprint.
- Four registers are occupied. MOV/ANDI/OUT, shifting and explicit bit-loop
  bookkeeping limit fast bit rates; a shift-and-output or counted-loop
  primitive is worth measuring against its area cost.
- Pausing `ena` stretches UART bits in wall-clock time. RUN and reset release
  TX. Keep the engine selected and unpaused for a valid frame.
- The engine is occupied for the entire transfer, including waits. It does
  not concurrently service another protocol. Final idle loops consume clocks
  because HALT cannot retain driven GPIO; a sleep-with-output mode could help.
- No UART RX, parity, flow control, indefinite queues, or arbitrary baud
  scheduling is provided. Those are separate experiments, not RTL changes
  required for this 8N1 TX demonstration.
