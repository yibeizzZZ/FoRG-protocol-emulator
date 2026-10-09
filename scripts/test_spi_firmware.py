"""SPI generator contracts; independent pin-level tests verify actual transfers."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from spi_firmware import generate_spi_master


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/spi_firmware.py"


class SpiFirmwareTests(unittest.TestCase):
    def test_all_values_use_the_same_bit_loop(self):
        template = generate_spi_master([0, 0])
        data_sites = [index for index, word in enumerate(template) if word & 0xFF00 == 0x4000]
        self.assertEqual(len(data_sites), 2)
        for value in range(256):
            words = generate_spi_master([value, value ^ 255])
            expected = template.copy()
            expected[data_sites[0]] |= value
            expected[data_sites[1]] |= value ^ 255
            self.assertEqual(words, expected)
            self.assertLessEqual(len(generate_spi_master([value])), 32)

    def test_timing_capacity_boundaries(self):
        for half_period, size in ((13, 30), (14, 31), (16, 31), (262, 31), (263, 32), (269, 32)):
            with self.subTest(half_period=half_period):
                self.assertEqual(len(generate_spi_master([0x35, 0xC6], half_period_cycles=half_period)), size)
        for half_period in (12, -1, 0, 270, 10**12, 16.5, True):
            with self.subTest(half_period=half_period), self.assertRaises(ValueError):
                generate_spi_master([0x35], half_period_cycles=half_period)

    def test_rejects_invalid_payloads(self):
        for payload in ([], [1, 2, 3], [-1], [256], [False], ["35"], [2.5]):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                generate_spi_master(payload)

    def test_checked_in_image(self):
        words = [int(word, 16) for word in (ROOT / "firmware/spi_master.hex").read_text().split()]
        self.assertEqual(generate_spi_master([0xA5, 0x3C]), words)

    def test_cli_keeps_payload_and_timing_separate(self):
        result = subprocess.run([sys.executable, str(GENERATOR), "0xA5", "60", "--half-period", "32"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([int(word, 16) for word in result.stdout.split()],
                         generate_spi_master([0xA5, 0x3C], half_period_cycles=32))

    def test_invalid_cli_preserves_output_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "spi.hex"
            output.write_text("existing firmware\n")
            for args in ([], ["256"], ["1", "2", "3"], ["1", "--half-period", "12"],
                         ["1", "--half-period", "270"]):
                with self.subTest(args=args):
                    result = subprocess.run([sys.executable, str(GENERATOR), *args, "--output", str(output)],
                                            capture_output=True, text=True, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output.read_text(), "existing firmware\n")


if __name__ == "__main__":
    unittest.main()
