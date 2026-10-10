import importlib.util
from pathlib import Path
import unittest


class I2CMasterGeneratorTests(unittest.TestCase):
    def module(self):
        path = Path(__file__).with_name('i2c_master.py')
        self.assertTrue(path.exists(), 'Full transaction firmware generator is missing')
        spec = importlib.util.spec_from_file_location('i2c_master', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_generic_code_and_separate_data(self):
        module = self.module()
        words = module.generate_i2c_master()
        self.assertLessEqual(len(words), 128)
        self.assertGreater(len(words), 32)
        for address in (0, 0x27, 0x50, 127):
            ram = module.transaction_data(address, write=[0x34, 0xAB], read_count=3)
            self.assertEqual(len(ram), 32)
            self.assertEqual(ram[2:0:-1], [0x34, 0xAB])
            self.assertEqual(ram[16:19], [address << 1, 2, 3])
            self.assertEqual(module.generate_i2c_master(), words)

    def test_bounds_and_empty_transaction(self):
        module = self.module()
        for kwargs in ({}, {'write':[0]*16}, {'read_count':16}, {'read_count':-1},
                       {'write':[256]}, {'write':[True]}, {'read_count':True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                module.transaction_data(0x50, **kwargs)
        for address in (-1, 128, True):
            with self.assertRaises(ValueError):
                module.transaction_data(address, read_count=1)
        for half_period in (249, True, 4097):
            with self.assertRaises(ValueError):
                module.generate_i2c_master(half_period_cycles=half_period)
        for polls in (0, 256, True):
            with self.assertRaises(ValueError):
                module.generate_i2c_master(stretch_polls=polls)


if __name__ == '__main__':
    unittest.main()
