"""Pin-only SPI slave/monitor: no generator, ISA, PC or register dependencies."""


class SpiSlave:
    # Board-level test wiring, deliberately not imported from the generator.
    MOSI, MISO, SCLK, CS, OE = 0x80, 0x01, 0x02, 0x04, 0x86

    def __init__(self, reply, half_period_cycles, mode=0, *, sample_hold_cycles=None,
                 response_delay_cycles=0):
        assert type(mode) is int and 0 <= mode <= 3
        self.mode = mode
        self.cpol, self.cpha = mode >> 1, mode & 1
        self.reply = list(reply)
        assert self.reply and all(0 <= byte <= 255 for byte in self.reply)
        self.half_period = half_period_cycles
        # Optional adversarial slave: hold through the specified post-sample
        # interval, then invalidate data until the next protocol setup edge.
        self.sample_hold_cycles = sample_hold_cycles
        assert 0 <= response_delay_cycles < half_period_cycles
        self.response_delay_cycles = response_delay_cycles
        self.pending_reply = None
        self.driven = self.active = self.complete = False
        self.previous = self.CS | (self.SCLK if self.cpol else 0)
        self.miso = 0
        self.received = []
        self.rising_edges, self.falling_edges = [], []
        self.leading_edges, self.trailing_edges, self.sample_edges = [], [], []
        self.high_cycles, self.low_cycles, self.setup_cycles = [], [], []
        self.cs_asserted = self.cs_released = self.last_edge = None
        self.last_mosi_change = None
        self._shift = 0

    def _reply_bit(self, bit):
        return ((self.reply[bit // 8] >> (7 - bit % 8)) & 1
                if bit < 8 * len(self.reply) else 0)

    def _launch_reply(self, cycle, bit):
        value = self._reply_bit(bit)
        self.miso = 1 - value if self.response_delay_cycles else value
        self.pending_reply = (cycle + self.response_delay_cycles, value)

    def observe(self, cycle, pins, oe):
        """Decode sample edges; launch replies on setup edges from observed pins."""
        if not self.driven and oe == 0:
            return self.miso
        assert oe == self.OE, f"SPI OE mismatch: {oe:#04x}; MISO must remain an input"
        assert pins & ~self.OE == 0, f"SPI unexpected output bits: {pins:#04x}"
        if not self.driven:
            assert pins == self.previous, "SPI must first drive deselected at CPOL with MOSI LOW"
            self.driven = True
            self.last_mosi_change = cycle
        clock = int(bool(pins & self.SCLK))
        old_clock = int(bool(self.previous & self.SCLK))
        selected, old_selected = not bool(pins & self.CS), not bool(self.previous & self.CS)
        edge = clock != old_clock
        leading = edge and clock != self.cpol
        mosi_changed = bool((pins ^ self.previous) & self.MOSI)
        if mosi_changed:
            self.last_mosi_change = cycle
        if selected != old_selected:
            assert clock == old_clock == self.cpol, "SPI CS changed away from idle SCLK"
            if selected:
                assert not self.active and not self.complete, "SPI unexpected CS reassertion"
                self.active = True
                self.cs_asserted = self.last_edge = cycle
                if not self.cpha:
                    self._launch_reply(cycle, 0)
                else:
                    self.miso = 1 - self._reply_bit(0)
            else:
                assert self.active, "SPI CS released without a transaction"
                assert len(self.leading_edges) == len(self.trailing_edges) == len(self.sample_edges) == 8 * len(self.reply), (
                    "SPI CS released before all eight-bit transfers completed"
                )
                self.active, self.complete = False, True
                self.cs_released = cycle
        if self.active:
            if self.cpha:
                assert not mosi_changed or leading, "SPI CPHA=1 MOSI changed outside leading edge"
            elif clock != self.cpol and old_clock != self.cpol:
                assert not mosi_changed, "SPI MOSI changed during sample hold phase"
            if edge:
                width = cycle - self.last_edge
                assert width == self.half_period, f"SPI half-period mismatch: {width} != {self.half_period}"
                self.last_edge = cycle
                (self.rising_edges if clock else self.falling_edges).append(cycle)
                (self.low_cycles if clock else self.high_cycles).append(width)
                (self.leading_edges if leading else self.trailing_edges).append(cycle)
                assert len(self.leading_edges) <= 8 * len(self.reply), "SPI extra clock pulse"
                if leading != bool(self.cpha):
                    assert not mosi_changed, "SPI MOSI changed on the sampling edge"
                    setup = cycle - self.last_mosi_change
                    assert setup >= (self.half_period if self.cpha else 2), "SPI insufficient MOSI setup"
                    self.setup_cycles.append(setup)
                    self.sample_edges.append(cycle)
                    self._shift = (self._shift << 1) | int(bool(pins & self.MOSI))
                    if len(self.sample_edges) % 8 == 0:
                        self.received.append(self._shift)
                        self._shift = 0
                else:
                    self._launch_reply(cycle, len(self.sample_edges))
            if self.pending_reply is not None and cycle >= self.pending_reply[0]:
                self.miso = self.pending_reply[1]
                self.pending_reply = None
            if (self.sample_hold_cycles is not None and self.sample_edges and
                    cycle == self.sample_edges[-1] + self.sample_hold_cycles):
                self.miso = 1 - self._reply_bit(len(self.sample_edges) - 1)
        elif not selected:
            assert clock == self.cpol, "SPI SCLK must idle at CPOL outside CS"
        self.previous = pins
        return self.miso


class Mode0Slave(SpiSlave):
    """Backward-compatible entry point used by loader integration tests."""

    def __init__(self, reply, half_period_cycles):
        super().__init__(reply, half_period_cycles, mode=0)
