# Runtime Program Loader

The loader adds whole-image transactions to the existing UI-only host packets.
It reuses the 128x16 flip-flop program memory, byte-valid bits, GPIO interface,
and legacy/extended ISA. There is no new memory macro, UART/SPI/I2C controller,
assembler, clock domain, or external pin. The implementation is based on the
I2C-capable `feature/i2c-firmware` revision `7b62a02`, not the older `main` core.
`project.v` remains the independent legacy demonstration; the ASIC top remains
`tt_um_forg_protocol_engine` in `protocol_top.v`.

## Pins and electrical timing

| Pins | Loading use |
|---|---|
| `clk` | Existing system clock; design target remains 50 MHz / 20 ns |
| `rst_n` | Active-low synchronous reset; takes priority over `ena` |
| `ena` | High to accept host samples; low freezes all sequential state |
| `ui_in[7]` | RUN: keep zero throughout upload; set one only after commit |
| `ui_in[6:5]` | `01` selects packet samples |
| `ui_in[4]` | Toggle strobe; each change accepts one nibble |
| `ui_in[3:0]` | Packet nibble, most significant first |
| `uo_out[7:0]` | Existing debug mux plus loader status/count/length selectors |
| `uio[7:0]` | Remain protocol GPIO; all output enables zero while stopped/loading |

All input controls and data must meet setup/hold relative to `clk`. Change the
whole UI byte together, preferably on a falling edge, and retain it through at
least one enabled rising edge. A sample may be held for many clocks without
repeated acceptance. There is no CDC synchronizer or asynchronous serial port:
an independently clocked MCU cannot simply toggle these pins arbitrarily. The
host must supply/coordinate the clock or use a synchronous bridge. It may step
the clock slowly during upload, then use the intended execution clock.

## Packet format

First present `ui_in=0x1e` for an enabled rising edge to discard any partial
packet and establish strobe zero. Send the 24-bit value `(address << 16) | value`
as six nibbles, MSB first. For each nibble, toggle bit 4 and present
`0x20 | strobe | nibble`. Strobes are therefore 1,0,1,0,1,0. The sixth accepted
nibble applies the complete packet atomically. Start each packet with `0x1e`
for simple resynchronization; this discards framing only, not the image transaction.

| Address | Value / action |
|---|---|
| `0xfe` | BEGIN: bits 15:9 zero, bit 8 extended ISA, bits 7:0 image length |
| `0x00..0x7f` | One 16-bit instruction; transactional writes must be sequential from zero |
| `0x80..0x9f` | Existing data RAM write; high value byte must be zero |
| `0xfd` | COMMIT: value must be zero; succeeds only after exactly the declared words |
| `0xff` | Existing mode command; prohibited after transactional BEGIN until reset |
| `0xa0..0xfc` | Invalid/reserved; never alias a program or RAM address |

Length is 1..32 in legacy mode or 1..128 in extended mode. The length byte
encodes 128 as `0x80`, not zero. Thus BEGIN for a 128-word extended image is
`(0xfe, 0x0180)`; for a 27-word UART image it is `(0xfe, 0x001b)`.

In loading mode the following UI values select combinational readback (no write):

| `ui_in` | `uo_out` |
|---|---|
| `0x1d` | `{managed, committed, error, 5'b0}`: reset `00`, loading `80`, ready `c0`, error `a0` |
| `0x1c` | Number of complete program words accepted, 0..128 |
| `0x1b` | Declared image length |

Sample readback after propagation settles. These selectors keep RUN low and
must not be used to inspect status while continuing execution. Existing RUN
debug selectors and instruction cycle counts are unchanged.

## States, replacement, and errors

- Reset invalidates all program/data validity, clears execution and loader
  state, and selects legacy mode. Physical storage bits need no reset and are
  never executed/read as valid until written. RUN without a loaded word faults.
- BEGIN stops/rewinds the CPU, clears all old program/data validity, latches
  length/mode, and prevents execution. It starts a fresh transaction even after
  a fault. There is a single memory bank: the old image is deliberately lost.
- Load instruction addresses 0 through length-1 exactly once, in order. Idle
  gaps and paused clocks are allowed. Aborting a partial packet with `0x1e`
  does not change the expected word address; resend that complete packet.
