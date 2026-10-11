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
    def __init__(self):
        self._words = []
        self._labels = {}
        self._branches = []

    def emit(self, mnemonic, rd=0, rs=0, imm=0):
        if not isinstance(mnemonic, str) or mnemonic not in OPCODES:
            raise ValueError(f"unknown instruction: {mnemonic!r}")
        for name, value, limit in (("rd", rd, 3), ("rs", rs, 3), ("imm", imm, 255)):
            if type(value) is not int or not 0 <= value <= limit:
                raise ValueError(f"{name} must be an integer in 0..{limit}")
        if mnemonic in ("JMP", "JNZ") and imm >= 32:
            raise ValueError("branch address must fit the 5-bit PC")
        if len(self._words) >= 32:
            raise ValueError("program exceeds the 32-word M2 instruction memory")
        self._words.append((OPCODES[mnemonic] << 12) | (rd << 10) | (rs << 8) | imm)

    def label(self, name):
        if name in self._labels:
            raise ValueError(f"duplicate label: {name}")
        self._labels[name] = len(self._words)

    def branch(self, mnemonic, target, rd=0):
        if mnemonic not in ("JMP", "JNZ"):
            raise ValueError("a labeled branch must use JMP or JNZ")
        self.emit(mnemonic, rd=rd)
        self._branches.append((len(self._words) - 1, target))

    def wait(self, cycles):
        """Emit a delay of exactly cycles, including WAIT issue cycles."""
        if type(cycles) is not int or cycles < 0:
            raise ValueError("delay must be a nonnegative integer cycle count")
        if len(self._words) + (cycles + 255) // 256 > 32:
            raise ValueError("delay exceeds the 32-word M2 instruction memory")
        while cycles:
            chunk = min(cycles, 256)
            self.emit("WAIT", imm=chunk - 1)
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
