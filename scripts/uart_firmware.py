"""Generate one or two 8N1 UART frames for the unchanged M2 engine on uio[0]."""

import argparse
from pathlib import Path


CLOCK_HZ = 50_000_000
CYCLES_PER_BIT = 434


def generate_uart_tx(payload):
    """Return a 27-word program; payload contains one or two integer bytes.

    R0 shifts the current byte, R1 masks its LSB, R2 counts data bits,
    and R3 counts frames. Payloads are embedded in MOVI instructions.
    """
    payload = list(payload)
    if not 1 <= len(payload) <= 2:
        raise ValueError("payload must contain one or two bytes")
    if any(type(byte) is not int or not 0 <= byte <= 255 for byte in payload):
        raise ValueError("each payload value must be an integer in 0..255")

    words, labels, branches = [], {}, []

    def emit(opcode, rd=0, rs=0, imm=0):
        words.append((opcode << 12) | (rd << 10) | (rs << 8) | imm)

    def label(name):
        labels[name] = len(words)

    def jump(opcode, target, rd=0):
        branches.append((len(words), target))
        emit(opcode, rd=rd)

    def wait(cycles):
        # WAIT n spends one issue cycle plus n stalled execution cycles.
        while cycles:
            chunk = min(cycles, 256)
            emit(0x1, imm=chunk - 1)
            cycles -= chunk

    emit(0x4, rd=3, imm=len(payload))            # MOVI R3, frame count
    emit(0x4, rd=0, imm=payload[0])              # MOVI R0, first byte
    emit(0x0, imm=1)                            # SET 1 before driving TX
    emit(0xA, imm=1)                            # DIR 1: only uio[0] drives
    wait(CYCLES_PER_BIT - 2)                    # MOVI + SET complete idle bit
    label("frame")
    emit(0x4, rd=2, imm=8)                      # MOVI R2, 8
    emit(0x0, imm=0)                            # SET 0: start bit
    wait(CYCLES_PER_BIT - 3)                    # MOV + ANDI + OUT complete start
    label("bit")
    emit(0x5, rd=1, rs=0)                       # MOV R1, R0
    emit(0xB, rd=1, imm=1)                      # ANDI R1, 1
    emit(0x9, rd=1)                             # OUT R1
    emit(0x7, rd=0)                             # SHR R0
    wait(CYCLES_PER_BIT - 6)                    # SHR/ADDI/JNZ/MOV/ANDI/OUT
    emit(0xC, rd=2, imm=255)                    # ADDI R2, -1
    jump(0x6, "bit", rd=2)                     # JNZ R2, bit
    wait(2)                                    # Replace MOV + ANDI on loop exit
    emit(0x0, imm=1)                            # SET 1: stop bit
    wait(CYCLES_PER_BIT - 6)                    # ADDI/JNZ/MOVI/JMP/MOVI/SET
    emit(0xC, rd=3, imm=255)                    # ADDI R3, -1
    jump(0x6, "second", rd=3)                  # JNZ R3, second
    label("idle")
    jump(0x2, "idle")                          # Keep TX driven; HALT releases it
    label("second")
    emit(0x4, rd=0, imm=payload[1] if len(payload) == 2 else 0)
    jump(0x2, "frame")

    if len(words) > 32:
        raise ValueError("program exceeds the 32-word M2 instruction memory")
    for address, target in branches:
        words[address] |= labels[target]
    return words


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", nargs="+", type=lambda value: int(value, 0),
                        help="one or two bytes, e.g. 0x55 0xAA or 85 170")
    parser.add_argument("--output", type=Path, help="hex output path (default: stdout)")
    args = parser.parse_args()
    try:
        words = generate_uart_tx(args.payload)
    except ValueError as error:
        parser.error(str(error))
    content = "".join(f"{word:04x}\n" for word in words)
    if args.output is None:
        print(content, end="")
    else:
        args.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
