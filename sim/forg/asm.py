"""Two-pass assembler for FoRG firmware (.fasm files).

Syntax overview (see sim/docs/ISA.md):

    .program uart_tx
    .side_set 1
    .define BIT 8
    .config clkdiv 13.0
    .config side_base 8
    .wrap_target
        pull block          side 1
        set x, 7            side 0 [BIT-1]
    bit:
        out pins, 1         side 0
        jmp x--, bit        side 0 [BIT-2]
    .wrap
    .assert_cycles bit bit 8
"""

import ast
import operator
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import isa
from .isa import IsaError


class AsmError(Exception):
    def __init__(self, msg, line_no=None, line=None, filename=None):
        self.msg = msg
        self.line_no = line_no
        self.line = line
        self.filename = filename
        loc = f"{filename or '<asm>'}:{line_no}: " if line_no else ""
        super().__init__(f"{loc}{msg}" + (f"\n    {line.strip()}" if line else ""))


@dataclass
class TimingAssert:
    start: str
    end: str
    min_cycles: int
    max_cycles: int
    line_no: int


@dataclass
class Program:
    name: str
    words: list
    side_count: int = 0
    wrap_target: int = 0
    wrap: int = None
    labels: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    asserts: list = field(default_factory=list)
    line_map: list = field(default_factory=list)
    source_lines: list = field(default_factory=list)
    filename: str = None

    def __post_init__(self):
        if self.wrap is None:
            self.wrap = len(self.words) - 1

    def __len__(self):
        return len(self.words)

    def relocated(self, origin: int) -> list:
        """Instruction words with jump targets offset by `origin`."""
        out = []
        for w in self.words:
            ins = isa.decode(w)
            if ins.op == isa.OP_JMP:
                addr = (ins.arg & 0x3F) + origin
                if addr >= isa.IMEM_SIZE:
                    raise AsmError(f"program {self.name} does not fit at origin {origin}")
                w = isa.encode(ins.op, ins.ds, (ins.arg & 0x1C0) | addr)
            out.append(w)
        return out

    def disassemble(self) -> str:
        rows = []
        inv = {}
        for name, addr in self.labels.items():
            inv.setdefault(addr, []).append(name)
        for i, w in enumerate(self.words):
            for name in inv.get(i, []):
                rows.append(f"{name}:")
            marks = ""
            if i == self.wrap_target:
                marks += " <wrap_target>"
            if i == self.wrap:
                marks += " <wrap>"
            rows.append(f"  {i:2d}: {w:04x}  {isa.disasm(w, self.side_count)}{marks}")
        return "\n".join(rows)


_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
}


def eval_expr(text: str, names: dict):
    text = text.strip()
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise AsmError(f"bad expression: {text!r}") from e

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in names:
                return names[node.id]
            raise AsmError(f"unknown symbol: {node.id}")
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            return _BINOPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
            return ~ev(node.operand)
        raise AsmError(f"unsupported expression: {text!r}")

    return ev(tree)


def eval_int(text, names):
    v = eval_expr(text, names)
    if isinstance(v, float):
        if not v.is_integer():
            raise AsmError(f"expected integer, got {v}")
        v = int(v)
    return v


_TRAILER_RE = re.compile(r"^(.*?)(?:\s+side\s+(\S+))?(?:\s*\[([^\]]+)\])?\s*$")
_LABEL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")

# Config keys whose value is a label name resolved to an address.
_LABEL_CONFIG_KEYS = {"trap"}
_STRING_CONFIG_KEYS = {"in_shift", "out_shift", "line_stuff_mode", "crc_src"}


def _strip_comment(line):
    for marker in (";", "//"):
        idx = line.find(marker)
        if idx >= 0:
            line = line[:idx]
    return line.rstrip()


