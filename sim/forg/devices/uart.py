"""UART bus-functional models: a timing-checking receiver and a transmitter."""

from collections import deque

from ..chip import Device


class UartMonitor(Device):
    """Decodes 8N1 frames from a pin and measures edge timing error."""

    def __init__(self, pin, baud, data_bits=8):
        super().__init__()
        self.pin = pin
        self.bit_ns = 1e9 / baud
        self.data_bits = data_bits
        self.frames = []
        self.framing_errors = 0
        self.max_edge_error = 0.0
        self.prev = 1
        self.state = "idle"
        self.t0 = 0.0
        self.sample_idx = 0
        self.shift = 0

    @property
    def bytes(self):
        return bytes(f[0] for f in self.frames)

    def step(self, chip, cycle, nets):
        v = (nets >> self.pin) & 1
        now = cycle * chip.period_ns
        if v != self.prev and self.state == "frame":
            k = round((now - self.t0) / self.bit_ns)
            err = abs(now - self.t0 - k * self.bit_ns) / self.bit_ns
            self.max_edge_error = max(self.max_edge_error, err)
        if self.state == "idle":
            if self.prev == 1 and v == 0:
                self.state = "frame"
                self.t0 = now
                self.sample_idx = 0
                self.shift = 0
        else:
            t_sample = self.t0 + (self.sample_idx + 0.5) * self.bit_ns
            if now >= t_sample:
                i = self.sample_idx
                if i == 0:
                    if v != 0:
                        self.state = "idle"
                elif i <= self.data_bits:
                    self.shift |= v << (i - 1)
                else:
                    ok = v == 1
                    if not ok:
                        self.framing_errors += 1
                    self.frames.append((self.shift, ok, self.t0))
                    self.state = "idle"
                self.sample_idx += 1
        self.prev = v


class UartDriver(Device):
    """Drives 8N1 frames onto a pin. `baud_error` is a fractional rate offset."""

    def __init__(self, pin, baud, baud_error=0.0, stop_bits=1.0, gap_bits=0.0):
        super().__init__()
        self.pin = pin
        self.bit_ns = 1e9 / (baud * (1 + baud_error))
        self.stop_bits = stop_bits
        self.gap_bits = gap_bits
        self.queue = deque()
        self.frame = None
        self.t0 = None
        self.drive(pin, 1)

    def send(self, data, bad_stop=False):
        for b in bytes(data):
            self.queue.append((b, bad_stop))

    @property
    def idle(self):
        return self.frame is None and not self.queue

    def step(self, chip, cycle, nets):
        now = cycle * chip.period_ns
        if self.frame is None:
            if not self.queue:
                return
            self.frame = self.queue.popleft()
            self.t0 = now
        b, bad_stop = self.frame
        i = int((now - self.t0) // self.bit_ns)
        if i == 0:
            level = 0
        elif i <= 8:
            level = (b >> (i - 1)) & 1
        elif i < 9 + self.stop_bits:
            level = 0 if bad_stop else 1
        else:
            level = 1
            if i >= 9 + self.stop_bits + self.gap_bits:
                self.frame = None
        self.drive(self.pin, level)
