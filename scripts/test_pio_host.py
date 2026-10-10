import importlib.util
from pathlib import Path
import unittest


class PIOHostTests(unittest.TestCase):
    def api(self):
        path = Path(__file__).with_name('pio_host.py')
        self.assertTrue(path.exists(), 'UI-only host codec missing')
        spec = importlib.util.spec_from_file_location('pio_host', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def decode(self, controls):
        packets, value, n, previous = [], 0, 0, 0
        for control in controls:
            self.assertLess(control, 128, 'Loading must keep RUN clear')
            if control == 0x1e:
                value, n, previous = 0, 0, 0
            elif control & 0x60 == 0x20 and control & 0x10 != previous:
                previous = control & 0x10
                value = (value << 4) | (control & 15)
                n += 1
                if n == 6:
                    packets.append((value >> 16, value & 65535))
                    n, value = 0, 0
        self.assertEqual(n, 0)
        return packets

    def test_program_pages_and_mode(self):
        api = self.api()
        words = [0x4567 ^ i for i in range(128)]
        self.assertEqual(self.decode(api.program_writes(words, extended=True)),
                         [(255, 1)] + list(enumerate(words)))
        self.assertEqual(self.decode(api.program_writes([0xD000])), [(255,0),(0,0xD000)])

    def test_data_and_invalid_inputs(self):
        api = self.api()
        self.assertEqual(self.decode(api.data_writes([0xFE, 0x27], offset=30)), [(158,254),(159,39)])
        for call in (lambda:api.program_writes([0]*33), lambda:api.data_writes([0,1],offset=31),
                     lambda:api.program_writes([65536]),lambda:api.data_writes([True]),
                     lambda:api.program_writes([])):
            with self.assertRaises(ValueError):
                call()


if __name__ == '__main__':
    unittest.main()
