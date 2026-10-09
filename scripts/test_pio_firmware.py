"""Contracts shared by protocol generators: encoding, labels and exact delays."""

import unittest

from pio_firmware import Program


class ProgramTests(unittest.TestCase):
    def test_encoding_and_forward_backward_labels(self):
        program = Program()
        program.label("start")
        program.emit("MOVI", rd=2, imm=0xAB)
        program.branch("JNZ", "end", rd=2)
        program.emit("MOV", rd=1, rs=3)
        program.branch("JMP", "start")
        program.label("end")
        program.emit("SET", imm=1)
        self.assertEqual(program.assemble(), [0x48AB, 0x6804, 0x5700, 0x2000, 0x0001])
        self.assertEqual(program.assemble(), [0x48AB, 0x6804, 0x5700, 0x2000, 0x0001])

    def test_wait_cycles_include_issue_and_split_at_256(self):
        for cycles, expected in ((0, []), (1, [0x1000]), (2, [0x1001]),
                                 (256, [0x10FF]), (257, [0x10FF, 0x1000]),
                                 (434, [0x10FF, 0x10B1])):
            with self.subTest(cycles=cycles):
                program = Program()
                program.wait(cycles)
                self.assertEqual(program.assemble(), expected)

    def test_rejects_invalid_operands_and_delays(self):
        for kwargs in ({"rd": -1}, {"rd": 4}, {"rs": 4}, {"imm": 256},
                       {"imm": -1}, {"imm": True}, {"rd": 1.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Program().emit("MOVI", **kwargs)
        with self.assertRaises(ValueError):
            Program().emit("NOT_AN_INSTRUCTION")
        with self.assertRaises(ValueError):
            Program().emit("JMP", imm=32)
        for cycles in (-1, 1.5, True):
            with self.subTest(cycles=cycles), self.assertRaises(ValueError):
                Program().wait(cycles)

    def test_rejects_bad_branches_and_duplicate_labels(self):
        program = Program()
        program.label("loop")
        with self.assertRaises(ValueError):
            program.label("loop")
        with self.assertRaises(ValueError):
            program.branch("MOVI", "loop")
        program.branch("JMP", "missing")
        with self.assertRaises(ValueError):
            program.assemble()

    def test_memory_limit_and_branch_target_must_be_loaded(self):
        program = Program()
        for _ in range(32):
            program.emit("NOP")
        self.assertEqual(len(program.assemble()), 32)
        with self.assertRaises(ValueError):
            program.emit("NOP")
        program = Program()
        program.branch("JMP", "past_end")
        program.label("past_end")
        with self.assertRaises(ValueError):
            program.assemble()
        with self.assertRaises(ValueError):
            Program().wait(32 * 256 + 1)


if __name__ == "__main__":
    unittest.main()
