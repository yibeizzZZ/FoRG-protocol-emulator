"""Cycle-accurate model of one FoRG state machine (SM)."""

from collections import deque

from . import isa
from .line import Crc, Deserializer, LineConfig, Serializer

W = isa.WORD_MASK

DEFAULT_CONFIG = {
    "clkdiv": 1.0,
    "out_base": 0,
    "out_count": 0,
    "set_base": 0,
    "set_count": 0,
    "in_base": 0,
    "side_base": 0,
    "jmp_pin": 0,
    "in_shift": "right",
    "out_shift": "right",
    "autopush": 0,
    "push_thresh": 32,
    "autopull": 0,
    "pull_thresh": 32,
    "timeout": 0,
    "trap": None,
    "start": 0,
    "crc_width": 16,
    "crc_poly": 0xA001,
    "crc_init": 0xFFFF,
    "crc_reflect": 1,
    "crc_src": "none",
    "line_period": 0,
    "pindirs": 0,
    "pins": 0,
    "od": 0,
    "sync_bypass": 0,
}

FIFO_DEPTH = 4


class SimError(Exception):
    pass


def bitrev32(v):
    return int(f"{v & W:032b}"[::-1], 2)


class StateMachine:
    def __init__(self, chip, index):
        self.chip = chip
        self.index = index
        self.enabled = False
        self.origin = 0
        self.program = None
        self.cfg = dict(DEFAULT_CONFIG)
        self.txf = deque()
        self.rxf = deque()
        self.trace = None
        self._apply_config()
        self.restart()

    def configure(self, **kw):
        unknown = {k for k in kw if k not in DEFAULT_CONFIG and not k.startswith("line_")}
        if unknown:
            raise SimError(f"unknown SM config keys: {sorted(unknown)}")
        self.cfg.update(kw)
        self._apply_config()

    def _apply_config(self):
        c = self.cfg
        n = self.chip.num_pins
        self.div256 = max(256, int(round(float(c["clkdiv"]) * 256)))
        self.cfg_out_base = c["out_base"] % n
        self.cfg_in_base_abs = c["in_base"] % n
        self.line_cfg = LineConfig(c)
        self.crc = Crc(c["crc_width"], c["crc_poly"], c["crc_init"], bool(c["crc_reflect"]))
        self.crc_src = c["crc_src"]
        if self.crc_src not in ("none", "in", "out"):
            raise SimError("crc_src must be none, in or out")
        if c["in_shift"] not in ("left", "right") or c["out_shift"] not in ("left", "right"):
            raise SimError("in_shift/out_shift must be left or right")
        self.line_tx = Serializer(self)
        self.line_rx = Deserializer(self)

    def restart(self):
        self.pc = self.origin + (self.cfg["start"] or 0)
        self.x = 0
        self.y = 0
        self.isr = 0
        self.isr_count = 0
        self.osr = 0
        self.osr_count = 32
        self.delay = 0
        self.status = 0
        self.stall_count = 0
        self.edge_armed = False
        self.irq_waiting = False
        self.cap_last = None
        self.cap_count = 0
        self.acc = 0
        self.issue = True
        self.cycles_executed = 0
        self.stall_cycles = 0
        self.crc.reset()
        self.line_tx.reset()
        self.line_rx.reset()

    # Pin helpers

    def _rot_pins(self, pins, base):
        n = self.chip.num_pins
        full = (1 << n) - 1
        return ((pins >> base) | (pins << (n - base))) & full

    def _write_group(self, base, count, value, dirs=False):
        for i in range(count):
            p = (base + i) % self.chip.num_pins
            if dirs:
                self.chip.write_dir(p, (value >> i) & 1)
            else:
                self.chip.write_pin(p, (value >> i) & 1)

    def status_word(self):
        s = self.status | isa.ST_MARKER
        if not self.txf:
            s |= isa.ST_TXF_EMPTY
        if len(self.rxf) >= FIFO_DEPTH:
            s |= isa.ST_RXF_FULL
        if self.line_rx.active:
            s |= isa.ST_RX_ACTIVE
        return s

    # Clocking

    def step(self, pins):
        """Advance one system clock cycle. `pins` are the synchronized inputs."""
        if not self.enabled:
            return
        self.line_tx.tick()
        self.line_rx.tick(pins)
        self.acc += 256
        tick = self.acc >= self.div256
        if tick:
            self.acc -= self.div256
        if self.delay:
            if tick:
                self.delay -= 1
            return
        if not tick:
            word = self.chip.imem[self.pc]
            if word >> 13 == isa.OP_WAIT and word & (1 << 5):
                if self._wait_satisfied(isa.decode(word), pins):
                    self.acc = 0
                    self._finish(isa.decode(word), jumped=False)
            return
        self._execute(pins)

    def _execute(self, pins):
        ins = isa.decode(self.chip.imem[self.pc])
        side, delay = isa.split_ds(ins.ds, self.program.side_count if self.program else 0)
        if side is not None:
            self._write_group(self.cfg["side_base"], self.program.side_count, side)
        if self.trace is not None:
            self.trace.append((self.chip.cycle, self.pc, ins.word, self.x, self.y, self.isr, self.osr))
        result = self._dispatch(ins, pins)
        if result is None:
            self.stall_cycles += 1
            self.stall_count += 1
            self.issue = False
            t = self.cfg["timeout"]
            if t and self.stall_count >= t and self.cfg["trap"] is not None:
                self.status |= isa.ST_TIMEOUT
                self._trap()
            return
        self._finish(ins, jumped=result, delay=delay)

    def _finish(self, ins, jumped, delay=None):
        if delay is None:
            _, delay = isa.split_ds(ins.ds, self.program.side_count if self.program else 0)
        self.cycles_executed += 1
        self.stall_count = 0
        self.issue = True
        self.edge_armed = False
        self.irq_waiting = False
        if not jumped:
            if self.pc == self.origin + self.program.wrap:
                self.pc = self.origin + self.program.wrap_target
            else:
                self.pc = (self.pc + 1) % isa.IMEM_SIZE
        self.delay = delay

    def _trap(self):
        self.stall_count = 0
        self.issue = True
        self.edge_armed = False
        self.irq_waiting = False
        self.pc = self.origin + self.cfg["trap"]

    # Instruction semantics. Return True if jumped, False if completed, None if stalled.

    def _dispatch(self, ins, pins):
        op = ins.op
        a = ins.arg
        if op == isa.OP_JMP:
            return self._jmp(a, pins)
        if op == isa.OP_WAIT:
            if not self._wait_satisfied(ins, pins):
                return None
            if a & (1 << 5):
                self.acc = 0
            return False
        if op == isa.OP_IN:
            return self._in(a, pins)
        if op == isa.OP_OUT:
            return self._out(a)
        if op == isa.OP_CTL:
            return self._ctl(a)
        if op == isa.OP_MOV:
            return self._mov(a, pins)
        if op == isa.OP_CAP:
            if a & 0x100:
                imm = isa.sext(a & 0x7F, 7)
                if a & 0x80:
                    self.y = (self.y + imm) & W
                else:
                    self.x = (self.x + imm) & W
                return False
            return self._cap((a & 7) + 1, pins)
        return self._set(a)

    def _jmp(self, a, pins):
        cond = a >> 6
        target = a & 0x3F
        if cond == 0:
            take = True
        elif cond == 1:
            take = self.x == 0
        elif cond == 2:
            take = self.x != 0
            self.x = (self.x - 1) & W
        elif cond == 3:
            take = self.y == 0
        elif cond == 4:
            take = self.y != 0
            self.y = (self.y - 1) & W
        elif cond == 5:
            take = self.x != self.y
        elif cond == 6:
            take = bool((pins >> (self.cfg["jmp_pin"] % self.chip.num_pins)) & 1)
        else:
            take = self.osr_count < self.cfg["pull_thresh"]
        if take:
            self.pc = target
            return True
        return False

    def _wait_satisfied(self, ins, pins):
        a = ins.arg
        pol = a >> 8
        src = (a >> 6) & 3
        idx = a & 0x1F
        n = self.chip.num_pins
        if src == 0:
            return ((pins >> (idx % n)) & 1) == pol
        if src == 1:
            return ((pins >> ((self.cfg["in_base"] + idx) % n)) & 1) == pol
        if src == 2:
            bit = 1 << (idx & 7)
            is_set = bool(self.chip.irq & bit)
            if is_set == bool(pol):
                if pol:
                    self.chip.irq &= ~bit
                return True
            return False
        level = (pins >> ((self.cfg["in_base"] + idx) % n)) & 1
        if level != pol:
            self.edge_armed = True
            return False
        return self.edge_armed

    def _push_isr(self, block):
        if len(self.rxf) >= FIFO_DEPTH:
            if block:
                return False
            self.status |= isa.ST_RXF_OVF
        else:
            self.rxf.append(self.isr & W)
        self.isr = 0
        self.isr_count = 0
        return True

    def _in(self, a, pins):
        src = a >> 6
        n = isa.bit_count(a & 0x1F)
        c = self.cfg
        will_push = c["autopush"] and self.isr_count + n >= c["push_thresh"]
        if will_push and len(self.rxf) >= FIFO_DEPTH:
            return None
        if src == 7:
            if len(self.line_rx.bits) < n:
                if not self.line_rx.active and c["trap"] is not None:
                    self._trap()
                    return True
                return None
            bits = self.line_rx.take(n)
            data = 0
            if c["in_shift"] == "right":
                for i, b in enumerate(bits):
                    data |= b << i
            else:
                for b in bits:
                    data = (data << 1) | b
        elif src == 0:
            data = self._rot_pins(pins, self.cfg_in_base_abs)
        elif src == 1:
            data = self.x
        elif src == 2:
            data = self.y
        elif src == 3:
            data = 0
        elif src == 5:
            data = self.isr
        elif src == 6:
            data = self.osr
        else:
            raise SimError(f"illegal IN source at pc={self.pc}")
        mask = (1 << n) - 1
        data &= mask
        if c["in_shift"] == "right":
            self.isr = ((self.isr >> n) | (data << (32 - n))) & W if n < 32 else data
        else:
            self.isr = ((self.isr << n) | data) & W if n < 32 else data
        self.isr_count = min(32, self.isr_count + n)
        if will_push:
            self._push_isr(True)
        return False

    def _out(self, a):
        dst = a >> 6
        n = isa.bit_count(a & 0x1F)
        c = self.cfg
        refill = c["autopull"] and self.osr_count >= c["pull_thresh"]
        if refill and not self.txf:
            return None
        if dst == 7 and not self.line_tx.can_load():
            return None
        if refill:
            self.osr = self.txf.popleft()
            self.osr_count = 0
        mask = (1 << n) - 1
        if c["out_shift"] == "right":
            data = self.osr & mask
            self.osr = (self.osr >> n) if n < 32 else 0
        else:
            data = (self.osr >> (32 - n)) & mask
            self.osr = (self.osr << n) & W if n < 32 else 0
        self.osr_count = min(32, self.osr_count + n)
        if dst == 0:
            self._write_group(self.cfg_out_base, min(n, c["out_count"]), data)
        elif dst == 1:
            self.x = data
        elif dst == 2:
            self.y = data
        elif dst == 3:
            pass
        elif dst == 4:
            self._write_group(self.cfg_out_base, min(n, c["out_count"]), data, dirs=True)
        elif dst == 5:
            self.pc = data % isa.IMEM_SIZE
            return True
        elif dst == 6:
            self.isr = data
            self.isr_count = n
        else:
            if c["out_shift"] == "right":
                bits = [(data >> i) & 1 for i in range(n)]
            else:
                bits = [(data >> (n - 1 - i)) & 1 for i in range(n)]
            if self.crc_src == "out":
                for b in bits:
                    self.crc.bit(b)
            self.line_tx.load(bits)
        return False

    def _ctl(self, a):
        sub = a >> 7
        c = self.cfg
        if sub == isa.CTL_PUSH:
            if a & 0x40 and self.isr_count < c["push_thresh"]:
                return False
            return False if self._push_isr(bool(a & 0x20)) else None
        if sub == isa.CTL_PULL:
            if a & 0x40 and self.osr_count < c["pull_thresh"]:
                return False
            if not self.txf:
                if a & 0x20:
                    return None
                self.osr = self.x
            else:
                self.osr = self.txf.popleft()
            self.osr_count = 0
            return False
        if sub == isa.CTL_LINE:
            if a & isa.LINE_DRAIN and self.line_tx.busy:
                return None
            if a & isa.LINE_CRC_RESET:
                self.crc.reset()
            if a & isa.LINE_TX_RESET:
                self.line_tx.reset()
            if a & isa.LINE_RX_STOP:
                self.line_rx.stop()
            if a & isa.LINE_RX_ARM:
                self.line_rx.arm()
            if a & isa.LINE_FLUSH:
                self.osr_count = 32
                self.isr = 0
                self.isr_count = 0
            return False
        bit = 1 << (a & 7)
        if a & 0x40:
            self.chip.irq &= ~bit
            return False
        if a & 0x20:
            if not self.irq_waiting:
                self.chip.irq |= bit
                self.irq_waiting = True
                return None
            return False if not self.chip.irq & bit else None
        self.chip.irq |= bit
        return False

    def _mov_src(self, s, pins):
        if s == 0:
            return self._rot_pins(pins, self.cfg_in_base_abs)
        if s == 1:
            return self.x
        if s == 2:
            return self.y
        if s == 3:
            return 0
        if s == 4:
            return self.status_word()
        if s == 5:
            return self.isr
        if s == 6:
            return self.osr
        return self.crc.value

    def _mov(self, a, pins):
        dst = a >> 6
        op = (a >> 4) & 3
        v = self._mov_src(a & 7, pins) & W
        if op == 1:
            v = ~v & W
        elif op == 2:
            v = bitrev32(v)
        elif op == 3:
            raise SimError(f"reserved MOV operation at pc={self.pc}")
        c = self.cfg
        if dst == 0:
            self._write_group(self.cfg_out_base, c["out_count"], v)
        elif dst == 1:
            self.x = v
        elif dst == 2:
            self.y = v
        elif dst == 3:
            self._write_group(self.cfg_out_base, c["out_count"], v, dirs=True)
        elif dst == 4:
            self.pc = v % isa.IMEM_SIZE
            return True
        elif dst == 5:
            self.isr = v
            self.isr_count = 0
        elif dst == 6:
            self.osr = v
            self.osr_count = 0
        else:
            self.crc.value = v & self.crc.mask
        return False

    def _cap(self, n, pins):
        sample = self._rot_pins(pins, self.cfg_in_base_abs) & ((1 << n) - 1)
        if self.cap_last is None:
            self.cap_last, self.cap_count = sample, 1
            return False
        if sample != self.cap_last or self.cap_count >= 0xFFFFFF:
            word = (self.cap_count << 8) | self.cap_last
            if len(self.rxf) >= FIFO_DEPTH:
                self.status |= isa.ST_RXF_OVF
            else:
                self.rxf.append(word)
            self.cap_last, self.cap_count = sample, 1
        else:
            self.cap_count += 1
        return False

    def _set(self, a):
        dst = (a >> 6) & 7
        imm = a & 0x1F
        c = self.cfg
        if dst == 0:
            self._write_group(c["set_base"], c["set_count"], imm)
        elif dst == 1:
            self.x = imm
        elif dst == 2:
            self.y = imm
        elif dst == 3:
            self._write_group(c["set_base"], c["set_count"], imm, dirs=True)
        else:
            raise SimError(f"illegal SET destination at pc={self.pc}")
        return False
