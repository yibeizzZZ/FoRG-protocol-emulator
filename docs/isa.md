# Programmable engine ISA: legacy v0.1 and opt-in extensions

This is an experimental single-engine architecture, not the M7 architecture
freeze. M1 remains a separate reference design. The original three-opcode
engine is preserved in project.v and tested with TARGET=legacy.

The following original M2 contract applies in **legacy mode**, selected on
reset. The physical core now contains 128 words and data RAM; legacy execution
still wraps at 32 words. Existing UART, SPI, and probe images and loaders remain
valid. See the extension section below for the new mode and loading commands.

## Legacy state and host interface

- 32 words of 16-bit writable instruction storage; 5-bit wrapping PC.
- Four 8-bit registers R0..R3, an 8-bit wait counter, 8 output data bits,
  and 8 output-enable bits. All external inputs must be synchronous to clk.
- rst_n is active-low and synchronous for state. Reset clears execution
  state and program validity, requiring a fresh load. Storage bits are not
  physically reset. Output enables are also suppressed while rst_n is low.
- ena=0 freezes all sequential state, including host writes. Driven GPIO
  remains stable while paused in RUN mode. Leaving RUN releases GPIO.
- ui_in[7] is RUN. When zero, execution is stopped and PC, wait, registers,
  outputs, halt, and fault state are cleared on each enabled edge.
- While stopped, ui_in[6]=1 writes uio_in to the instruction byte selected
  by ui_in[4:0] (word address) and ui_in[5] (0=low, 1=high byte).
- Both bytes must have been written since reset before a word can execute.
  Rewriting one byte retains the other previously loaded byte. Load both
  bytes when replacing a word. Writes are never accepted in RUN mode.
- Unloaded-word fetch or opcode E sets fault and halt without advancing PC;
  GPIO output enables are released. Loading mode clears fault/halt.
- uio_out carries output data; uio_oe carries the direction mask only in
  RUN mode while not halted/faulted and not reset. Open-drain operation is implemented by firmware holding output data LOW
  and changing direction bits; the core has no protocol-specific electrical mode.
- During RUN, ui_in[6:5] selects uo_out readback: 00=output data;
  01={fault, halted, wait_active, PC[4:0]}; 10=R[ui_in[1:0]];
  11=stored direction mask. Readback selection does not advance execution.

## Encoding

Bits 15:12 are opcode, 11:10 destination register, 9:8 source register,
7:0 immediate. Fields unused by an instruction are ignored.

| Opcode | Mnemonic | Operation |
|---|---|---|
| 0 | SET imm8 | Set all output data bits |
| 1 | WAIT imm8 | Wait imm8 additional enabled RUN edges |
| 2 | JMP addr5 | Set PC to imm[4:0] |
| 3 | READ rd | Capture uio_in into rd |
| 4 | MOVI rd, imm8 | Load immediate |
| 5 | MOV rd, rs | Copy register |
| 6 | JNZ rd, addr5 | Jump if rd is nonzero, otherwise advance |
| 7 | SHR rd | Logical right shift by one |
| 8 | SHL rd | Logical left shift by one, discard overflow |
| 9 | OUT rd | Copy rd to output data |
| A | DIR imm8 | Set output-enable mask |
| B | ANDI rd, imm8 | Bitwise AND |
| C | ADDI rd, imm8 | Add modulo 256; 0xFF decrements |
| D | HALT | Halt and release GPIO; PC stays on HALT |
| E | reserved | Fault and halt |
| F | NOP | Advance PC only |

Each ordinary instruction executes on one enabled rising edge and advances
PC modulo 32, except a taken branch or HALT/fault. WAIT advances PC at issue,
then consumes imm8 extra edges without changing PC, registers or GPIO.
WAIT 0 therefore costs one edge; WAIT 255 costs 256 edges. A paused engine
does not consume wait time. This is a cycle contract, not an Fmax claim.

## Firmware examples

`firmware/engine_blink.hex` repeats a byte-wide high/low pattern.
`firmware/engine_countdown.hex` uses a register, output, decrement, and a
conditional branch, then halts. Each line is one 16-bit hexadecimal word.

## Tradeoffs

The legacy 32-word execution window and four registers are sufficient for
short loops and instruction experiments. Two validity bits per word allow
safe execution after byte-wise loading without resetting all storage flops.
Byte-wide GPIO and register readback support protocol experiments and tests.
M3's [UART TX firmware and generator](uart-firmware.md) use this unchanged ISA
to transmit one or two bytes. [SPI Mode-0 firmware](spi-firmware.md) exchanges
one or two bytes full duplex using the same ISA and shared instruction builder.
There is no FIFO, interrupt, multi-engine scheduler, general text assembler,
or dedicated protocol controller. The complete [I2C firmware](i2c-master.md)
uses the opt-in extensions below; its measured costs are recorded there.


## Opt-in extended mode

The architecture remains a single general-purpose engine. Reset selects legacy
mode, bank 0, empty call stack and invalid program/data storage. To enter extended
mode use UI packet `FF0001`, or stopped bank command `ui_in=0x1f`,
`uio_in=0x80 | bank`. `FF0000` returns to legacy mode and bank 0. Mode changes
are accepted only while stopped and enabled. No instruction is I2C-specific.

- 128x16 program storage; PC, branches, sequential wrap and return addresses
  use seven bits. Branch immediates use bits 6:0; the assembler rejects targets
  outside 0..127. Legacy mode still uses five bits and wraps at 31.
