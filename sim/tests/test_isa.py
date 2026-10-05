import random

import pytest

from forg import assemble, isa
from forg.asm import AsmError


def legal(word):
    ins = isa.decode(word)
    a = ins.arg
    if ins.op == isa.OP_IN and isa.IN_SRCS[a >> 6] is None:
        return False
    if ins.op == isa.OP_MOV and ((a >> 4) & 3) == 3:
        return False
    if ins.op == isa.OP_SET and (a >> 6) >= 4:
        return False
    if ins.op == isa.OP_WAIT and ((a >> 6) & 3) == 2 and (a & 0x1F) >= isa.NUM_IRQ:
        return False
    if ins.op == isa.OP_WAIT and (a & 0x1F) >= isa.NUM_PINS:
        return False
    return True


@pytest.mark.parametrize("side_count", [0, 1, 2])
def test_disasm_assemble_roundtrip(side_count):
    """Disassembly must re-assemble to an instruction with identical meaning."""
    r = random.Random(side_count)
    checked = 0
    while checked < 3000:
        w = r.randrange(1 << 16)
        if not legal(w):
            continue
        text = isa.disasm(w, side_count)
        src = f".side_set {side_count}\n{text}\n"
        if side_count and " side " not in text:
            continue
        w2 = assemble(src).words[0]
        assert isa.disasm(w2, side_count) == text, (hex(w), text)
        checked += 1


def test_encode_decode():
    for op in range(8):
        for ds in (0, 5, 15):
            for arg in (0, 1, 0x155, 0x1FF):
                assert isa.decode(isa.encode(op, ds, arg)) == isa.Instr(op, ds, arg)


def test_side_set_and_delay_fields():
    p = assemble(".side_set 2\nnop side 3 [3]\n")
    side, delay = isa.split_ds(isa.decode(p.words[0]).ds, 2)
    assert (side, delay) == (3, 3)
    with pytest.raises(AsmError):
        assemble(".side_set 2\nnop side 1 [4]\n")
    with pytest.raises(AsmError):
        assemble(".side_set 1\nnop\n")


@pytest.mark.parametrize("bad", [
    "jmp nowhere",
    "set x, 32",
    "out pins, 33",
    "wait 2 pin 0",
    "irq set 9",
    "add x, 64",
    "frobnicate",
    "mov pc, ::bogus",
])
def test_assembler_rejects(bad):
    with pytest.raises(AsmError):
        assemble(bad + "\n")


def test_labels_defines_and_expressions():
    p = assemble("""
        .define N 3
        .config clkdiv 48e6 / (8 * 115200)
    top:
        set x, N * 2 + 1
        jmp x--, top [N]
    """)
    assert isa.disasm(p.words[0]) == "set x, 7"
    assert isa.disasm(p.words[1]) == "jmp x--, 0 [3]"
    assert abs(p.config["clkdiv"] - 52.083) < 0.01


def test_relocation():
    p = assemble("a:\n nop\n jmp a\n")
    reloc = p.relocated(10)
    assert isa.disasm(reloc[1]) == "jmp 10"
