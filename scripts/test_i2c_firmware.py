"""I2C probe generator bounds and the unchanged shared assembler contract."""
from pathlib import Path
import unittest

from i2c_firmware import generate_i2c_probe


class I2CFirmwareTests(unittest.TestCase):
    def test_all_addresses_and_timing_limits_fit(self):
        for address in range(128):
            for period in (250, 251, 260, 267):
                words = generate_i2c_probe(address, half_period_cycles=period)
                self.assertEqual(len(words), 32)
                self.assertEqual(words[0] & 255, ~(address << 1) & 255)
                # Open drain relies on the documented stopped-mode output latch=0.
                self.assertFalse(any(word >> 12 in (0, 9) for word in words))
                self.assertEqual(sum(word >> 12 == 8 for word in words), 1)
                self.assertEqual(sum(word >> 12 == 13 for word in words), 1)

    def test_only_configuration_and_address_change(self):
        baseline = generate_i2c_probe(0x50)
        for address in range(128):
            self.assertEqual(generate_i2c_probe(address)[1:], baseline[1:])
        slower = generate_i2c_probe(0x50, half_period_cycles=267)
        differences = [i for i, (a, b) in enumerate(zip(baseline, slower)) if a != b]
        self.assertEqual(len(differences), 3)
        self.assertTrue(all(baseline[i] >> 12 == 1 for i in differences))

    def test_invalid_configuration_rejected(self):
        for address in (-1, 128, True, 1.5, '0x50', None):
            with self.subTest(address=address), self.assertRaises(ValueError):
                generate_i2c_probe(address)
        for period in (0, 249, 268, 1000, True, 250.0):
            with self.subTest(period=period), self.assertRaises(ValueError):
                generate_i2c_probe(0x50, half_period_cycles=period)

    def test_example_is_reproducible(self):
        image = Path(__file__).resolve().parents[1] / 'firmware/i2c_probe.hex'
        self.assertEqual([int(word, 16) for word in image.read_text().split()], generate_i2c_probe(0x50))


if __name__ == '__main__':
    unittest.main()
