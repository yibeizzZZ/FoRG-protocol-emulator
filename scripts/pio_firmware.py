"""Small M2 instruction builder shared by protocol firmware generators.

This module handles ISA encoding, labels and cycle-counted delays only.
Protocol state machines, pin assignments, timing policy and data stay in callers.
"""


OPCODES = {
    "SET": 0x0, "WAIT": 0x1, "JMP": 0x2, "READ": 0x3,
    "MOVI": 0x4, "MOV": 0x5, "JNZ": 0x6, "SHR": 0x7,
    "SHL": 0x8, "OUT": 0x9, "DIR": 0xA, "ANDI": 0xB,
    "ADDI": 0xC, "HALT": 0xD, "NOP": 0xF,
}


class Program:
    def __init__(self, *, extended=False):
        self.extended = extended
        self.capacity = 128 if extended else 32
        self._words = []
        self._labels = {}
        self._branches = []

    def emit(self, mnemonic, rd=0, rs=0, imm=0):
        extra = {'JZ', 'OESET', 'OECLR', 'LDA', 'STA', 'LDB', 'STB',
                 'CALL', 'RET', 'OR', 'DIRR', 'DJNZ', 'INBIT'}
        if not isinstance(mnemonic, str) or mnemonic not in (set(OPCODES) | extra):
            raise ValueError(f"unknown instruction: {mnemonic!r}")
        for name, value, limit in (("rd", rd, 3), ("rs", rs, 3), ("imm", imm, 255)):
            if type(value) is not int or not 0 <= value <= limit:
                raise ValueError(f"{name} must be an integer in 0..{limit}")
        if self.extended and mnemonic in ('NOP', 'WAIT') and (rd or rs):
            raise ValueError("extended NOP/WAIT cannot carry register operands; use wait() for long delays")
        if mnemonic in extra and not self.extended:
            raise ValueError("instruction requires extended PIO mode")
        if mnemonic in ('JMP', 'JNZ', 'JZ', 'CALL', 'DJNZ') and imm >= self.capacity:
            raise ValueError("branch address exceeds program memory")
        if len(self._words) >= self.capacity:
            raise ValueError(f"program exceeds the {self.capacity}-word instruction memory")
        if mnemonic in ('LDA', 'STA'):
            if imm >= 32:
                raise ValueError("data address must be in 0..31")
            word = (0xE600 if mnemonic == 'LDA' else 0xE700) | (rd << 6) | imm
        elif mnemonic in ('LDB', 'STB', 'OR'):
            word = {'LDB': 0xE800, 'STB': 0xE900, 'OR': 0xEC00}[mnemonic] | (rd << 2) | rs
        elif mnemonic == 'DIRR':
            word = 0xED00 | rd
        elif mnemonic == 'INBIT':
            if imm >= 8:
                raise ValueError("input pin must be in 0..7")
            word = 0xFC00 | (rd << 3) | imm
        elif mnemonic == 'JZ':
            word = 0xE000 | (rd << 8) | imm
        elif mnemonic == 'DJNZ':
            word = 0xF800 | (rd << 8) | imm
        elif mnemonic in ('OESET', 'OECLR', 'CALL', 'RET'):
            word = {'OESET':0xE400, 'OECLR':0xE500, 'CALL':0xEA00, 'RET':0xEB00}[mnemonic] | imm
        else:
            word = (OPCODES[mnemonic] << 12) | (rd << 10) | (rs << 8) | imm
        self._words.append(word)

    def label(self, name):
        if name in self._labels:
            raise ValueError(f"duplicate label: {name}")
        self._labels[name] = len(self._words)

    def branch(self, mnemonic, target, rd=0):
        if mnemonic not in ("JMP", "JNZ", "JZ", "DJNZ", "CALL"):
            raise ValueError("invalid labeled branch instruction")
        self.emit(mnemonic, rd=rd)
        self._branches.append((len(self._words) - 1, target))

    def wait(self, cycles):
        """Emit a delay of exactly cycles, including WAIT issue cycles."""
        if type(cycles) is not int or cycles < 0:
            raise ValueError("delay must be a nonnegative integer cycle count")
        limit = 4096 if self.extended else 256
        if len(self._words) + (cycles + limit - 1) // limit > self.capacity:
            raise ValueError(f"delay exceeds the {self.capacity}-word instruction memory")
        while cycles:
            chunk = min(cycles, limit)
            self._words.append(0x1000 | (chunk - 1))
            cycles -= chunk

    def assemble(self):
        words = self._words.copy()
        for address, target in self._branches:
            if target not in self._labels:
                raise ValueError(f"undefined label: {target}")
            destination = self._labels[target]
            if not 0 <= destination < len(words):
                raise ValueError(f"branch target has no instruction: {target}")
            words[address] |= destination
        return words
