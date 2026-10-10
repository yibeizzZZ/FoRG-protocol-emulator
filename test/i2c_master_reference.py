"""Independent edge-driven I2C memory target and Standard-mode timing monitor.

The model consumes only resolved SDA/SCL and external master output enables.
It has no knowledge of program words, instruction addresses, or PIO registers.
One model cycle is 20 ns. The first write byte selects a register, subsequent
bytes write and increment it; reads return and increment the current register.
"""


class I2CMemorySlave:
    def __init__(self, address, *, memory=None, pointer=0, acknowledge=True,
                 acknowledge_read=True, nack_data=None, stretches=None,
                 stretch_all=0, hold_sda_after_ack=False):
        self.address = address
        self.memory = bytearray(range(256)) if memory is None else bytearray(memory)
        assert len(self.memory) == 256
        self.pointer = pointer
        self.acknowledge = acknowledge
        self.acknowledge_read = acknowledge_read
        self.nack_data = nack_data
        self.stretches = stretches or {}
        self.stretch_all = stretch_all
        self.hold_sda_after_ack = hold_sda_after_ack
        self.previous = (1, 1)
        self.sda_low = self.scl_low = False
        self.segments = []
        self.starts = []
        self.stops = []
        self.falling_edges = []
        self.rising_edges = []
        self.low_cycles = []
        self.high_cycles = []
        self.last_sda_change = -1000
        self.last_start = None
        self._first_fall = False
        self._active = False
        self._state = 'idle'
        self._bits = []
        self._segment = None
        self._sda_event = self._scl_release = None
        self._ack = False
        self._write_index = 0
        self._tx_byte = 0
        self._tx_index = 0
        self._wait_rises = 0

    @property
    def complete(self):
        return bool(self.stops)

    def _drive(self, cycle, low):
        # Target changes data 400 ns after an observed falling edge.
        self._sda_event = (cycle + 20, bool(low))

    def _receive_byte(self):
        value = 0
        for bit in self._bits:
            value = (value << 1) | bit
        self._bits = []
        if self._segment is None:
            read = bool(value & 1)
            self._ack = (value >> 1 == self.address and self.acknowledge
                         and (not read or self.acknowledge_read))
            self._segment = dict(address=value >> 1, read=read, address_byte=value,
                                 address_ack=int(not self._ack), bytes=[], acks=[],
                                 master_acks=[])
            self.segments.append(self._segment)
            self._write_index = 0
        else:
            self._ack = self._write_index != self.nack_data
            self._segment['bytes'].append(value)
            self._segment['acks'].append(int(not self._ack))
            if self._ack:
                if self._write_index == 0:
                    self.pointer = value
                else:
                    self.memory[self.pointer] = value
                    self.pointer = (self.pointer + 1) & 255
            self._write_index += 1
        self._state = 'rx_ack_setup'

    def _begin_tx(self, cycle):
        self._tx_byte = self.memory[self.pointer]
        self.pointer = (self.pointer + 1) & 255
        self._tx_index = 0
        self._state = 'tx'
        self._drive(cycle, not (self._tx_byte & 0x80))

    def observe(self, cycle, sda, scl, outputs, oe):
        assert outputs & oe == 0, 'I2C actively drove HIGH'
        assert oe & ~3 == 0, 'I2C drove an unrelated pin'
        old_sda, old_scl = self.previous
        if sda != old_sda:
            if old_scl and scl:
                if not sda:
                    if self._active:
                        assert self._state in ('rx', 'wait') and len(self._bits) <= 1, 'Repeated START interrupted a byte'
                        assert self.rising_edges and cycle - self.rising_edges[-1] >= 235, 'Repeated START setup shorter than 4.7 us'
                    self.starts.append(cycle)
                    self.last_start = cycle
                    self._first_fall = True
                    self._active = True
                    self._state = 'rx'
                    self._bits = []
                    self._segment = None
                else:
                    assert self._active and self._segment is not None, 'Unexpected STOP'
                    assert self._state in ('rx', 'wait') and len(self._bits) <= 1, 'STOP interrupted a byte'
                    assert cycle - self.rising_edges[-1] >= 200, 'STOP setup shorter than 4 us'
                    self.stops.append(cycle)
                    self._active = False
                    self._state = 'idle'
            elif self._active:
                assert not (old_scl and not scl), 'SDA changed on SCL falling edge'
                assert not scl, 'SDA changed on SCL rising edge'
                assert self.falling_edges and cycle > self.falling_edges[-1], 'SDA hold violated'
            self.last_sda_change = cycle
        if self._active:
            if old_scl and not scl:
                if self._first_fall:
                    assert cycle - self.last_start >= 200, 'START hold shorter than 4 us'
                    self._first_fall = False
                else:
                    high = cycle - self.rising_edges[-1]
                    assert high >= 200, 'SCL HIGH shorter than 4 us'
                    self.high_cycles.append(high)
                self.falling_edges.append(cycle)
                duration = self.stretches.get(len(self.falling_edges), self.stretch_all)
                if duration:
                    self.scl_low = True
                    self._scl_release = cycle + duration
                if self._state == 'rx_ack_setup':
                    self._drive(cycle, self._ack)
                    self._state = 'rx_ack'
                elif self._state == 'rx_ack_done':
                    if not self._ack:
                        self._drive(cycle, False)
                        self._state = 'wait'
                        self._wait_rises = 0
                    elif self._segment['read']:
                        self._begin_tx(cycle)
                    else:
                        self._drive(cycle, self.hold_sda_after_ack)
                        self._state = 'rx'
                elif self._state == 'tx':
                    self._drive(cycle, not ((self._tx_byte >> (7 - self._tx_index)) & 1))
                elif self._state == 'tx_ack_setup':
                    self._drive(cycle, False)
                    self._state = 'tx_ack'
                elif self._state == 'tx_ack_done':
                    if self._segment['master_acks'][-1]:
                        self._state = 'wait'
                        self._wait_rises = 0
                        self._drive(cycle, False)
                    else:
                        self._begin_tx(cycle)
            elif not old_scl and scl:
                assert self.falling_edges, 'SCL rose before START hold'
                low = cycle - self.falling_edges[-1]
                assert low >= 235, 'SCL LOW shorter than 4.7 us'
                assert cycle - self.last_sda_change >= 13, 'SDA setup shorter than 250 ns'
                if self.rising_edges:
                    assert cycle - self.rising_edges[-1] >= 500, 'SCL exceeds 100 kHz'
                self.low_cycles.append(low)
                self.rising_edges.append(cycle)
                if self._state == 'rx':
                    self._bits.append(sda)
                    if len(self._bits) == 8:
                        self._receive_byte()
                elif self._state == 'rx_ack':
                    assert oe & 1 == 0, 'Master did not release SDA for target ACK'
                    assert sda == int(not self._ack), 'Target ACK was corrupted'
                    self._state = 'rx_ack_done'
                elif self._state == 'tx':
                    assert oe & 1 == 0, 'Master drove SDA during target data'
                    expected = (self._tx_byte >> (7 - self._tx_index)) & 1
                    assert sda == expected, 'Read data corrupted on resolved bus'
                    self._tx_index += 1
                    if self._tx_index == 8:
                        self._segment['bytes'].append(self._tx_byte)
                        self._state = 'tx_ack_setup'
                elif self._state == 'tx_ack':
                    self._segment['master_acks'].append(sda)
                    self._state = 'tx_ack_done'
                elif self._state == 'wait':
                    self._wait_rises += 1
                    assert self._wait_rises <= 1, 'Extra clocks after NACK before STOP or repeated START'
            if scl and not (oe & 2) and self._state in ('rx_ack_done', 'tx', 'tx_ack_setup'):
                assert oe & 1 == 0, 'Master drove SDA during target-owned HIGH phase'
        if self._sda_event is not None and cycle >= self._sda_event[0]:
            self.sda_low = self._sda_event[1]
            self._sda_event = None
        if self._scl_release is not None and cycle >= self._scl_release:
            self.scl_low = False
            self._scl_release = None
        self.previous = sda, scl
        return self.sda_low, self.scl_low

    def check(self, address, write=b'', read=b'', *, status=2):
        """Compare the externally decoded transaction with a caller's contract."""
        expected_segments = int(bool(write)) + int(bool(read))
        if status in (3, 4):
            expected_segments = 1
        assert len(self.segments) == expected_segments, 'Wrong number of address phases'
        assert len(self.starts) == expected_segments, 'Missing or extra START/repeated START'
        assert len(self.stops) == 1, 'Transaction must end with exactly one STOP'
        for segment in self.segments:
            assert segment['address'] == address, 'Wrong slave address'
        if write:
            segment = self.segments[0]
            assert not segment['read'], 'Write address R/W bit was set'
            assert segment['address_ack'] == int(status == 3), 'Wrong write address ACK'
            count = 0 if status == 3 else (self.nack_data + 1 if status == 4 else len(write))
            assert segment['bytes'] == list(write[:count]), 'Write bytes differ from request'
            assert segment['acks'] == ([0] * (count - 1) + [1] if status == 4 else [0] * count), 'Wrong data ACKs'
        if read and status not in (3, 4):
            segment = self.segments[-1]
            assert segment['read'], 'Read address R/W bit was clear'
            assert segment['address_ack'] == int(status == 5), 'Wrong read address ACK'
            if status == 5:
                assert segment['bytes'] == [], 'Read proceeded after address NACK'
            else:
                assert segment['bytes'] == list(read), 'Read bytes differ from target memory'
                assert segment['master_acks'] == [0] * (len(read) - 1) + [1], 'Master must ACK each read byte except final NACK'
