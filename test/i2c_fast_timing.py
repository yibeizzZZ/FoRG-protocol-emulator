"""Independent nanosecond bus-edge checker for UM10204 Fast-mode timing.

Only resolved SDA/SCL enter this oracle. No generator, ISA or DUT internals.
The provisional clock preceding START/STOP is a control phase, not a data bit;
validate data/ACK validity when the next falling edge confirms a real clock.
"""


class FastTiming:
    def __init__(self, now=0):
        # Integer picoseconds match the testbench resolution. Floating-point
        # subtraction must not turn an exact 1300 ns boundary into 1299.999...
        now = round(now * 1000)
        self.previous = (1, 1)
        self.active = False
        self.idle_since = now
        self.start = self.fall = self.rise = None
        self.last_change = now
        self.changes = []
        self.pending = None
        self.bit = 0
        self.values = {name: [] for name in (
            'low_ns', 'high_ns', 'period_ns', 'start_hold_ns', 'restart_setup_ns',
            'stop_setup_ns', 'setup_ns', 'hold_ns', 'data_valid_ns', 'ack_valid_ns',
            'bus_free_ns', 'byte_boundary_valid_ns')}

    def minimum(self, name, value, limit):
        assert value >= limit * 1000, f'{name}: {value / 1000} ns < {limit} ns'
        self.values[name].append(value / 1000)

    def observe(self, now, sda, scl):
        now = round(now * 1000)
        old_sda, old_scl = self.previous
        if (sda, scl) == self.previous:
            return
        assert not (sda != old_sda and scl != old_scl), 'SDA changed on a clock edge'
        if sda != old_sda:
            self.last_change = now
            if scl:
                if not sda:
                    if self.active:
                        self.minimum('restart_setup_ns', now - self.rise, 600)
                    else:
                        self.minimum('bus_free_ns', now - self.idle_since, 1300)
                    self.active = True
                    self.start = now
                    self.rise = self.fall = None
                    self.pending = None
                    self.bit = 0
                else:
                    assert self.active and self.rise is not None, 'Unexpected STOP'
                    self.minimum('stop_setup_ns', now - self.rise, 600)
                    self.active = False
                    self.idle_since = now
                    self.pending = None
            elif self.active:
                assert self.fall is not None, 'Data changed before START hold'
                self.minimum('hold_ns', now - self.fall, 0)
                self.changes.append(now)
        if self.active and scl != old_scl:
            if not scl:
                if self.rise is None:
                    self.minimum('start_hold_ns', now - self.start, 600)
                else:
                    self.minimum('high_ns', now - self.rise, 600)
                    if self.pending is not None:
                        valid = self.pending
                        name = 'ack_valid_ns' if self.bit % 9 == 8 else 'data_valid_ns'
                        assert valid <= 900000, f'{name}: {valid / 1000} ns > 900 ns'
                        self.values[name].append(valid / 1000)
                        if self.bit and self.bit % 9 == 0:
                            self.values['byte_boundary_valid_ns'].append(valid / 1000)
                        self.bit += 1
                self.fall = now
                self.changes = []
                self.pending = None
            else:
                assert self.fall is not None, 'Clock before START hold'
                self.minimum('low_ns', now - self.fall, 1300)
                self.minimum('setup_ns', now - self.last_change, 100)
                if self.rise is not None:
                    self.minimum('period_ns', now - self.rise, 2500)
                self.rise = now
                self.pending = self.changes[-1] - self.fall if self.changes else 0
        self.previous = sda, scl

    def summary(self):
        return {key: dict(min=min(values), max=max(values), count=len(values))
                for key, values in self.values.items() if values}
