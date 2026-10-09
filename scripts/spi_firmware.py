"""Generate loop-based, full-duplex Mode-0 SPI firmware for the unchanged M2.

Pins: MOSI=uio[7], MISO=uio[0], SCLK=uio[1], active-low CS=uio[2].
R0 shifts TX out and RX in; R3 preserves the first RX byte of a two-byte transfer.
"""

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
MOSI = 0x80
MISO = 0x01
SCLK = 0x02
CS = 0x04


def generate_spi_master(payload, *, half_period_cycles=16):
    """Generate one/two MSB-first bytes with equal high/low SCLK phases.

    Payload is compile-time data, half_period_cycles is configuration, and
    all bits execute the same runtime loop. H=13..269 fits in 32 words.
    Both bytes share one CS assertion and have no extra inter-byte clocks/gap.
    MISO is sampled one system cycle after each rising SCLK edge.
    """
    payload = list(payload)
    if not 1 <= len(payload) <= 2:
        raise ValueError("payload must contain one or two bytes")
    if any(type(byte) is not int or not 0 <= byte <= 255 for byte in payload):
        raise ValueError("each payload value must be an integer in 0..255")
    if type(half_period_cycles) is not int or half_period_cycles < 13:
        raise ValueError("half-period must be an integer of at least 13 system cycles")

    program = Program()
    emit, label, branch, wait = program.emit, program.label, program.branch, program.wait
    emit("SET", imm=CS)                        # Idle SCLK/MOSI=0, CS=1 before DIR
    emit("DIR", imm=MOSI | SCLK | CS)          # MISO and unused pins remain inputs
    emit("MOVI", rd=0, imm=payload[0])         # TX/RX shift register
    emit("MOVI", rd=2, imm=8 * len(payload))   # Total remaining bits
    emit("SET", imm=0)                         # Assert CS with SCLK low
    wait(4)                                    # Match initial low phase to the loop
    label("next_bit")
    wait(4)                                    # Balance normal vs byte-boundary path
    label("output_bit")
    emit("MOV", rd=1, rs=0)
    emit("ANDI", rd=1, imm=MOSI)              # Directly select current TX MSB
    emit("OUT", rd=1)
    wait(half_period_cycles - 13)
    emit("ADDI", rd=1, imm=SCLK)
    emit("OUT", rd=1)                         # Rising SCLK, MOSI unchanged
    emit("READ", rd=1)                        # Sample while slave holds MISO high-phase
    emit("ANDI", rd=1, imm=MISO)
    emit("SHL", rd=0)                         # Vacate low bit as TX advances
    branch("JNZ", "received_one", rd=1)
    branch("JMP", "sampled")                 # Zero path costs same as ADDI below
    label("received_one")
    emit("ADDI", rd=0, imm=1)
    label("sampled")
    wait(half_period_cycles - 6)
    emit("SET", imm=0)                         # Falling SCLK; MOSI may change here
    emit("ADDI", rd=2, imm=255)
    emit("MOV", rd=1, rs=2)
    emit("ANDI", rd=1, imm=7)
    branch("JNZ", "next_bit", rd=1)          # Non-boundary bits
    branch("JNZ", "second_byte", rd=2)       # Exactly eight bits remain
    emit("SET", imm=CS)                        # End transaction; hold idle driven
    label("idle")
    branch("JMP", "idle")
    label("second_byte")
    emit("MOV", rd=3, rs=0)                   # Preserve first RX byte through completion
    emit("MOVI", rd=0, imm=payload[1] if len(payload) == 2 else 0)
    branch("JMP", "output_bit")              # Skip normal-bit balancing delay
    return program.assemble()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", nargs="+", type=lambda value: int(value, 0),
                        help="one or two TX bytes (decimal or 0x hexadecimal)")
    parser.add_argument("--half-period", type=int, default=16,
                        help="system cycles per SCLK half-period, 13..269 (default: 16)")
    parser.add_argument("--output", type=Path, help="hex output path (default: stdout)")
    args = parser.parse_args()
    try:
        words = generate_spi_master(args.payload, half_period_cycles=args.half_period)
    except ValueError as error:
        parser.error(str(error))
    content = "".join(f"{word:04x}\n" for word in words)
    if args.output is None:
        print(content, end="")
    else:
        args.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
