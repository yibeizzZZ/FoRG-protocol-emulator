"""10BASE-T reference monitor: Manchester decode, preamble/SFD, FCS (zlib CRC32), link pulses."""

import zlib

from ..chip import Device

BIT_NS = 100.0


def frame_words(frame: bytes):
    """Host FIFO stream for eth10_tx."""
    frame = bytes(frame)
    pad = frame + b"\0" * ((-len(frame)) % 4)
    return [len(frame), 0x55555555, 0xD5555555] + [
        int.from_bytes(pad[i:i + 4], "little") for i in range(0, len(pad), 4)
    ]


class Eth10Monitor(Device):
    """Decodes the TD+/TD- differential pair.

    Every Manchester bit has a transition in the middle; its direction is the
    bit value (towards positive = 1). Transitions at bit boundaries are
    ignored. A missing mid-bit transition ends the frame.
    """

    def __init__(self, tdp, tdn, bit_ns=BIT_NS):
        super().__init__()
        self.tdp, self.tdn = tdp, tdn
        self.bit_ns = bit_ns
        self.frames = []
        self.link_pulses = []
        self.errors = []
        self.state = "idle"
        self.prev = 0
        self.pulse_start = None
        self.mid = None

    def step(self, chip, cycle, nets):
        now = cycle * chip.period_ns
        d = ((nets >> self.tdp) & 1) - ((nets >> self.tdn) & 1)
        T = self.bit_ns
        if self.state == "idle":
            if self.prev == 0 and d == 1:
                self.pulse_start = now
            elif self.prev == 1 and d == 0 and self.pulse_start is not None:
                self.link_pulses.append((self.pulse_start, now - self.pulse_start))
                self.pulse_start = None
            elif self.prev == 0 and d == -1:
                self.state = "rx"
                self.bits = []
                self.t_start = now
                self.mid = now + T / 2
                self.pulse_start = None
        else:
            if d != self.prev and abs(now - self.mid) <= T / 4:
                if d == 0:
                    self._finish()
                else:
                    self.bits.append(1 if d == 1 else 0)
                    self.mid = now + T
            elif now > self.mid + T / 4:
                self._finish()
        self.prev = d

    def _finish(self):
        self.state = "idle"
        bits = self.bits
        sfd = None
        for i in range(8, len(bits) - 7):
            if sum(bits[i + k] << k for k in range(8)) == 0xD5:
                sfd = i
                break
        if sfd is None:
            self.errors.append(("no SFD", self.t_start))
            return
        if any(bits[i] != (1 - i % 2) for i in range(sfd)):
            self.errors.append(("bad preamble", self.t_start))
        body = bits[sfd + 8:]
        if len(body) % 8:
            self.errors.append((f"{len(body)} body bits not byte aligned", self.t_start))
        n = len(body) // 8
        data = bytes(sum(body[i * 8 + k] << k for k in range(8)) for i in range(n))
        ok = len(data) >= 4 and zlib.crc32(data[:-4]) == int.from_bytes(data[-4:], "little")
        self.frames.append({
            "t": self.t_start,
            "preamble_bits": sfd,
            "data": data[:-4] if ok else data,
            "fcs_ok": ok,
        })
