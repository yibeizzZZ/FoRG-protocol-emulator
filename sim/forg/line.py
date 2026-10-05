"""Per-state-machine line unit: CRC engine, serializer and deserializer.

The line unit is the main architectural departure from RP2040 PIO. It runs on
the system clock with its own bit-period counter, so firmware only has to keep
a small bit buffer fed. Bit stuffing, NRZI, Manchester and CRC happen in
hardware, which is what makes USB low-speed and 10BASE-T reachable on a
tiny core without cycle-perfect firmware loops.
"""


class Crc:
    """Generic bit-serial CRC/LFSR.

    reflect=True shifts right (LSB-first protocols: USB, Ethernet), with
    `poly` given in reflected form. reflect=False shifts left (CAN, SD).
    """

    def __init__(self, width=16, poly=0xA001, init=0xFFFF, reflect=True):
        self.width = width
        self.poly = poly
        self.init = init
        self.reflect = reflect
        self.mask = (1 << width) - 1 if width < 32 else 0xFFFFFFFF
        self.value = init & self.mask

    def reset(self):
        self.value = self.init & self.mask

    def bit(self, b):
        v = self.value
        if self.reflect:
            fb = (v ^ b) & 1
            v >>= 1
            if fb:
                v ^= self.poly
        else:
            fb = ((v >> (self.width - 1)) ^ b) & 1
            v = (v << 1) & self.mask
            if fb:
                v ^= self.poly
        self.value = v & self.mask


class LineConfig:
    def __init__(self, cfg):
        self.period = int(cfg.get("line_period", 0))
        self.nrzi = bool(cfg.get("line_nrzi", 0))
        self.stuff = int(cfg.get("line_stuff", 0))
        self.stuff_mode = cfg.get("line_stuff_mode", "ones")
        self.manchester = bool(cfg.get("line_manchester", 0))
        self.diff = bool(cfg.get("line_diff", 0))
        self.idle = int(cfg.get("line_idle", 1)) & 1
        self.se0 = bool(cfg.get("line_se0", 0))
        if self.stuff_mode not in ("ones", "any"):
            raise ValueError("line_stuff_mode must be 'ones' or 'any'")
        if self.manchester and self.period and self.period % 2:
            raise ValueError("line_period must be even for Manchester")


class Serializer:
    """TX half. Holds up to 32 queued data bits plus the bit in flight."""

    def __init__(self, sm):
        self.sm = sm
        self.reset()

    def reset(self):
        cfg = self.sm.line_cfg
        self.queue = []
        self.active = False
        self.count = 0
        self.level = cfg.idle
        self.run_val = None
        self.run_len = 0
        self.stuff_next = False
        self.half_level = None

    @property
    def busy(self):
        return self.active or bool(self.queue) or self.stuff_next

    def can_load(self):
        return not self.queue

    def load(self, bits):
        self.queue.extend(bits)

    def _drive(self, level):
        sm = self.sm
        base = sm.cfg_out_base
        sm.chip.write_pin(base, level)
        if sm.line_cfg.diff:
            sm.chip.write_pin(base + 1, level ^ 1)

    def _start_bit(self, data, stuffed):
        cfg = self.sm.line_cfg
        if cfg.stuff and not stuffed:
            if cfg.stuff_mode == "ones":
                self.run_len = self.run_len + 1 if data else 0
                self.stuff_next = self.run_len >= cfg.stuff
            else:
                if data == self.run_val:
                    self.run_len += 1
                else:
                    self.run_val, self.run_len = data, 1
                self.stuff_next = self.run_len >= cfg.stuff
        elif stuffed:
            if cfg.stuff_mode == "ones":
                self.run_len = 0
            else:
                self.run_val, self.run_len = data, 1
        if cfg.nrzi:
            if data == 0:
                self.level ^= 1
        else:
            self.level = data
        self.active = True
        self.count = cfg.period
        if cfg.manchester:
            self.half_level = self.level
            self._drive(self.level ^ 1)
        else:
            self.half_level = None
            self._drive(self.level)

    def tick(self):
        cfg = self.sm.line_cfg
        if self.active:
            self.count -= 1
            if self.half_level is not None and self.count == cfg.period // 2:
                self._drive(self.half_level)
            if self.count > 0:
                return
            self.active = False
        if self.stuff_next:
            self.stuff_next = False
            if cfg.stuff_mode == "ones":
                bit = 0
            else:
                bit = self.run_val ^ 1
            self._start_bit(bit, stuffed=True)
        elif self.queue:
            self._start_bit(self.queue.pop(0), stuffed=False)


class Deserializer:
    """RX half with edge-aligned sampling (a 1-cycle-resolution DPLL)."""

    MAX_BITS = 32

    def __init__(self, sm):
        self.sm = sm
        self.reset()

    def reset(self):
        self.state = "idle"
        self.bits = []
        self.countdown = 0
        self.prev_pin = None
        self.prev_level = self.sm.line_cfg.idle
        self.run_val = None
        self.run_len = 0

    def arm(self):
        self.reset()
        self.state = "hunt"
        self.sm.status &= ~0x38  # EOP, STUFF_ERR, RX_OVF

    def stop(self):
        self.state = "idle"

    @property
    def active(self):
        return self.state != "idle"

    def take(self, n):
        out, self.bits = self.bits[:n], self.bits[n:]
        return out

    def tick(self, pins):
        if self.state == "idle":
            return
        sm = self.sm
        cfg = sm.line_cfg
        pin = (pins >> sm.cfg_in_base_abs) & 1
        edge = self.prev_pin is not None and pin != self.prev_pin
        self.prev_pin = pin
        half = max(cfg.period // 2, 1)
        if self.state == "hunt":
            if edge and pin != cfg.idle:
                self.state = "run"
                self.countdown = half
            return
        if edge:
            self.countdown = half
        self.countdown -= 1
        if self.countdown > 0:
            return
        self.countdown = cfg.period
        self._sample(pin, pins)

    def _sample(self, level, pins):
        sm = self.sm
        cfg = sm.line_cfg
        from . import isa

        if cfg.se0:
            other = (pins >> sm.chip.wrap_pin(sm.cfg_in_base_abs + 1)) & 1
            if level == 0 and other == 0:
                sm.status |= isa.ST_RX_EOP
                self.state = "idle"
                return
        if cfg.nrzi:
            data = 1 if level == self.prev_level else 0
        else:
            data = level
        self.prev_level = level
        if cfg.stuff:
            if cfg.stuff_mode == "ones":
                if self.run_len >= cfg.stuff:
                    self.run_len = 0
                    if data == 1:
                        sm.status |= isa.ST_RX_STUFF_ERR
                        self.state = "idle"
                    return
                self.run_len = self.run_len + 1 if data else 0
            else:
                if self.run_len >= cfg.stuff:
                    if data == self.run_val:
                        sm.status |= isa.ST_RX_STUFF_ERR
                        self.state = "idle"
                        return
                    self.run_val, self.run_len = data, 1
                    return
                if data == self.run_val:
                    self.run_len += 1
                else:
                    self.run_val, self.run_len = data, 1
        if sm.crc_src == "in":
            sm.crc.bit(data)
        if len(self.bits) >= self.MAX_BITS:
            sm.status |= isa.ST_RX_OVF
            return
        self.bits.append(data)
