"""USB low-speed reference models: packet builder, line monitor and line driver.

The CRC and encoding code here is written independently of forg.line so the
two implementations check each other.
"""

from ..chip import Device

LS_BIT_NS = 1e9 / 1.5e6

PIDS = {
    "OUT": 0x1, "IN": 0x9, "SOF": 0x5, "SETUP": 0xD,
    "DATA0": 0x3, "DATA1": 0xB, "ACK": 0x2, "NAK": 0xA, "STALL": 0xE,
}
DATA_PIDS = {0x3, 0xB}
TOKEN_PIDS = {0x1, 0x9, 0x5, 0xD}


def pid_byte(name):
    p = PIDS[name]
    return p | ((~p & 0xF) << 4)


def crc5(value11):
    """USB token CRC5 over 11 bits, LSB first. Returns the 5 bits to send."""
    crc = 0x1F
    for i in range(11):
        bit = (value11 >> i) & 1
        top = (crc >> 4) & 1
        crc = (crc << 1) & 0x1F
        if bit ^ top:
            crc ^= 0x05
    out = ~crc & 0x1F
    return int(f"{out:05b}"[::-1], 2)


def crc16(data):
    """USB data CRC16 (poly 0x8005, MSB-first table-free form). Returns the
    16-bit value as transmitted LSB first."""
    crc = 0xFFFF
    for byte in data:
        for i in range(8):
            bit = (byte >> i) & 1
            top = (crc >> 15) & 1
            crc = (crc << 1) & 0xFFFF
            if bit ^ top:
                crc ^= 0x8005
    out = ~crc & 0xFFFF
    return int(f"{out:016b}"[::-1], 2)


def token_packet(pid, addr, endp):
    v = (addr & 0x7F) | ((endp & 0xF) << 7)
    c = crc5(v)
    w = v | (c << 11)
    return bytes([0x80, pid_byte(pid), w & 0xFF, w >> 8])


def data_packet(pid, payload):
    c = crc16(payload)
    return bytes([0x80, pid_byte(pid)]) + bytes(payload) + bytes([c & 0xFF, c >> 8])


def handshake_packet(pid):
    return bytes([0x80, pid_byte(pid)])


def encode_line(packet):
    """Packet bytes -> list of line states ('J'/'K'/'0') per bit, incl. EOP."""
    states = []
    level = "J"
    ones = 0
    for byte in packet:
        for i in range(8):
            bit = (byte >> i) & 1
            if bit == 0:
                level = "K" if level == "J" else "J"
                ones = 0
            else:
                ones += 1
            states.append(level)
            if ones == 6:
                level = "K" if level == "J" else "J"
                states.append(level)
                ones = 0
    return states + ["0", "0", "J"]


class UsbLsMonitor(Device):
    """Decodes low-speed packets from D-/D+ and validates SYNC, PID, CRC, stuffing, EOP."""

    def __init__(self, dm, dp, bit_ns=LS_BIT_NS):
        super().__init__()
        self.dm, self.dp = dm, dp
        self.bit_ns = bit_ns
        self.packets = []
        self.errors = []
        self.state = "idle"
        self.prev_line = "J"

    def _line(self, nets):
        dm = (nets >> self.dm) & 1
        dp = (nets >> self.dp) & 1
        if dm and not dp:
            return "J"
        if dp and not dm:
            return "K"
        if not dp and not dm:
            return "0"
        return "1"

    def step(self, chip, cycle, nets):
        now = cycle * chip.period_ns
        line = self._line(nets)
        if self.state == "idle":
            if self.prev_line == "J" and line == "K":
                self.state = "rx"
                self.bits = []
                self.ones = 0
                self.last = "J"
                self.next_sample = now + self.bit_ns / 2
                self.se0_bits = 0
                self.t_start = now
                self.stuff_err = False
        else:
            if line != self.prev_line:
                self.next_sample = now + self.bit_ns / 2
            if now >= self.next_sample:
                self.next_sample += self.bit_ns
                self._sample(line, now)
        self.prev_line = line

    def _sample(self, line, now):
        if line == "0":
            self.se0_bits += 1
            return
        if self.se0_bits:
            if line == "J":
                self._finish(now)
            else:
                self.errors.append(("bad EOP", now))
                self.state = "idle"
            return
        if line == "1":
            self.errors.append(("SE1", now))
            self.state = "idle"
            return
        bit = 1 if line == self.last else 0
        self.last = line
        if self.ones == 6:
            self.ones = 0
            if bit != 0:
                self.stuff_err = True
            return
        self.ones = self.ones + 1 if bit else 0
        self.bits.append(bit)

    def _finish(self, now):
        self.state = "idle"
        if not 1 <= self.se0_bits <= 3:
            self.errors.append((f"EOP SE0 {self.se0_bits} bits", now))
        if len(self.bits) % 8:
            self.errors.append((f"{len(self.bits)} bits not byte aligned", now))
        data = bytes(
            sum(self.bits[i + k] << k for k in range(8)) for i in range(0, len(self.bits) - 7, 8)
        )
        pkt = {"raw": data, "t": self.t_start, "ok": True, "errors": []}
        if self.stuff_err:
            pkt["errors"].append("stuff")
        if not data or data[0] != 0x80:
            pkt["errors"].append("sync")
        elif len(data) >= 2:
            pid = data[1] & 0xF
            if (data[1] >> 4) != (~pid & 0xF):
                pkt["errors"].append("pid check")
            pkt["pid"] = pid
            body = data[2:]
            if pid in DATA_PIDS:
                if len(body) < 2:
                    pkt["errors"].append("short data packet")
                else:
                    pkt["payload"] = body[:-2]
                    if crc16(body[:-2]) != body[-2] | (body[-1] << 8):
                        pkt["errors"].append("crc16")
            elif pid in TOKEN_PIDS:
                if len(body) != 2:
                    pkt["errors"].append("token length")
                else:
                    w = body[0] | (body[1] << 8)
                    if crc5(w & 0x7FF) != w >> 11:
                        pkt["errors"].append("crc5")
                    pkt["addr"] = w & 0x7F
                    pkt["endp"] = (w >> 7) & 0xF
        pkt["ok"] = not pkt["errors"]
        self.packets.append(pkt)


class UsbLsDriver(Device):
    """Drives low-speed packets onto D-/D+ (as a device or host would)."""

    def __init__(self, dm, dp, bit_ns=LS_BIT_NS, rate_error=0.0, release=True):
        super().__init__()
        self.dm, self.dp = dm, dp
        self.bit_ns = bit_ns / (1 + rate_error)
        self.queue = []
        self.states = None
        self.release = release

    def send(self, packet, gap_bits=4):
        self.queue.append((encode_line(packet), gap_bits))

    @property
    def idle(self):
        return self.states is None and not self.queue

    def _set(self, st):
        if st is None:
            self.drive(self.dm, None)
            self.drive(self.dp, None)
        else:
            self.drive(self.dm, 1 if st == "J" else 0)
            self.drive(self.dp, 1 if st == "K" else 0)

    def step(self, chip, cycle, nets):
        now = cycle * chip.period_ns
        if self.states is None:
            if not self.queue:
                return
            self.states, self.gap = self.queue.pop(0)
            self.t0 = now
        i = int((now - self.t0) // self.bit_ns)
        if i < len(self.states):
            self._set(self.states[i])
        elif i < len(self.states) + self.gap:
            self._set(None if self.release else "J")
        else:
            self._set(None if self.release else "J")
            self.states = None
