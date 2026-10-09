# M2 minimal programmable engine, ISA v0.1

This is an experimental single-engine architecture, not the M7 architecture
freeze. M1 remains a separate reference design. The original three-opcode
engine is preserved in project.v and tested with TARGET=legacy.

## State and host interface

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
  RUN mode while not halted/faulted and not reset. No I2C electrical behavior
  is implied; open-drain operation would need firmware using direction bits.
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

32x16 storage and four registers are deliberately small but sufficient for
short loops and instruction experiments. Two validity bits per word allow
safe execution after byte-wise loading without resetting all storage flops.
Byte-wide GPIO and register readback support protocol experiments and tests.
M3's [UART TX firmware and generator](uart-firmware.md) use this unchanged ISA
to transmit one or two bytes. There is no FIFO, interrupt, multi-engine
scheduler, general assembler, or SPI/I2C firmware. Physical measurements
determine later revisions.
