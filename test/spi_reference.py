"""Pin-only Mode-0 slave and timing monitor, independent of M2 and its ISA."""


class Mode0Slave:
    # These are the board-level test wiring, not imported generator definitions.
    MOSI, MISO, SCLK, CS, OE = 0x80, 0x01, 0x02, 0x04, 0x86

    def __init__(self, reply, half_period_cycles):
        self.reply = list(reply)
        self.half_period = half_period_cycles
        self.driven = self.active = self.complete = False
        self.previous = self.CS
        self.miso = 0
        self.received = []
        self.rising_edges = []
        self.falling_edges = []
        self.high_cycles = []
        self.low_cycles = []
        self.cs_asserted = self.cs_released = self.last_edge = None
        self._shift = 0

    def observe(self, cycle, pins, oe):
        """Sample master pins; update MISO only on CS assertion or falling SCLK."""
        if not self.driven and oe == 0:
            return self.miso
        assert oe == self.OE, f"SPI OE mismatch: {oe:#04x}; MISO must remain an input"
        assert pins & ~self.OE == 0, f"SPI unexpected output bits: {pins:#04x}"
        if not self.driven:
            assert pins == self.CS, "SPI must first drive deselected with SCLK LOW"
            self.driven = True
        clock = bool(pins & self.SCLK)
        old_clock = bool(self.previous & self.SCLK)
        selected = not bool(pins & self.CS)
        old_selected = not bool(self.previous & self.CS)
        if selected != old_selected:
            assert not clock and not old_clock, "SPI CS changed while SCLK was HIGH"
            if selected:
                assert not self.active and not self.complete, "SPI unexpected CS reassertion"
                self.active = True
                self.cs_asserted = self.last_edge = cycle
                self.miso = self.reply[0] >> 7
            else:
                assert self.active, "SPI CS released without a transaction"
                assert len(self.rising_edges) == len(self.falling_edges) == 8 * len(self.reply), (
                    "SPI CS released before all eight-bit transfers completed"
                )
                self.active = False
                self.complete = True
                self.cs_released = cycle
        if self.active:
            if clock and old_clock:
                assert (pins ^ self.previous) & self.MOSI == 0, "SPI MOSI changed during SCLK HIGH"
            if clock != old_clock:
                width = cycle - self.last_edge
                assert width == self.half_period, f"SPI half-period mismatch: {width} != {self.half_period}"
                self.last_edge = cycle
                if clock:
                    assert (pins ^ self.previous) & self.MOSI == 0, "SPI MOSI changed on the sampling edge"
                    self.low_cycles.append(width)
                    self.rising_edges.append(cycle)
                    assert len(self.rising_edges) <= 8 * len(self.reply), "SPI extra clock pulse"
                    self._shift = (self._shift << 1) | int(bool(pins & self.MOSI))
                    if len(self.rising_edges) % 8 == 0:
                        self.received.append(self._shift)
                        self._shift = 0
                else:
                    self.high_cycles.append(width)
                    self.falling_edges.append(cycle)
                    bit = len(self.rising_edges)
                    self.miso = ((self.reply[bit // 8] >> (7 - bit % 8)) & 1
                                 if bit < 8 * len(self.reply) else 0)
        elif not selected:
            assert not clock, "SPI SCLK must idle LOW outside CS"
        self.previous = pins
        return self.miso