- COMMIT succeeds only with the exact length received and no sticky loader
  error. It does not start the CPU. Check ready, optionally initialize data RAM,
  then assert RUN. Execution starts at PC zero on the first enabled RUN edge.
- RUN before commit, or RUN interrupting a packet, faults without executing a
  single instruction. Duplicate/out-of-order/out-of-length program writes,
  invalid BEGIN/COMMIT, reserved addresses, malformed RAM writes and attempts
  to bypass the loader with parallel writes/mode changes invalidate commitment
  and set a sticky loader error. Recovery requires a fresh BEGIN or reset.
- Program writes after commit are rejected; use a new BEGIN to replace code.
  Dropping RUN without writes merely rewinds a committed program so it can run
  again. Valid data RAM packets remain available while stopped, before or after
  commit, for the existing RAM-configured I2C firmware.
- Unloaded tail instructions remain invalid after a shorter replacement;
  branching/falling into them faults. Legacy PC wrap at 32 and extended wrap at
  128 are unchanged. Extended program/data/stack fault checks are unchanged.

Backward compatibility is explicit: until the first BEGIN after reset, the
existing byte/banked and atomic-word loaders retain their original behavior.
Those legacy paths have per-word validity, **not whole-image transaction
protection**. New integrations should use transactional loading. Once entered,
the managed mode cannot be bypassed without reset. Formerly reserved packet
addresses `fd/fe` and idle readback selectors `1b..1d` now have defined meanings.

Malformed framing/addresses/counts are checked, but there is no checksum or
authentication. A corrupted yet syntactically valid instruction cannot be
detected by this loader. An incomplete transfer can wait indefinitely with the
CPU stopped; there is no timeout and no automatic execution/rollback.

## Host example: UART, then SPI without reset

`scripts/pio_host.py` is a transport codec, not an assembler or board driver.
It rejects invalid lengths and words before returning any commands.

```python
from scripts.pio_host import program_writes
from scripts.uart_firmware import generate_uart_tx
from scripts.spi_firmware import generate_spi_master

def upload(words):
    # apply_ui_and_clock meets the synchronous timing contract above.
    for control in program_writes(words, transactional=True):
        apply_ui_and_clock(control)  # ena=1, rst_n=1; do not drive uio
    apply_ui_and_clock(0x1d)
    assert read_uo() == 0xc0
    apply_ui_and_clock(0xa0)  # RUN, execution status readback

reset_once()
upload(generate_uart_tx([0x96]))
wait_for_uart_frame()
upload(generate_spi_master([0x3c], half_period_cycles=16))
wait_for_spi_transaction()
```

For a raw `firmware/*.hex` file, parse each line with `int(line, 16)` and supply
the words to the same codec. Use `extended=True` for the 127-word I2C master,
then `data_writes(transaction_data(...))` to configure RAM before RUN.

## Reproduce verification

With `make`, Icarus and `test/requirements.txt` installed:

```bash
cd test
make -B TARGET=loader
python ../scripts/check-test-results.py results.xml
```

The test `test_uart_then_spi_same_build_no_reset` resets once, uploads UART,
checks all ten 434-cycle symbols, replaces it with SPI through the pins, and
checks MOSI and received MISO against an independent slave. Both run in one
Cocotb test, one compiled build and one DUT. No internal memory writes are used.
Other loader tests cover incomplete uploads, bounds, reset, pause, held strobes,
frame abort, stale tails, commits, post-commit edits, and the host codec.
Default `TARGET=core` includes these tests, also for `GATES=yes`.

Run `bash scripts/test-program-loader.sh` from an activated test environment for
all core/UART/SPI/legacy regressions plus both I2C suites at three bus-delay
settings. Reports/logs are in `build/program-loader/rtl/`. One full I2C smoke
test uses the transactional loader; the remaining cases preserve legacy loading.

## Physical scope

`info.yaml` still requests 6x4 and 50 MHz; `src/config.json` still uses 20 ns and
all-corner checks. The official `TinyTapeout/tt-gds-action@ihp-cmos5l` workflow
is unchanged. New RTL requires fresh synthesis, routing, extracted timing,
DRC/LVS, independent Tiny Tapeout precheck and gate simulation. Old I2C GDS and
its hash-guarded handoff script cannot sign off this loader. RTL or pre-route
STA alone does not demonstrate 50 MHz closure. See the measured run summary
in [program-loader-results.md](program-loader-results.md).
