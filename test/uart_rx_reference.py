"""Independent asynchronous 8N1 wire source; no firmware/ISA/DUT-state imports.

Edges use wall-clock simulation time, never the receiver's instruction clock.
The caller's byte list is the receive scoreboard, independent of RAM contents.
"""
from cocotb.triggers import Timer
from cocotb.utils import get_sim_time


class SerialSource:
    def __init__(self, pins, *, pin=1):
        self.pins = pins
        self.pin = pin
        self.events = []
        self.frames = []

    def drive(self, value):
        noise = (len(self.events) * 73 + 0xa5) & 255
        self.pins.value = (noise & ~(1 << self.pin)) | (value << self.pin)
        self.events.append((int(get_sim_time(unit='ps')), value))

    async def send(self, payload, *, bit_ns=8680, phase_ns=0, gap_bits=0, bad_stops=()):
        if phase_ns:
            await Timer(round(phase_ns * 1000), unit='ps')
        bit_ps = round(bit_ns * 1000)
        for index, byte in enumerate(payload):
            self.frames.append(dict(start_ps=int(get_sim_time(unit='ps')),
                                    byte=byte, bit_ps=bit_ps, stop=int(index not in bad_stops)))
            for bit in [0] + [(byte >> i) & 1 for i in range(8)] + [int(index not in bad_stops)]:
                self.drive(bit)
                await Timer(bit_ps, unit='ps')
            self.drive(1)
            if gap_bits:
                await Timer(round(gap_bits * bit_ps), unit='ps')

    def decoded(self):
        """Check the driven external trace at symbol centers, not DUT internals."""
        def at(time):
            return next(value for edge, value in reversed(self.events) if edge <= time)
        result = []
        for frame in self.frames:
            start, bit = frame['start_ps'], frame['bit_ps']
            assert at(start + bit // 2) == 0, 'Source START invalid'
            value = sum(at(start + (2 * i + 3) * bit // 2) << i for i in range(8))
            stop = at(start + 19 * bit // 2)
            result.append((value, stop))
        return result
