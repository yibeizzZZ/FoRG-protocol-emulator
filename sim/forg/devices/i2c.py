"""I2C target (EEPROM-style register file) with clock stretching and a protocol checker."""

from ..chip import Device

# Minimum timings in ns: (tLOW, tHIGH, tSU;STA, tHD;STA, tSU;STO, tBUF)
I2C_TIMING = {
    "standard": (4700, 4000, 4700, 4000, 4000, 4700),
    "fast": (1300, 600, 600, 600, 600, 1300),
}


class I2cTarget(Device):
    """Register-pointer target: first written byte sets the pointer, then
    subsequent writes/reads auto-increment through `mem`."""

    def __init__(self, sda, scl, address=0x50, size=256, stretch_ns=0, mode="standard"):
        super().__init__()
        self.sda, self.scl = sda, scl
        self.address = address
        self.mem = bytearray(size)
        self.stretch_ns = stretch_ns
        self.timing = I2C_TIMING[mode]
        self.ptr = 0
        self.state = "idle"
        self.prev_sda = 1
        self.prev_scl = 1
        self.violations = []
        self.events = []
        self.stretch_until = None
        self.last_scl_edge = None
        self.last_stop = None
        self.last_start = None
        self.first_data = False

    def _violation(self, now, msg):
        if len(self.violations) < 50:
            self.violations.append((now, msg))

    def step(self, chip, cycle, nets):
        now = cycle * chip.period_ns
        sda = (nets >> self.sda) & 1
        scl = (nets >> self.scl) & 1
        if self.stretch_until is not None and now >= self.stretch_until:
            self.stretch_until = None
            self.drive(self.scl, None)

        t_low, t_high, t_su_sta, t_hd_sta, t_su_sto, t_buf = self.timing
        if scl != self.prev_scl:
            if self.last_scl_edge is not None and self.state != "idle":
                width = now - self.last_scl_edge
                need = t_high if self.prev_scl else t_low
                if width + chip.period_ns < need and self.stretch_until is None:
                    self._violation(now, f"SCL {'high' if self.prev_scl else 'low'} {width:.0f}ns < {need}ns")
            self.last_scl_edge = now

        if scl and self.prev_scl and sda != self.prev_sda:
            if sda == 0:
                self._start(now)
            else:
                self._stop(now)
        elif scl and not self.prev_scl:
            self._rise(sda)
        elif not scl and self.prev_scl:
            if self.last_start is not None and self.state == "addr" and self.bits == 0:
                if now - self.last_start + chip.period_ns < t_hd_sta:
                    self._violation(now, "tHD;STA too short")
            self._fall(now)
        self.prev_sda, self.prev_scl = sda, scl

    def _start(self, now):
        if self.last_stop is not None and self.state == "idle":
            if now - self.last_stop < self.timing[5]:
                self._violation(now, "tBUF too short")
        self.events.append(("S" if self.state == "idle" else "Sr", now))
        self.state = "addr"
        self.bits = 0
        self.shift = 0
        self.last_start = now
        self.drive(self.sda, None)

    def _stop(self, now):
        self.events.append(("P", now))
        self.state = "idle"
        self.last_stop = now
        self.drive(self.sda, None)

    def _rise(self, sda):
        if self.state in ("addr", "write"):
            if self.bits < 8:
                self.shift = (self.shift << 1) | sda
                self.bits += 1
        elif self.state == "read":
            if self.bits == 8:
                self.master_ack = sda
                self.bits += 1

    def _fall(self, now):
        if self.state in ("addr", "write"):
            if self.bits == 8:
                ack = True
                if self.state == "addr":
                    ack = (self.shift >> 1) == self.address
                if ack:
                    self.drive(self.sda, 0)
                self.bits = 9
                self.pending_ack = ack
            elif self.bits == 9:
                self.drive(self.sda, None)
                self._byte_done(now)
        elif self.state == "read":
            if self.bits < 8:
                self.bits += 1
                if self.bits < 8:
                    self._drive_read_bit()
                else:
                    self.drive(self.sda, None)
            elif self.bits == 9:
                if self.master_ack == 0:
                    self.ptr = (self.ptr + 1) % len(self.mem)
                    self._begin_read_byte()
                    self._stretch(now)
                else:
                    self.ptr = (self.ptr + 1) % len(self.mem)
                    self.state = "wait_stop"

    def _byte_done(self, now):
        b = self.shift
        if self.state == "addr":
            if not self.pending_ack:
                self.state = "wait_stop"
                return
            self.events.append(("A", b >> 1, "R" if b & 1 else "W"))
            if b & 1:
                self.state = "read"
                self._begin_read_byte()
            else:
                self.state = "write"
                self.first_data = True
                self.bits = 0
                self.shift = 0
        else:
            if self.first_data:
                self.ptr = b % len(self.mem)
                self.first_data = False
            else:
                self.mem[self.ptr] = b
                self.events.append(("W", self.ptr, b))
                self.ptr = (self.ptr + 1) % len(self.mem)
            self.bits = 0
            self.shift = 0
        self._stretch(now)

    def _begin_read_byte(self):
        self.read_byte = self.mem[self.ptr]
        self.events.append(("R", self.ptr, self.read_byte))
        self.bits = 0
        self._drive_read_bit()

    def _drive_read_bit(self):
        bit = (self.read_byte >> (7 - self.bits)) & 1
        self.drive(self.sda, 0 if bit == 0 else None)

    def _stretch(self, now):
        if self.stretch_ns:
            self.drive(self.scl, 0)
            self.stretch_until = now + self.stretch_ns
