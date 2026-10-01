# M1: fixed UART transmitter

`src/uart_tx.v` implements a dedicated 8N1 transmitter. It is a reference
design, not a peripheral embedded in the programmable engine.

## Contract

- Synchronous active-low reset aborts transmission and sets TX high, busy low.
- On an idle rising edge with start high, capture data and drive the start bit.
- Send eight bits, least-significant bit first, followed by one high stop bit.
- Every bit lasts exactly CLKS_PER_BIT cycles; the parameter must be >= 1.
- Busy stays high for the entire 10-bit frame. Requests while busy are ignored.
- If start is held high, another frame starts on the next idle rising edge.
- Input data can change after acceptance without affecting the current frame.

The Tiny Tapeout wrapper uses ui_in as the byte, uio_in[0] as start, uo_out[0]
as TX, and uo_out[1] as busy. ena gates acceptance only; it does not interrupt
an active frame. All bidirectional pins remain inputs.

Default CLKS_PER_BIT is 434. At the 50 MHz physical-design target, the nominal
baud rate is approximately 115207 baud. The testbench clock need not match
that target: tests validate cycles per bit, not demonstrated silicon speed.

## Run

```bash
bash scripts/test-local.sh TARGET=uart UART_CLKS_PER_BIT=4
bash scripts/test-local.sh TARGET=uart
```

Tests check every bit interval, independently decode sampled payloads, and
exercise 0x00, 0x55, 0xAA, 0xFF, seeded random bytes, busy requests, consecutive
frames, held start, disabled acceptance, and reset mid-frame. CI uses bit
periods of 1, 2, 4, 17, and 434 cycles.

The uart-baseline workflow builds only the UART wrapper and transmitter,
then runs physical precheck and gate-level tests. Area and timing must be
taken from that workflow, not from the programmable engine's build.
