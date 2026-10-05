"""Top-level chip model: shared instruction memory, state machines, GPIO and nets.

Logical pin map (matches the Tiny Tapeout user interface):

    pins 0-7    uio[0..7]  bidirectional, open-drain capable
    pins 8-15   uo[0..7]   output only (always driven)
    pins 16-23  ui[0..7]   input only

In silicon, ui[0..2] and uo[7] are reserved for the host SPI link that loads
programs and moves FIFO data. The simulator models the host link at the
transaction level (Chip.put / Chip.get) and warns if firmware maps onto those
pins.
"""

from collections import deque

from . import isa
from .asm import Program
from .sm import StateMachine, SimError

OUT_ONLY_MASK = 0x00FF00
IN_ONLY_MASK = 0xFF0000
HOST_RESERVED_PINS = {16, 17, 18, 15}

PIN_NAMES = [f"uio{i}" for i in range(8)] + [f"uo{i}" for i in range(8)] + [f"ui{i}" for i in range(8)]


def pin_index(name) -> int:
    if isinstance(name, int):
        return name
    return PIN_NAMES.index(name)


class Device:
    """Base class for bus-functional models attached to chip pins."""

    def __init__(self):
        self.oe = 0
        self.out = 0

    def drive(self, pin, value):
        """value: 0, 1 or None (release)."""
        bit = 1 << pin
        if value is None:
            self.oe &= ~bit
        else:
            self.oe |= bit
            if value:
                self.out |= bit
            else:
                self.out &= ~bit

    def step(self, chip, cycle, nets):
        pass


