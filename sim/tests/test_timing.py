import pytest

from forg import FIRMWARE_DIR, assemble, assemble_file
from forg.timing import analyze_path, check_asserts

PROGRAMS = sorted(FIRMWARE_DIR.glob("*.fasm"))


@pytest.mark.parametrize("path", PROGRAMS, ids=lambda p: p.stem)
def test_firmware_timing_proofs(path):
    prog = assemble_file(path)
    for r in check_asserts(prog):
        assert r.ok, r.message


def test_analyzer_catches_unbalanced_branch():
    # The '1' path is one tick longer than the '0' path: a classic bit-banging bug.
    prog = assemble("""
    bit:
        out x, 1
        jmp !x, zero
        set pins, 1 [5]
        jmp bit
    zero:
        set pins, 0 [5]
        jmp bit
    .assert_cycles bit bit 8
    """)
    (r,) = check_asserts(prog)
    assert not r.ok
    assert r.result.costs == {9, 9} or r.result.costs == {9}


def test_analyzer_reports_both_branch_costs():
    prog = assemble("""
    a:
        jmp pin, slow
        nop
        jmp b
    slow:
        nop [3]
    b:
        nop
    """)
    r = analyze_path(prog, "a", "b")
    assert r.costs == {3, 5}


def test_analyzer_flags_unbounded_loop():
    prog = assemble("""
    a:
        jmp x--, a
    b:
        nop
    """)
    r = analyze_path(prog, "a", "b")
    assert r.unbounded
