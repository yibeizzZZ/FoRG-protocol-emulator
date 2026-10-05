"""FoRG instruction set: encoding, decoding and disassembly.

Instruction word (16 bits):

    [15:13] OP     opcode
    [12:9]  DS     delay / side-set field (top `side_count` bits are side-set)
    [8:0]   ARG    opcode specific

See sim/docs/ISA.md for the full specification.
"""

from dataclasses import dataclass

IMEM_SIZE = 64
ADDR_BITS = 6
DS_BITS = 4
NUM_PINS = 24
NUM_IRQ = 8
WORD_MASK = 0xFFFFFFFF

OP_JMP = 0
OP_WAIT = 1
OP_IN = 2
OP_OUT = 3
OP_CTL = 4
OP_MOV = 5
OP_CAP = 6  # also ADD
OP_SET = 7

OP_NAMES = ["jmp", "wait", "in", "out", "ctl", "mov", "cap", "set"]

JMP_CONDS = ["", "!x", "x--", "!y", "y--", "x!=y", "pin", "!osre"]

WAIT_SRCS = ["gpio", "pin", "irq", "edge"]

IN_SRCS = ["pins", "x", "y", "null", None, "isr", "osr", "line"]
OUT_DSTS = ["pins", "x", "y", "null", "pindirs", "pc", "isr", "line"]

MOV_DSTS = ["pins", "x", "y", "pindirs", "pc", "isr", "osr", "crc"]
MOV_SRCS = ["pins", "x", "y", "null", "status", "isr", "osr", "crc"]
MOV_OPS = ["", "~", "::", None]

SET_DSTS = ["pins", "x", "y", "pindirs"]

CTL_PUSH = 0
CTL_PULL = 1
CTL_LINE = 2
CTL_IRQ = 3

# CTL LINE flag bits
LINE_CRC_RESET = 1 << 0
LINE_TX_RESET = 1 << 1
LINE_RX_ARM = 1 << 2
LINE_DRAIN = 1 << 3
LINE_RX_STOP = 1 << 4
LINE_FLUSH = 1 << 5  # empty OSR and clear ISR
LINE_FLAG_NAMES = [
    (LINE_CRC_RESET, "crc_reset"),
    (LINE_TX_RESET, "tx_reset"),
    (LINE_RX_ARM, "rx_arm"),
    (LINE_DRAIN, "drain"),
    (LINE_RX_STOP, "rx_stop"),
    (LINE_FLUSH, "flush"),
]

# STATUS register bits (MOV src STATUS)
ST_TXF_EMPTY = 1 << 0
ST_RXF_FULL = 1 << 1
ST_TIMEOUT = 1 << 2
ST_RX_EOP = 1 << 3
ST_RX_STUFF_ERR = 1 << 4
ST_RX_OVF = 1 << 5
ST_RXF_OVF = 1 << 6
ST_RX_ACTIVE = 1 << 7
ST_MARKER = 1 << 15  # always set, lets host tell status words from data


class IsaError(Exception):
    pass


@dataclass(frozen=True)
class Instr:
    op: int
    ds: int
    arg: int

    @property
    def word(self) -> int:
        return encode(self.op, self.ds, self.arg)


def encode(op: int, ds: int, arg: int) -> int:
    if not 0 <= op < 8:
        raise IsaError(f"opcode out of range: {op}")
    if not 0 <= ds < (1 << DS_BITS):
        raise IsaError(f"delay/side field out of range: {ds}")
    if not 0 <= arg < 512:
        raise IsaError(f"argument out of range: {arg}")
    return (op << 13) | (ds << 9) | arg


def decode(word: int) -> Instr:
    if not 0 <= word <= 0xFFFF:
        raise IsaError(f"instruction word out of range: {word:#x}")
    return Instr(word >> 13, (word >> 9) & 0xF, word & 0x1FF)