def assemble(source: str, filename: str = None, defines: dict = None) -> Program:
    names = dict(defines or {})
    lines = source.splitlines()
    name = Path(filename).stem if filename else "program"
    side_count = 0
    wrap_target = None
    wrap = None
    trap_label = None
    labels = {}
    config = {}
    raw_config = []
    asserts = []
    pending = []  # (line_no, line, text)

    # Pass 1: directives, labels, instruction text.
    for no, line in enumerate(lines, 1):
        text = _strip_comment(line).strip()
        if not text:
            continue
        try:
            while True:
                m = _LABEL_RE.match(text)
                if not m or text.startswith("."):
                    break
                lbl, text = m.group(1), m.group(2).strip()
                if lbl in labels:
                    raise AsmError(f"duplicate label {lbl}")
                labels[lbl] = len(pending)
            if not text:
                continue
            if text.startswith("."):
                parts = text.split(None, 1)
                d = parts[0].lower()
                rest = parts[1] if len(parts) > 1 else ""
                if d == ".program":
                    name = rest.strip()
                elif d == ".side_set":
                    side_count = eval_int(rest, names)
                    if not 0 <= side_count <= 2:
                        raise AsmError(".side_set must be 0..2")
                elif d == ".define":
                    k, v = rest.split(None, 1)
                    if k not in (defines or {}):
                        names[k] = eval_expr(v, names)
                elif d == ".wrap_target":
                    wrap_target = len(pending)
                elif d == ".wrap":
                    wrap = len(pending) - 1
                elif d == ".trap":
                    trap_label = ("__trap", len(pending))
                elif d == ".config":
                    k, v = rest.split(None, 1)
                    raw_config.append((no, line, k, v.strip()))
                elif d == ".assert_cycles":
                    toks = rest.split()
                    if len(toks) not in (3, 4):
                        raise AsmError(".assert_cycles <from> <to> <n> | <min> <max>")
                    lo = eval_int(toks[2], names)
                    hi = eval_int(toks[3], names) if len(toks) == 4 else lo
                    asserts.append(TimingAssert(toks[0], toks[1], lo, hi, no))
                else:
                    raise AsmError(f"unknown directive {d}")
                continue
            pending.append((no, line, text))
        except AsmError as e:
            raise AsmError(e.msg, no, line, filename) from None

    if trap_label:
        labels.setdefault("__trap", trap_label[1])
    names_with_labels = dict(names)
    names_with_labels.update(labels)

    for no, line, k, v in raw_config:
        try:
            if k in _LABEL_CONFIG_KEYS:
                if v not in labels:
                    raise AsmError(f"unknown label {v}")
                config[k] = labels[v]
            elif k in _STRING_CONFIG_KEYS:
                config[k] = v
            else:
                config[k] = eval_expr(v, names_with_labels)
        except AsmError as e:
            raise AsmError(e.msg, no, line, filename) from None
    if trap_label and "trap" not in config:
        config["trap"] = trap_label[1]

    # Pass 2: encode.
    words = []
    line_map = []
    for no, line, text in pending:
        try:
            words.append(_encode_line(text, names_with_labels, labels, side_count))
        except (AsmError, IsaError) as e:
            raise AsmError(getattr(e, "msg", str(e)), no, line, filename) from None
        line_map.append(no)

    if not words:
        raise AsmError("program has no instructions", filename=filename)
    if len(words) > isa.IMEM_SIZE:
        raise AsmError(f"program is {len(words)} instructions, limit is {isa.IMEM_SIZE}", filename=filename)
    for a in asserts:
        for lbl in [a.start] + a.end.split("|"):
            if lbl not in labels:
                raise AsmError(f"unknown label {lbl} in .assert_cycles", a.line_no, None, filename)

    return Program(
        name=name,
        words=words,
        side_count=side_count,
        wrap_target=wrap_target or 0,
        wrap=wrap if wrap is not None else len(words) - 1,
        labels=labels,
        config=config,
        asserts=asserts,
        line_map=line_map,
        source_lines=lines,
        filename=filename,
    )


def assemble_file(path, defines=None) -> Program:
    path = Path(path)
    return assemble(path.read_text(), filename=str(path), defines=defines)


def _split_args(text):
    return [t.strip() for t in text.split(",")] if text.strip() else []