class Chip:
    def __init__(self, num_sm=4, clock_hz=50e6, sync_stages=2, num_pins=isa.NUM_PINS):
        self.num_pins = num_pins
        self.clock_hz = clock_hz
        self.period_ns = 1e9 / clock_hz
        self.imem = [isa.encode(isa.OP_JMP, 0, 0)] * isa.IMEM_SIZE
        self.used = [None] * isa.IMEM_SIZE
        self.loaded = {}
        self.irq = 0
        self.out = 0
        self.oe = 0
        self.od = 0
        self.pull_up = 0
        self.pull_down = 0
        self.sync_bypass = 0
        self.nets = 0
        self.cycle = 0
        self.devices = []
        self.contention = []
        self.warnings = []
        self.watchers = []
        self.sync_pipe = deque([0] * sync_stages)
        self.synced = 0
        self.sms = [StateMachine(self, i) for i in range(num_sm)]

    # Configuration

    def wrap_pin(self, p):
        return p % self.num_pins

    def attach(self, device):
        self.devices.append(device)
        return device

    def set_pulls(self, up=(), down=()):
        for p in up:
            self.pull_up |= 1 << pin_index(p)
        for p in down:
            self.pull_down |= 1 << pin_index(p)

    def _alloc(self, size, origin):
        if origin is None:
            for start in range(isa.IMEM_SIZE - size + 1):
                if all(u is None for u in self.used[start:start + size]):
                    return start
            raise SimError(f"no room for {size} instructions in {isa.IMEM_SIZE}-word imem")
        if origin + size > isa.IMEM_SIZE:
            raise SimError("program does not fit at requested origin")
        return origin

    def load(self, program: Program, sm=0, origin=None, enable=True, **overrides):
        """Load `program` into imem (shared if already loaded) and bind it to SM `sm`."""
        key = id(program)
        if key in self.loaded and origin in (None, self.loaded[key]):
            origin = self.loaded[key]
        else:
            origin = self._alloc(len(program), origin)
            for i, w in enumerate(program.relocated(origin)):
                if self.used[origin + i] not in (None, key):
                    raise SimError(f"imem overlap at {origin + i}")
                self.imem[origin + i] = w
                self.used[origin + i] = key
            self.loaded[key] = origin
        m = self.sms[sm]
        m.program = program
        m.origin = origin
        cfg = dict(program.config)
        cfg.update(overrides)
        m.configure(**cfg)
        self._apply_pin_defaults(m)
        self._lint_pins(m)
        m.restart()
        m.enabled = enable
        return m

    def _apply_pin_defaults(self, m):
        c = m.cfg
        self.od |= c["od"]
        self.sync_bypass |= c["sync_bypass"]
        owned = c["pindirs"]
        for p in self._sm_pins(m):
            owned |= 1 << p
        self.out = (self.out & ~owned) | (c["pins"] & owned)
        self.oe |= c["pindirs"]

    def _sm_pins(self, m):
        c = m.cfg
        used = set()
        groups = [
            (c["out_base"], c["out_count"]),
            (c["set_base"], c["set_count"]),
            (c["side_base"], m.program.side_count),
        ]
        if m.line_cfg.period:
            groups.append((c["out_base"], 2 if m.line_cfg.diff else 1))
        for base, count in groups:
            used.update((base + i) % self.num_pins for i in range(count))
        return used

    def _lint_pins(self, m):
        used = self._sm_pins(m)
        for p in sorted(used & HOST_RESERVED_PINS):
            self.warnings.append(f"SM{m.index} drives host-reserved pin {PIN_NAMES[p]}")
        for p in sorted(used):
            if (1 << p) & IN_ONLY_MASK:
                self.warnings.append(f"SM{m.index} drives input-only pin {PIN_NAMES[p]}")

    # Pin writes from state machines (registered, visible next cycle)

    def write_pin(self, p, v):
        bit = 1 << (p % self.num_pins)
        self.out = (self.out | bit) if v else (self.out & ~bit)

    def write_dir(self, p, v):
        bit = 1 << (p % self.num_pins)
        self.oe = (self.oe | bit) if v else (self.oe & ~bit)

    def chip_drive(self):
        oe = (self.oe | OUT_ONLY_MASK) & ~IN_ONLY_MASK
        oe = (oe & ~self.od) | (oe & self.od & ~self.out)
        return oe, self.out

    # Host interface (transaction level)

    def put(self, sm, word):
        """Push one word into an SM's TX FIFO. Returns False if full."""
        f = self.sms[sm].txf
        if len(f) >= 4:
            return False
        f.append(word & isa.WORD_MASK)
        return True

    def get(self, sm):
        f = self.sms[sm].rxf
        return f.popleft() if f else None

    def drain(self, sm):
        """Pop every word currently in an SM's RX FIFO."""
        out = []
        while (w := self.get(sm)) is not None:
            out.append(w)
        return out

    def exec(self, sm, word):
        """Force-execute one instruction on an SM (host debug port)."""
        m = self.sms[sm]
        saved = self.imem[m.pc]
        self.imem[m.pc] = word
        pc = m.pc
        try:
            jumped = m._dispatch(isa.decode(word), self.synced)
        finally:
            self.imem[pc] = saved
        if jumped is False:
            m.pc = pc
        return jumped

    # Simulation

    def resolve(self):
        oe, out = self.chip_drive()
        d1 = oe & out
        d0 = oe & ~out
        for d in self.devices:
            d1 |= d.oe & d.out
            d0 |= d.oe & ~d.out
        full = (1 << self.num_pins) - 1
        cont = d1 & d0
        if cont and len(self.contention) < 100:
            self.contention.append((self.cycle, cont))
        driven = (d1 | d0) & full
        floating = ~driven & full
        hold = self.nets & ~self.pull_down & ~self.pull_up
        self.nets = ((d1 & ~d0) | (floating & (self.pull_up | hold))) & full
        return self.nets

    def step(self):
        nets = self.resolve()
        for w in self.watchers:
            w(self.cycle, nets)
        for d in self.devices:
            d.step(self, self.cycle, nets)
        if self.sync_pipe:
            self.synced = self.sync_pipe.popleft()
            self.sync_pipe.append(nets)
        else:
            self.synced = nets
        if self.sync_bypass:
            self.synced = (self.synced & ~self.sync_bypass) | (nets & self.sync_bypass)
        for m in self.sms:
            if m.enabled:
                m.step(self.synced)
        self.cycle += 1

    def run(self, cycles):
        for _ in range(int(cycles)):
            self.step()

    def run_until(self, cond, max_cycles=1_000_000):
        for _ in range(int(max_cycles)):
            if cond():
                return True
            self.step()
        return cond()

    def time_ns(self, cycle=None):
        return (self.cycle if cycle is None else cycle) * self.period_ns
