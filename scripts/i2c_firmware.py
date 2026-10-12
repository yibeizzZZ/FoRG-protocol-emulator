"""Generate an open-drain I2C address-write probe for the unchanged M2.

This is NOT a data-transfer controller. The host must qualify both bus lines
HIGH continuously for >=4.7 us before RUN on a single-controller bus.
SDA=uio[0], SCL=uio[1]. Read R3 after HALT: 0=ACK, 1=NACK.
"""
import argparse
from pathlib import Path

if __package__:
    from .pio_firmware import Program
else:
    from importlib.util import module_from_spec, spec_from_file_location

    _spec = spec_from_file_location("pio_firmware", Path(__file__).with_name("pio_firmware.py"))
    _builder = module_from_spec(_spec)
    _spec.loader.exec_module(_builder)
    Program = _builder.Program

CLOCK_HZ = 50_000_000
SDA = 1
SCL = 2


def generate_i2c_probe(address, *, half_period_cycles=250):
    """START, address+W, sampled ACK/NACK, STOP; 32 words with stretch polling.

    Address is compile-time data; all nine clocks share one bit loop. P=250..267
    is a timing budget, not an exact half-period: ideal SCL low=P+3/P+4,
    high=P/P+1. The maximum nominal rate at P=250 is 50 MHz / 503.
    Reserved address encodings are emitted literally, without special handling.
    See docs/i2c-firmware.md for mandatory bus/host preconditions and scope.
    """
    if type(address) is not int or not 0 <= address <= 127:
        raise ValueError("address must be a seven-bit integer in 0..127")
    if type(half_period_cycles) is not int or not 250 <= half_period_cycles <= 267:
        raise ValueError("half-period budget must be an integer in 250..267 cycles at 50 MHz")
    p = Program()
    emit, label, branch, wait = p.emit, p.label, p.branch, p.wait
    # STOPPED mode zeros pins_out and registers. Never drive either pin HIGH.
    emit("MOVI", rd=0, imm=~(address << 1) & 255)
    emit("MOVI", rd=2, imm=10)              # Nine clocks, then STOP preparation
    emit("DIR", imm=SDA)                   # START on the host-qualified idle bus
    wait(215)                               # Includes allowance for SDA fall time
    label("fall")
    branch("JNZ", "fall_released", rd=3)  # Preserve the previous observed SDA
    emit("DIR", imm=SDA | SCL)
    branch("JMP", "hold")
    label("fall_released")
    emit("DIR", imm=SCL)
    label("hold")
    wait(16)                                # SDA unchanged while SCL falls
    emit("ADDI", rd=2, imm=255)
    branch("JNZ", "bit", rd=2)
    label("drive_zero")                     # Also prepares SDA LOW for STOP
    emit("DIR", imm=SDA | SCL)
    wait(half_period_cycles - 20)
    emit("DIR", imm=SDA)                   # Release SCL; do NOT assume it is HIGH
    branch("JMP", "scl_high")
    label("bit")
    emit("MOV", rd=1, rs=0)
    emit("ANDI", rd=1, imm=128)
    branch("JNZ", "drive_zero", rd=1)
    emit("DIR", imm=SCL)                   # Release SDA, including ninth ACK bit
    wait(half_period_cycles - 20)
    emit("DIR", imm=0)
    label("scl_high")
    emit("READ", rd=1)
    emit("ANDI", rd=1, imm=SCL)
    emit("ADDI", rd=1, imm=256 - SCL)
    branch("JNZ", "scl_high", rd=1)       # Unbounded clock stretching / stuck LOW
    wait(half_period_cycles - 11)
    branch("JNZ", "sample", rd=2)
    emit("HALT")                           # Releases SDA only after actual SCL HIGH
    label("sample")
    emit("READ", rd=3)
    emit("ANDI", rd=3, imm=SDA)
    emit("SHL", rd=0)                      # Eight shifts leave zero: release ACK
    branch("JMP", "fall")
    return p.assemble()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", type=lambda value: int(value, 0))
    parser.add_argument("--half-period", type=int, default=250,
                        help="timing budget in system cycles, 250..267 (default: 250)")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        words = generate_i2c_probe(args.address, half_period_cycles=args.half_period)
    except ValueError as error:
        parser.error(str(error))
    content = "".join(f"{word:04x}\n" for word in words)
    if args.output is None:
        print(content, end="")
    else:
        args.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