def _encode_line(text, names, labels, side_count):
    m = _TRAILER_RE.match(text)
    body, side_txt, delay_txt = m.group(1).strip(), m.group(2), m.group(3)
    side = eval_int(side_txt, names) if side_txt is not None else None
    delay = eval_int(delay_txt, names) if delay_txt is not None else 0
    if side_count and side is None:
        side = None  # reported by join_ds
    ds = isa.join_ds(side, delay, side_count)

    parts = body.split(None, 1)
    mnem = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    def num(t):
        return eval_int(t, names)

    def count_field(t):
        n = num(t)
        if not 1 <= n <= 32:
            raise AsmError("bit count must be 1..32")
        return n & 0x1F

    if mnem == "nop":
        return isa.encode(isa.OP_MOV, ds, (2 << 6) | 2)

    if mnem == "jmp":
        args = _split_args(rest)
        if len(args) == 1:
            cond, target = "", args[0]
        elif len(args) == 2:
            cond, target = args[0].replace(" ", "").lower(), args[1]
        else:
            raise AsmError("jmp [cond,] target")
        if cond not in isa.JMP_CONDS:
            raise AsmError(f"unknown jmp condition {cond}")
        addr = labels[target] if target in labels else num(target)
        if not 0 <= addr < isa.IMEM_SIZE:
            raise AsmError("jump target out of range")
        return isa.encode(isa.OP_JMP, ds, (isa.JMP_CONDS.index(cond) << 6) | addr)

    if mnem == "wait":
        toks = rest.replace(",", " ").split()
        resync = 0
        if toks and toks[-1].lower() == "resync":
            resync = 1
            toks = toks[:-1]
        if len(toks) != 3:
            raise AsmError("wait <pol> <gpio|pin|irq|edge> <index> [resync]")
        pol, src, idx = num(toks[0]), toks[1].lower(), num(toks[2])
        if pol not in (0, 1):
            raise AsmError("wait polarity must be 0 or 1")
        if src not in isa.WAIT_SRCS:
            raise AsmError(f"unknown wait source {src}")
        limit = isa.NUM_IRQ if src == "irq" else isa.NUM_PINS
        if not 0 <= idx < limit:
            raise AsmError("wait index out of range")
        return isa.encode(isa.OP_WAIT, ds, (pol << 8) | (isa.WAIT_SRCS.index(src) << 6) | (resync << 5) | idx)

    if mnem in ("in", "out"):
        args = _split_args(rest)
        if len(args) != 2:
            raise AsmError(f"{mnem} <src/dst>, <count>")
        table = isa.IN_SRCS if mnem == "in" else isa.OUT_DSTS
        reg = args[0].lower()
        if reg not in table:
            raise AsmError(f"bad {mnem} operand {reg}")
        op = isa.OP_IN if mnem == "in" else isa.OP_OUT
        return isa.encode(op, ds, (table.index(reg) << 6) | count_field(args[1]))

    if mnem in ("push", "pull"):
        toks = rest.lower().split()
        cond = 0
        block = 1
        for t in toks:
            if t in ("iffull", "ifempty"):
                if (t == "iffull") != (mnem == "push"):
                    raise AsmError(f"{t} not valid for {mnem}")
                cond = 1
            elif t == "block":
                block = 1
            elif t == "noblock":
                block = 0
            else:
                raise AsmError(f"unknown {mnem} option {t}")
        sub = isa.CTL_PUSH if mnem == "push" else isa.CTL_PULL
        return isa.encode(isa.OP_CTL, ds, (sub << 7) | (cond << 6) | (block << 5))

    if mnem == "ctl":
        flags = 0
        lookup = {n: b for b, n in isa.LINE_FLAG_NAMES}
        for t in rest.lower().split():
            if t not in lookup:
                raise AsmError(f"unknown ctl flag {t}")
            flags |= lookup[t]
        return isa.encode(isa.OP_CTL, ds, (isa.CTL_LINE << 7) | flags)

    if mnem == "irq":
        toks = rest.lower().split()
        mode = "set"
        if len(toks) == 2:
            mode, idx_t = toks
        elif len(toks) == 1:
            idx_t = toks[0]
        else:
            raise AsmError("irq [set|wait|clear] <n>")
        idx = num(idx_t)
        if not 0 <= idx < isa.NUM_IRQ:
            raise AsmError("irq index out of range")
        bits = {"set": 0, "wait": 0x20, "clear": 0x40}
        if mode not in bits:
            raise AsmError(f"unknown irq mode {mode}")
        return isa.encode(isa.OP_CTL, ds, (isa.CTL_IRQ << 7) | bits[mode] | idx)

    if mnem == "mov":
        args = _split_args(rest)
        if len(args) != 2:
            raise AsmError("mov <dst>, [~|::]<src>")
        dst = args[0].lower()
        src = args[1].lower().replace(" ", "")
        op = 0
        if src.startswith("::"):
            op, src = 2, src[2:]
        elif src.startswith("~") or src.startswith("!"):
            op, src = 1, src[1:]
        if dst not in isa.MOV_DSTS:
            raise AsmError(f"bad mov destination {dst}")
        if src not in isa.MOV_SRCS:
            raise AsmError(f"bad mov source {src}")
        return isa.encode(isa.OP_MOV, ds, (isa.MOV_DSTS.index(dst) << 6) | (op << 4) | isa.MOV_SRCS.index(src))

    if mnem == "cap":
        n = num(rest)
        if not 1 <= n <= 8:
            raise AsmError("cap pin count must be 1..8")
        return isa.encode(isa.OP_CAP, ds, n - 1)

    if mnem == "add":
        args = _split_args(rest)
        if len(args) != 2 or args[0].lower() not in ("x", "y"):
            raise AsmError("add <x|y>, <imm>")
        imm = num(args[1])
        if not -64 <= imm <= 63:
            raise AsmError("add immediate must be -64..63")
        reg = 1 if args[0].lower() == "y" else 0
        return isa.encode(isa.OP_CAP, ds, 0x100 | (reg << 7) | (imm & 0x7F))

    if mnem == "set":
        args = _split_args(rest)
        if len(args) != 2:
            raise AsmError("set <dst>, <imm>")
        dst = args[0].lower()
        if dst not in isa.SET_DSTS:
            raise AsmError(f"bad set destination {dst}")
        imm = num(args[1])
        if not 0 <= imm <= 31:
            raise AsmError("set immediate must be 0..31")
        return isa.encode(isa.OP_SET, ds, (isa.SET_DSTS.index(dst) << 6) | imm)

    raise AsmError(f"unknown instruction {mnem}")