- 32x8 data RAM with per-byte validity. RAM and program persist across RUN=0;
  reset invalidates both. Stores mark bytes valid. Invalid loads, indirect
  addresses above 31, bad instruction fetches, reserved opcodes and stack
  under/overflow halt with fault and release GPIO at the faulting PC.
- WAIT uses instruction bits 11:0, for 1..4096 total enabled cycles. Legacy
  WAIT ignores the upper four bits and still costs imm8+1.
- A four-entry return stack holds seven-bit PCs. CALL pushes the following PC;
  RET pops it. Stopping clears the stack and all four working registers.

Extended readback, while RUN stays set:

| `ui_in` | `uo_out` |
| --- | --- |
| `0x80 | address` | Data RAM[0..31], zero for an invalid byte |
| `0xA0` | `{fault, halted, wait_active, PC[4:0]}` |
| `0xC0 | register` | R0..R3 |
| `0xE0` | Stored direction mask |
| `0xE1` | Full seven-bit PC (bit 7 zero) |
| `0xE2` | Output data latch |

Other extended mux-11 addresses return direction. Legacy readback remains
unchanged for all addresses. Readback changes need not clock the engine.
A host must distinguish hardware fault/HALT from firmware status in data RAM.

## Extended instruction encodings

Legacy opcodes 0..D keep their operations, subject to the wider PC/WAIT above.
E/F encodings below are decoded **only in extended mode**. In legacy mode all
E instructions still fault and all F instructions still mean NOP.

| Hex encoding | Assembly | Operation |
| --- | --- | --- |
| `Eraa`, r=0..3 | JZ Rr, address | Branch when register is zero |
| `E4mm` | OESET mask | direction = direction OR mask |
| `E5mm` | OECLR mask | direction = direction AND NOT mask |
| `E6xx` | LDA rd, address | Load RAM; xx[7:6]=rd, xx[4:0]=address |
| `E7xx` | STA rd, address | Store RAM using the same fields |
| `E8xx` | LDB rd, rs | Load RAM[Rrs]; xx[3:2]=rd, xx[1:0]=rs |
| `E9xx` | STB rd, rs | Store Rrd at RAM[Rrs] |
| `EAaa` | CALL address | Push next PC and branch |
| `EB00` | RET | Pop return PC |
| `ECxx` | OR rd, rs | Register OR; same register fields as LDB |
| `EDxx` | DIRR rd | Copy Rrd to direction; xx[1:0]=rd |
| `Fraa`, r=8..B | DJNZ R(r-8), address | Decrement modulo 256, then branch if nonzero |
| `FCxx` | INBIT rd, pin | Shift register left, insert actual input pin at bit 0; xx[4:3]=rd, xx[2:0]=pin |

`EE/EF` and `FD/FE/FF` are reserved and fault in extended mode. `F0..F7` remain
NOP. Unused fields are ignored by hardware. `Program(extended=True)` provides
bounds checks, labels, CALL/JZ/DJNZ, RAM operations and long delays. It rejects
register operands on NOP/WAIT because those bits have different meanings in
extended encodings. Default `Program()` retains the 32-word legacy contract.

## Host programming without touching GPIO

The preferred host interface carries all data on the eight dedicated `ui_in`
pins. `uio` can stay connected to a live external bus, with the core released
while stopped. No external program/bus mux is required for this path.

1. Drive `ui_in=0x1e` on an enabled edge to abort a partial packet and establish
   a zero strobe. This does not invalidate memory or change mode.
2. Send six nibbles, most significant first, representing a 24-bit packet:
   eight-bit destination followed by sixteen-bit value.
3. Each nibble uses `ui_in=0x20 | strobe | nibble`, alternating strobe bit 4
   between 1 and 0. Only a **change** of the strobe consumes a nibble. Holding
   the same command across multiple clocks cannot duplicate a write.
4. The sixth accepted nibble commits atomically. Remain stopped or send another
   packet. Reset, RUN, or `0x1e` discards a partial packet; ena=0 freezes it.

| Destination | Value and action |
| --- | --- |
| `0x00..0x7f` | Complete 16-bit program word, both validity bits set atomically |
| `0x80..0x9f` | RAM byte 0..31, value[15:8] must be zero |
| `0xff` | Value 0=legacy, 1=extended; select bank 0 |

Other destinations and invalid values are ignored. An aborted partial write
leaves a previously valid word unchanged. After reset an incomplete frame
cannot create a valid instruction. There is no write acknowledgment pin: the
host must satisfy synchronous input setup/hold and strobe timing. Each command
returned by `scripts/pio_host.py` must span at least one enabled rising edge.
The utility validates ranges and emits packet-reset/mode/write sequences. All packets
are stopped-mode operations; there is no concurrent host DMA while RUN is set.

The old byte loader also remains available. Command `ui_in=0x1f` selects
`extended=uio_in[7]` and `bank=uio_in[2:0]`; banks 0..3 select 32-word program
pages, bank 4 selects RAM. Subsequent old `0x40|address`/`0x60|address` writes
load low/high program bytes. RAM uses low-byte writes only; banks 5..7 ignore
writes. This alternative still drives uio for payload and requires bus
isolation. Existing loaders start in bank 0 after reset. Hosts switching from
extended operation to an old loader must explicitly select legacy mode/bank 0.
`0x1e`, `0x1f`, and `0x20..0x3f` are now reserved stopped-mode commands.
