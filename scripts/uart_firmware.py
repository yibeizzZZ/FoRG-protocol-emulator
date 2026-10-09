"""Generate one or two 8N1 UART frames for the unchanged M2 engine on uio[0]."""

import argparse
from pathlib import Path

if __package__:
    from .pio_firmware import Program
else:
    # Preserve standalone CLI and importlib file loading without changing sys.path.
    from importlib.util import module_from_spec, spec_from_file_location

    _spec = spec_from_file_location("pio_firmware", Path(__file__).with_name("pio_firmware.py"))
    _builder = module_from_spec(_spec)
    _spec.loader.exec_module(_builder)
    Program = _builder.Program


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

    program = Program()
    emit, label, jump, wait = program.emit, program.label, program.branch, program.wait

    emit("MOVI", rd=3, imm=len(payload))        # MOVI R3, frame count
    emit("MOVI", rd=0, imm=payload[0])          # MOVI R0, first byte
    emit("SET", imm=1)                         # SET 1 before driving TX
    emit("DIR", imm=1)                         # DIR 1: only uio[0] drives
    wait(CYCLES_PER_BIT - 2)                    # MOVI + SET complete idle bit
    label("frame")
    emit("MOVI", rd=2, imm=8)                  # MOVI R2, 8
    emit("SET", imm=0)                         # SET 0: start bit
    wait(CYCLES_PER_BIT - 3)                    # MOV + ANDI + OUT complete start
    label("bit")
    emit("MOV", rd=1, rs=0)                    # MOV R1, R0
    emit("ANDI", rd=1, imm=1)                  # ANDI R1, 1
    emit("OUT", rd=1)                         # OUT R1
    emit("SHR", rd=0)                         # SHR R0
    wait(CYCLES_PER_BIT - 6)                    # SHR/ADDI/JNZ/MOV/ANDI/OUT
    emit("ADDI", rd=2, imm=255)                # ADDI R2, -1
    jump("JNZ", "bit", rd=2)                 # JNZ R2, bit
    wait(2)                                    # Replace MOV + ANDI on loop exit
    emit("SET", imm=1)                         # SET 1: stop bit
    wait(CYCLES_PER_BIT - 6)                    # ADDI/JNZ/MOVI/JMP/MOVI/SET
    emit("ADDI", rd=3, imm=255)                # ADDI R3, -1
    jump("JNZ", "second", rd=3)              # JNZ R3, second
    label("idle")
    jump("JMP", "idle")                      # Keep TX driven; HALT releases it
    label("second")
    emit("MOVI", rd=0, imm=payload[1] if len(payload) == 2 else 0)
    jump("JMP", "frame")
    return program.assemble()


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
