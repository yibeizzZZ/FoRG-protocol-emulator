"""FoRG protocol emulator: cycle-accurate golden model, assembler and verification kit."""

from pathlib import Path

from .asm import AsmError, Program, assemble, assemble_file
from .chip import Chip, Device, PIN_NAMES, pin_index
from .sm import SimError

FIRMWARE_DIR = Path(__file__).resolve().parent.parent / "firmware"


def firmware(name, **defines) -> Program:
    """Assemble a bundled firmware program by name, e.g. firmware('uart_tx')."""
    return assemble_file(FIRMWARE_DIR / f"{name}.fasm", defines=defines)


__all__ = [
    "AsmError",
    "Chip",
    "Device",
    "FIRMWARE_DIR",
    "PIN_NAMES",
    "Program",
    "SimError",
    "assemble",
    "assemble_file",
    "firmware",
    "pin_index",
]
