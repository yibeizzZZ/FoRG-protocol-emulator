"""IEEE 1149.1 TAP controller model with IDCODE, BYPASS and a user data register."""

from ..chip import Device

_NEXT = {
    "TLR": ("RTI", "TLR"),
    "RTI": ("RTI", "SELDR"),
    "SELDR": ("CAPDR", "SELIR"),
    "CAPDR": ("SHDR", "EX1DR"),
    "SHDR": ("SHDR", "EX1DR"),
    "EX1DR": ("PADR", "UPDR"),
    "PADR": ("PADR", "EX2DR"),
    "EX2DR": ("SHDR", "UPDR"),
    "UPDR": ("RTI", "SELDR"),
    "SELIR": ("CAPIR", "TLR"),
    "CAPIR": ("SHIR", "EX1IR"),
    "SHIR": ("SHIR", "EX1IR"),
    "EX1IR": ("PAIR", "UPIR"),
    "PAIR": ("PAIR", "EX2IR"),
    "EX2IR": ("SHIR", "UPIR"),
    "UPIR": ("RTI", "SELDR"),
}

IR_LEN = 4
IR_IDCODE = 0b0001
IR_USER = 0b0010
IR_BYPASS = 0b1111


class JtagTap(Device):
    def __init__(self, tck, tms, tdi, tdo, idcode=0x1BADC0DE | 1, user_len=16):
        super().__init__()
        self.tck, self.tms, self.tdi, self.tdo = tck, tms, tdi, tdo
        self.idcode = idcode
        self.user_len = user_len
        self.user = 0
        self.state = "TLR"
        self.ir = IR_IDCODE
        self.prev_tck = 0
        self.shift = 0
        self.length = 0
        self.history = []
        self.drive(tdo, 1)

    def _dr(self):
        if self.ir == IR_IDCODE:
            return self.idcode, 32
        if self.ir == IR_USER:
            return self.user, self.user_len
        return 0, 1

    def step(self, chip, cycle, nets):
        tck = (nets >> self.tck) & 1
        if tck and not self.prev_tck:
            tms = (nets >> self.tms) & 1
            tdi = (nets >> self.tdi) & 1
            st = self.state
            if st in ("SHDR", "SHIR"):
                self.shift = (self.shift >> 1) | (tdi << (self.length - 1))
            nxt = _NEXT[st][tms]
            if nxt == "TLR":
                self.ir = IR_IDCODE
            if nxt == "CAPDR":
                self.shift, self.length = self._dr()
            elif nxt == "CAPIR":
                self.shift, self.length = 0b0101, IR_LEN
            elif nxt == "UPIR":
                self.ir = self.shift & ((1 << IR_LEN) - 1)
            elif nxt == "UPDR" and self.ir == IR_USER:
                self.user = self.shift & ((1 << self.user_len) - 1)
            if nxt != st:
                self.history.append(nxt)
            self.state = nxt
        elif not tck and self.prev_tck:
            if self.state in ("SHDR", "SHIR"):
                self.drive(self.tdo, self.shift & 1)
        self.prev_tck = tck
