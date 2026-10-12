"""Generate loop-based, full-duplex SPI Modes 0..3 for the unchanged M2 ISA.

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
HALF_PERIOD_RANGES = ((13, 269), (16, 272), (14, 262), (15, 515))


def generate_spi_master(payload, *, half_period_cycles=16, mode=0):
    """Generate one/two MSB-first bytes with equal high/low SCLK phases.

    Payload is compile-time data, half_period_cycles is configuration, and
    all bits execute the same runtime loop. Mode 0 output is unchanged.
    Half-period bounds per mode: 0=13..269, 1=16..272, 2=14..262, 3=15..515.
    Both bytes share one CS assertion and have no extra inter-byte clocks/gap.
    READ captures MISO one system cycle after the mode's sampling edge.
    """
    payload = list(payload)
    if not 1 <= len(payload) <= 2:
        raise ValueError("payload must contain one or two bytes")
    if any(type(byte) is not int or not 0 <= byte <= 255 for byte in payload):
        raise ValueError("each payload value must be an integer in 0..255")
    if type(mode) is not int or not 0 <= mode <= 3:
        raise ValueError("mode must be an integer in 0..3")
    minimum, maximum = HALF_PERIOD_RANGES[mode]
    if type(half_period_cycles) is not int or not minimum <= half_period_cycles <= maximum:
        raise ValueError(f"mode {mode} half-period must be an integer in {minimum}..{maximum} system cycles")
    if mode & 1:
        return _generate_phase_one(payload, half_period_cycles, mode)

    idle_clock = SCLK if mode == 2 else 0
    program = Program()
    emit, label, branch, wait = program.emit, program.label, program.branch, program.wait
    emit("SET", imm=CS | idle_clock)           # Establish CPOL before enabling outputs
    emit("DIR", imm=MOSI | SCLK | CS)          # MISO and unused pins remain inputs
    emit("MOVI", rd=0, imm=payload[0])         # TX/RX shift register
    emit("MOVI", rd=2, imm=8 * len(payload))   # Total remaining bits
    emit("SET", imm=idle_clock)                # Assert CS with SCLK at CPOL
    wait(4)                                    # Match initial low phase to the loop
    label("next_bit")
    wait(4)                                    # Balance normal vs byte-boundary path
    label("output_bit")
    emit("MOV", rd=1, rs=0)
    emit("ANDI", rd=1, imm=MOSI)              # Directly select current TX MSB
    if idle_clock:
        emit("ADDI", rd=1, imm=SCLK)
    emit("OUT", rd=1)
    wait(half_period_cycles - minimum)
    emit("ADDI", rd=1, imm=256 - SCLK if idle_clock else SCLK)
    emit("OUT", rd=1)                         # Leading/sample edge, MOSI unchanged
    emit("READ", rd=1)                        # Slave holds MISO through active phase
    emit("ANDI", rd=1, imm=MISO)
    emit("SHL", rd=0)                         # Vacate low bit as TX advances
    branch("JNZ", "received_one", rd=1)
    branch("JMP", "sampled")                 # Zero path costs same as ADDI below
    label("received_one")
    emit("ADDI", rd=0, imm=1)
    label("sampled")
    wait(half_period_cycles - 6)
    emit("SET", imm=idle_clock)                # Trailing/setup edge; MOSI may change
    emit("ADDI", rd=2, imm=255)
    emit("MOV", rd=1, rs=2)
    emit("ANDI", rd=1, imm=7)
    branch("JNZ", "next_bit", rd=1)          # Non-boundary bits
    branch("JNZ", "second_byte", rd=2)       # Exactly eight bits remain
    emit("SET", imm=CS | idle_clock)           # End transaction; hold idle driven
    label("idle")
    branch("JMP", "idle")
    label("second_byte")
    emit("MOV", rd=3, rs=0)                   # Preserve first RX byte through completion
    emit("MOVI", rd=0, imm=payload[1] if len(payload) == 2 else 0)
    branch("JMP", "output_bit")              # Skip normal-bit balancing delay
    return program.assemble()


def _generate_phase_one(payload, half_period, mode):
    """Launch on leading edges, sample after trailing edges (CPHA=1).

    R1 retains the MOSI pattern through both edges. Shift R0 during the
    active phase so receive/dispatch work fits the next idle half-period.
    Ordinary and second-byte paths are balanced, independent of data.
    """
    idle_clock = SCLK if mode == 3 else 0
    active_clock = SCLK ^ idle_clock
    minimum = HALF_PERIOD_RANGES[mode][0]
    program = Program()
    emit, label, branch, wait = program.emit, program.label, program.branch, program.wait
    emit("SET", imm=CS | idle_clock)
    emit("DIR", imm=MOSI | SCLK | CS)
    emit("MOVI", rd=0, imm=payload[0])
    emit("MOVI", rd=2, imm=8 * len(payload))
    emit("SET", imm=idle_clock)
    wait(8)                                   # Replace receive(4) + boundary work(4)
    label("next_bit")
    wait(4)                                   # Replace second-byte dispatch path
    label("output_bit")
    emit("MOV", rd=1, rs=0)
    emit("ANDI", rd=1, imm=MOSI)
    if active_clock:
        emit("ADDI", rd=1, imm=SCLK)
    wait(half_period - minimum)
    emit("OUT", rd=1)                         # Leading edge launches MOSI
    emit("SHL", rd=0)                         # Shift only after old MSB is on MOSI
    wait(half_period - 3)
    emit("ADDI", rd=1, imm=SCLK if idle_clock else 256 - SCLK)
    emit("OUT", rd=1)                         # Trailing edge, MOSI unchanged
    emit("READ", rd=1)                        # One system cycle after sample edge
    emit("ANDI", rd=1, imm=MISO)
    branch("JNZ", "received_one", rd=1)
    branch("JMP", "sampled")
    label("received_one")
    emit("ADDI", rd=0, imm=1)
    label("sampled")
    emit("ADDI", rd=2, imm=255)
    emit("MOV", rd=1, rs=2)
    emit("ANDI", rd=1, imm=7)
    branch("JNZ", "next_bit", rd=1)
    branch("JNZ", "second_byte", rd=2)
    emit("SET", imm=CS | idle_clock)           # Ten cycles after final trailing edge
    label("idle")
    branch("JMP", "idle")
    label("second_byte")
    emit("MOV", rd=3, rs=0)
    emit("MOVI", rd=0, imm=payload[1] if len(payload) == 2 else 0)
    branch("JMP", "output_bit")
    return program.assemble()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", nargs="+", type=lambda value: int(value, 0),
                        help="one or two TX bytes (decimal or 0x hexadecimal)")
    parser.add_argument("--half-period", type=int, default=16,
                        help="system cycles per half-period (default: 16); modes 0/1/2/3: 13..269/16..272/14..262/15..515")
    parser.add_argument("--mode", type=int, choices=range(4), default=0,
                        help="SPI mode: 0=00, 1=01, 2=10, 3=11 (CPOL/CPHA; default: 0)")
    parser.add_argument("--output", type=Path, help="hex output path (default: stdout)")
    args = parser.parse_args()
    try:
        words = generate_spi_master(args.payload, half_period_cycles=args.half_period, mode=args.mode)
    except ValueError as error:
        parser.error(str(error))
    content = "".join(f"{word:04x}\n" for word in words)
    if args.output is None:
        print(content, end="")
    else:
        args.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
