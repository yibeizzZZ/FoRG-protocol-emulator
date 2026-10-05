"""SPI target (peripheral) bus-functional model, all four modes, MSB first."""

from ..chip import Device


class SpiTarget(Device):
    def __init__(self, sck, mosi, miso, cs, cpol=0, cpha=0, respond=None):
        super().__init__()
        self.sck, self.mosi, self.miso, self.cs = sck, mosi, miso, cs
        self.cpol, self.cpha = cpol, cpha
        self.respond = respond or (lambda idx, rx: (0xA5 + idx) & 0xFF)
        self.transactions = []
        self.mode_errors = 0
        self.prev_sck = None
        self.prev_cs = 1
        self.active = False

    def _load_next(self):
        self.tx_byte = self.respond(len(self.rx), bytes(self.rx)) & 0xFF
        self.tx_bit = 7

    def _drive_bit(self):
        self.drive(self.miso, (self.tx_byte >> self.tx_bit) & 1)

    def step(self, chip, cycle, nets):
        sck = (nets >> self.sck) & 1
        cs = (nets >> self.cs) & 1
        if self.prev_sck is None:
            self.prev_sck = sck
        if self.prev_cs == 1 and cs == 0:
            if sck != self.cpol:
                self.mode_errors += 1
            self.active = True
            self.rx = bytearray()
            self.shift = 0
            self.nbits = 0
            self._load_next()
            if self.cpha == 0:
                self._drive_bit()
        elif self.prev_cs == 0 and cs == 1:
            if self.active:
                self.transactions.append(bytes(self.rx))
            self.active = False
            self.drive(self.miso, None)
        elif self.active and sck != self.prev_sck:
            leading = sck != self.cpol
            sample_edge = leading if self.cpha == 0 else not leading
            if sample_edge:
                self.shift = ((self.shift << 1) | ((nets >> self.mosi) & 1)) & 0xFF
                self.nbits += 1
                if self.nbits % 8 == 0:
                    self.rx.append(self.shift)
            else:
                if self.cpha == 0:
                    if self.nbits % 8 == 0:
                        self._load_next()
                    else:
                        self.tx_bit -= 1
                else:
                    if self.nbits % 8 == 0 and self.nbits:
                        self._load_next()
                    elif self.nbits:
                        self.tx_bit -= 1
                self._drive_bit()
        self.prev_sck = sck
        self.prev_cs = cs
