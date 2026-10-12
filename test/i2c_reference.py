"""Edge-driven address-write target and Standard-mode digital timing monitor.

No instruction words, PCs, register state, or generator timing expressions enter
this model. Only resolved bus edges and the master's externally visible OE/data
are observed. Pull-ups and propagation delays live in the Verilog testbench.
"""


class AddressProbeSlave:
    def __init__(self, address, *, acknowledge=True, stretches=None):
        self.address = address
        self.acknowledge = acknowledge
        self.stretches = stretches or {}   # Low phase number (1..10) -> hold cycles
        self.sda_low = False
        self.scl_low = False
        self.previous = (1, 1)
        self.start = None
        self.stop = None
        self.bits = []
        self.ack = None
        self.falling_edges = []
        self.rising_edges = []
        self.low_cycles = []
        self.high_cycles = []
        self.last_sda_change = -1000
        self._sda_event = None
        self._scl_release = None

    @property
    def byte(self):
        value = 0
        for bit in self.bits:
            value = (value << 1) | bit
        return value

    @property
    def complete(self):
        return self.stop is not None

    def observe(self, cycle, sda, scl, outputs, oe):
        assert outputs & oe == 0, 'I2C actively drove HIGH'
        assert oe & ~3 == 0, 'I2C drove an unrelated pin'
        old_sda, old_scl = self.previous
        if sda != old_sda:
            if old_scl and scl:
                if not sda:
                    assert self.start is None, 'Unexpected or repeated START'
                    self.start = cycle
                else:
                    assert self.start is not None and len(self.bits) == 8 and self.ack is not None, 'Premature STOP'
                    assert len(self.rising_edges) == 10, 'STOP must follow ninth ACK clock and SCL release'
                    assert cycle - self.rising_edges[-1] >= 200, 'STOP setup shorter than 4 us'
                    self.stop = cycle
            elif self.start is not None and not self.complete:
                assert not (old_scl and not scl), 'SDA changed on SCL falling edge'
                assert not scl, 'SDA changed on SCL rising edge'
                assert cycle > self.falling_edges[-1], 'SDA hold violated'
            self.last_sda_change = cycle
        if self.start is not None and not self.complete:
            if old_scl and not scl:
                if not self.falling_edges:
                    assert cycle - self.start >= 200, 'START hold shorter than 4 us'
                else:
                    high = cycle - self.rising_edges[-1]
                    assert high >= 200, 'SCL HIGH shorter than 4 us'
                    self.high_cycles.append(high)
                self.falling_edges.append(cycle)
                phase = len(self.falling_edges)
                assert phase <= 10, 'Extra I2C clock'
                if phase == 9:
                    accept = self.byte == self.address << 1 and self.acknowledge
                    self._sda_event = (cycle + 20, accept)
                elif phase == 10:
                    self._sda_event = (cycle + 20, False)
                if phase in self.stretches:
                    self.scl_low = True
                    self._scl_release = cycle + self.stretches[phase]
            elif not old_scl and scl:
                low = cycle - self.falling_edges[-1]
                assert low >= 235, 'SCL LOW shorter than 4.7 us'
                assert cycle - self.last_sda_change >= 13, 'SDA setup shorter than 250 ns'
                if self.rising_edges:
                    assert cycle - self.rising_edges[-1] >= 500, 'SCL exceeds 100 kHz'
                self.low_cycles.append(low)
                self.rising_edges.append(cycle)
                if len(self.bits) < 8:
                    self.bits.append(sda)
                elif self.ack is None:
                    assert oe & 1 == 0, 'Master did not release SDA for ACK'
                    self.ack = sda
                else:
                    assert sda == 0, 'STOP preparation must hold SDA LOW'
            # Require release throughout ACK until the master starts pulling SCL
            # LOW. During physical SCL fall, preserving an observed ACK=0 can
            # overlap the target's LOW drive without changing the resolved SDA.
            if scl and len(self.rising_edges) == 9 and not (oe & 2):
                assert oe & 1 == 0, 'Master drove SDA during ACK'
        if self._sda_event and cycle >= self._sda_event[0]:
            self.sda_low = self._sda_event[1]
            self._sda_event = None
        if self._scl_release is not None and cycle >= self._scl_release:
            self.scl_low = False
            self._scl_release = None
        self.previous = sda, scl
        return self.sda_low, self.scl_low