def split_ds(ds: int, side_count: int):
    """Returns (side_value or None, delay)."""
    delay_bits = DS_BITS - side_count
    delay = ds & ((1 << delay_bits) - 1)
    if side_count == 0:
        return None, delay
    return ds >> delay_bits, delay


def join_ds(side, delay: int, side_count: int) -> int:
    delay_bits = DS_BITS - side_count
    if not 0 <= delay < (1 << delay_bits):
        raise IsaError(f"delay {delay} does not fit in {delay_bits} bits (side_count={side_count})")
    if side_count == 0:
        if side is not None:
            raise IsaError("side-set used but .side_set is 0")
        return delay
    if side is None:
        raise IsaError("side-set value required on every instruction when .side_set > 0")
    if not 0 <= side < (1 << side_count):
        raise IsaError(f"side-set value {side} does not fit in {side_count} bits")
    return (side << delay_bits) | delay


def bit_count(n: int) -> int:
    return 32 if n == 0 else n


def sext(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value & (sign - 1)) - (value & sign)


def may_stall(ins: Instr) -> bool:
    """Whether an instruction can block, independent of configuration."""
    if ins.op == OP_WAIT:
        return True
    if ins.op == OP_CTL:
        sub = ins.arg >> 7
        if sub in (CTL_PUSH, CTL_PULL):
            return bool(ins.arg & (1 << 5))
        if sub == CTL_LINE:
            return bool(ins.arg & LINE_DRAIN)
        if sub == CTL_IRQ:
            return bool(ins.arg & (1 << 5))
    if ins.op == OP_IN:
        return True  # autopush or LINE source can block
    if ins.op == OP_OUT:
        return True  # autopull or LINE destination can block
    return False


def disasm(word: int, side_count: int = 0) -> str:
    ins = decode(word)
    a = ins.arg
    if ins.op == OP_JMP:
        cond = JMP_CONDS[a >> 6]
        text = f"jmp {cond + ', ' if cond else ''}{a & 0x3F}"
    elif ins.op == OP_WAIT:
        pol = a >> 8
        src = WAIT_SRCS[(a >> 6) & 3]
        resync = " resync" if a & (1 << 5) else ""
        text = f"wait {pol} {src} {a & 0x1F}{resync}"
    elif ins.op == OP_IN:
        src = IN_SRCS[a >> 6] or "?"
        text = f"in {src}, {bit_count(a & 0x1F)}"
    elif ins.op == OP_OUT:
        text = f"out {OUT_DSTS[a >> 6]}, {bit_count(a & 0x1F)}"
    elif ins.op == OP_CTL:
        sub = a >> 7
        if sub == CTL_PUSH:
            text = "push" + (" iffull" if a & 0x40 else "") + (" block" if a & 0x20 else " noblock")
        elif sub == CTL_PULL:
            text = "pull" + (" ifempty" if a & 0x40 else "") + (" block" if a & 0x20 else " noblock")
        elif sub == CTL_LINE:
            flags = [n for b, n in LINE_FLAG_NAMES if a & b]
            text = " ".join(["ctl"] + flags)
        else:
            if a & 0x40:
                text = f"irq clear {a & 7}"
            else:
                text = f"irq {'wait' if a & 0x20 else 'set'} {a & 7}"
    elif ins.op == OP_MOV:
        dst = MOV_DSTS[a >> 6]
        op = MOV_OPS[(a >> 4) & 3]
        op = "?" if op is None else op
        text = f"mov {dst}, {op}{MOV_SRCS[a & 7]}"
    elif ins.op == OP_CAP:
        if a & 0x100:
            reg = "y" if a & 0x80 else "x"
            text = f"add {reg}, {sext(a & 0x7F, 7)}"
        else:
            text = f"cap {(a & 7) + 1}"
    else:
        text = f"set {SET_DSTS[(a >> 6) & 3]}, {a & 0x1F}"
    side, delay = split_ds(ins.ds, side_count)
    if side is not None:
        text += f" side {side}"
    if delay:
        text += f" [{delay}]"
    return text
