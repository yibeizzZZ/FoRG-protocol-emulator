"""Executable host/assembler contracts for the opt-in extended PIO mode."""
import unittest
from pio_firmware import Program


class ExtendedAssemblerTests(unittest.TestCase):
    def program(self):
        try:
            return Program(extended=True)
        except TypeError:
            self.fail('Assembler must support opt-in extended programs')

    def test_upper_address_and_call_encoding(self):
        p = self.program()
        p.branch('CALL', 'upper')
        for _ in range(126):
            p.emit('NOP')
        p.label('upper')
        p.emit('RET')
        self.assertEqual(p.assemble()[0], 0xEA7F)
        self.assertEqual(p.assemble()[127], 0xEB00)
        with self.assertRaises(ValueError):
            p.emit('NOP')

    def test_long_wait_and_gpio_data_instructions(self):
        p = self.program()
        p.wait(4096)
        p.emit('OESET', imm=2)
        p.emit('OECLR', imm=1)
        p.emit('LDA', rd=3, imm=31)
        p.emit('STA', rd=2, imm=16)
        p.emit('LDB', rd=1, rs=2)
        p.emit('STB', rd=3, rs=0)
        p.emit('OR', rd=2, rs=1)
        p.emit('DIRR', rd=3)
        p.emit('INBIT', rd=2, imm=7)
        self.assertEqual(p.assemble(), [0x1FFF,0xE402,0xE501,0xE6DF,0xE790,
                                         0xE806,0xE90C,0xEC09,0xED03,0xFC17])

    def test_extended_nop_and_wait_cannot_alias_other_operations(self):
        for name in ('NOP', 'WAIT'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.program().emit(name, rd=2)

    def test_operand_bounds_and_legacy_rejection(self):
        for name, operands in [('LDA',dict(imm=32)),('STA',dict(imm=255)),
                               ('INBIT',dict(imm=8)),('CALL',dict(imm=128))]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.program().emit(name, **operands)
        with self.assertRaises(ValueError):
            Program().emit('OESET', imm=1)
        with self.assertRaises(ValueError):
            Program().branch('CALL', 'target')


if __name__ == '__main__':
    unittest.main()
