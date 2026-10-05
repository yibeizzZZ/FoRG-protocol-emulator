"""Unit tests of instruction and pin semantics against the spec in sim/docs/ISA.md."""

import pytest

from forg import Chip, assemble, isa
from forg.chip import Device


def run_prog(src, cycles=50, sm=0, chip=None, **cfg):
    chip = chip or Chip(clock_hz=10e6)
    m = chip.load(assemble(src), sm, **cfg)
    edges = []
    prev = [chip.nets]
    chip.watchers.append(lambda c, n: (edges.append((c, n)) if n != prev[0] else None, prev.__setitem__(0, n)))
    chip.run(cycles)
    return chip, m, edges


def test_delay_and_wrap_timing():
    # set pins 1 [2] ; set pins 0 [4] -> period 8, high 3 cycles
    chip, m, edges = run_prog(
        ".wrap_target\n set pins, 1 [2]\n set pins, 0 [4]\n.wrap\n",
        cycles=40, set_base=8, set_count=1,
    )
    rises = [c for c, n in edges if n & 0x100]
    assert rises[1] - rises[0] == 8
    falls = [c for c, n in edges if not n & 0x100]
    assert falls[0] - rises[0] == 3


def test_side_set_applies_at_issue_even_when_stalled():
    chip, m, _ = run_prog(".side_set 1\n pull block side 1\n", cycles=5, side_base=9)
    assert chip.nets & (1 << 9)
    assert m.pc == m.origin
    assert m.stall_cycles >= 4


def test_clkdiv_fractional_average():
    chip, m, edges = run_prog(
        ".wrap_target\n set pins, 1\n set pins, 0\n.wrap\n", cycles=1000, set_base=8, set_count=1, clkdiv=2.5,
    )
    rises = [c for c, n in edges if n & 0x100]
    periods = [b - a for a, b in zip(rises, rises[1:])]
    assert set(periods) <= {4, 5, 6}
    assert abs(sum(periods) / len(periods) - 5.0) < 0.05


def test_jmp_conditions_and_add():
    src = """
        set x, 3
        set y, 3
        jmp x!=y, bad
        add x, -1
        jmp x!=y, ok
    bad:
        set pins, 1
    ok:
        mov y, ::x
        jmp ok
    """
    chip, m, _ = run_prog(src, cycles=30, set_base=8, set_count=1)
    assert not chip.nets & 0x100
    assert m.x == 2
    assert m.y == 0x40000000


def test_autopull_autopush_shift_directions():
    src = ".wrap_target\n out x, 8\n in x, 8\n.wrap\n"
    chip = Chip()
    m = chip.load(assemble(src), 0, autopull=1, pull_thresh=32, autopush=1, push_thresh=32,
                  out_shift="right", in_shift="left")
    chip.put(0, 0x11223344)
    chip.run(20)
    assert chip.get(0) == 0x44332211


def test_out_stalls_on_empty_fifo_and_jmp_osre():
    src = "loop:\n out pins, 1\n jmp !osre, loop\n set x, 1\n"
    chip = Chip()
    m = chip.load(assemble(src), 0, out_base=8, out_count=1, autopull=0, pull_thresh=4)
    m.osr, m.osr_count = 0b1010, 0
    chip.run(20)
    assert m.x == 1


def test_irq_synchronises_two_state_machines():
    a = assemble("irq wait 2\n set pins, 1\nhang:\n jmp hang\n")
    b = assemble("set x, 20\nd:\n jmp x--, d\n irq clear 2\nhang:\n jmp hang\n")
    chip = Chip()
    chip.load(a, 0, set_base=8, set_count=1)
    chip.load(b, 1)
    chip.run(15)
    assert not chip.nets & 0x100
    chip.run(40)
    assert chip.nets & 0x100


def test_input_synchroniser_latency():
    class Pulse(Device):
        def step(self, chip, cycle, nets):
            self.drive(16, 1 if cycle >= 10 else 0)

    chip = Chip()
    chip.attach(Pulse())
    m = chip.load(assemble("wait 1 gpio 16\nset pins, 1\nh:\n jmp h\n"), 0, set_base=8, set_count=1)
    seen = []
    chip.watchers.append(lambda c, n: seen.append(c) if n & 0x100 and not seen else None)
    chip.run(30)
    # input net rises at 11; 2 sync stages, WAIT completes, SET issues, output register
    assert seen[0] - 11 == 4


def test_resync_wait_aligns_divider_phase():
    """With resync, the first tick after an edge lands exactly clkdiv cycles later."""
    class Edge(Device):
        def __init__(self, at):
            super().__init__()
            self.at = at

        def step(self, chip, cycle, nets):
            self.drive(16, 0 if cycle >= self.at else 1)

    results = set()
    for at in range(40, 48):
        chip = Chip()
        chip.attach(Edge(at))
        chip.load(assemble("wait 0 edge 0 resync\nset pins, 1\nh:\n jmp h\n"), 0,
                  clkdiv=8, in_base=16, set_base=8, set_count=1)
        hit = []
        chip.watchers.append(lambda c, n, h=hit: h.append(c) if n & 0x100 and not h else None)
        chip.run(80)
        results.add(hit[0] - at)
    assert len(results) == 1


def test_timeout_traps():
    src = """
        wait 1 gpio 17
        set pins, 0
    h:  jmp h
    .trap
        set pins, 1
    t:  jmp t
    """
    chip = Chip()
    m = chip.load(assemble(src), 0, set_base=8, set_count=1, timeout=10)
    chip.run(30)
    assert chip.nets & 0x100
    assert m.status_word() & isa.ST_TIMEOUT


def test_open_drain_and_contention():
    class Low(Device):
        def step(self, chip, cycle, nets):
            self.drive(0, 0)

    chip = Chip()
    chip.set_pulls(up=[0])
    chip.load(assemble("set pins, 1\nh:\n jmp h\n"), 0, set_base=0, set_count=1, pindirs=1, od=1)
    chip.run(5)
    assert chip.nets & 1
    chip.attach(Low())
    chip.run(5)
    assert not chip.nets & 1
    assert not chip.contention

    chip2 = Chip()
    chip2.load(assemble("set pins, 1\nh:\n jmp h\n"), 0, set_base=0, set_count=1, pindirs=1)
    chip2.attach(Low())
    chip2.run(5)
    assert chip2.contention


def test_cap_run_length():
    class Sq(Device):
        def step(self, chip, cycle, nets):
            self.drive(16, (cycle // 7) & 1)

    chip = Chip()
    chip.attach(Sq())
    chip.load(assemble("cap 1\n"), 0, in_base=16)
    words = []
    for _ in range(100):
        chip.step()
        words += chip.drain(0)
    durations = [w >> 8 for w in words[1:]]
    assert durations and all(d == 7 for d in durations)


def test_shared_imem_and_overflow():
    p = assemble("\n".join(["nop"] * 40))
    chip = Chip()
    chip.load(p, 0)
    chip.load(p, 1)
    assert chip.sms[0].origin == chip.sms[1].origin
    with pytest.raises(Exception):
        chip.load(assemble("\n".join(["nop"] * 30)), 2)


def test_host_exec_debug_port():
    chip = Chip()
    m = chip.load(assemble("h:\n jmp h\n"), 0)
    chip.exec(0, assemble("set x, 9\n").words[0])
    assert m.x == 9
