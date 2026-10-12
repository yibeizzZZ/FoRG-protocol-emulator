import importlib.util
from pathlib import Path
import unittest
import hashlib
import subprocess
import sys


class I2CMasterGeneratorTests(unittest.TestCase):
    def test_standard_output_frozen_across_supported_range(self):
        module = self.module()
        digest = hashlib.sha256()
        for delay in range(250, 4097):
            for polls in (1, 255):
                words = module.generate_i2c_master(half_period_cycles=delay, stretch_polls=polls)
                digest.update(bytes.fromhex(''.join(f'{word:04x}' for word in words)))
        # Captured from the unmodified Standard generator before Fast-mode work.
        self.assertEqual(digest.hexdigest(), '5b6526732495a3e168eaf9db839e0465333d541e918839167f623c3cbba324dd')
        image = Path(__file__).parents[1] / 'firmware/i2c_master.hex'
        self.assertEqual(module.generate_i2c_master(), [int(w, 16) for w in image.read_text().split()])

    def test_separate_fast_profile_and_bounds(self):
        module = self.module()
        for polls in (1, 2, 255):
            self.assertEqual(len(module.generate_i2c_master(mode='fast', stretch_polls=polls)), 127)
        self.assertEqual(module.generate_i2c_master(mode='standard'), module.generate_i2c_master())
        for options in ({'mode':'FAST'}, {'mode':True}, {'mode':'fast', 'half_period_cycles':250},
                        {'mode':'fast', 'stretch_polls':0}, {'mode':'fast', 'stretch_polls':True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                module.generate_i2c_master(**options)

    def test_cli_profiles(self):
        module = self.module()
        script = Path(__file__).with_name('i2c_master.py')
        for args, options in (([], {}), (['--mode', 'standard'], {}),
                              (['--mode', 'fast'], {'mode':'fast'})):
            result = subprocess.run([sys.executable, '-B', str(script), *args],
                                    text=True, capture_output=True, check=True)
            self.assertEqual([int(w, 16) for w in result.stdout.split()], module.generate_i2c_master(**options))
        result = subprocess.run([sys.executable, '-B', str(script), '--mode', 'fast', '--half-period', '62'],
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('only configurable in Standard', result.stderr)

    def test_timing_checker_exact_boundary_precision(self):
        path = Path(__file__).parents[1] / 'test/i2c_fast_timing.py'
        spec = importlib.util.spec_from_file_location('i2c_fast_timing', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows = [(2000,0,1), (2600,0,0), (3000,1,0), (3900,1,1),
                (5200,1,0), (5600,0,0), (6500,0,1), (7100,1,1)]
        monitor = module.FastTiming(100000.001)
        for t, sda, scl in rows:
            monitor.observe(t + 100000.001, sda, scl)
        self.assertEqual(monitor.summary()['low_ns']['min'], 1300)
        monitor = module.FastTiming()
        for row in rows[:3]:
            monitor.observe(*row)
        with self.assertRaisesRegex(AssertionError, 'low_ns'):
            monitor.observe(3899.999, 1, 1)  # One picosecond short must fail.

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
