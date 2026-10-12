"""SPI generator contracts; independent pin-level tests verify actual transfers."""

from pathlib import Path
import hashlib
import subprocess
import sys
import tempfile
import unittest

from spi_firmware import generate_spi_master


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/spi_firmware.py"


class SpiFirmwareTests(unittest.TestCase):
    def test_mode0_legacy_output_exact(self):
        # SHA-256 captured from unmodified 3139fb7 generator, big-endian words.
        digest = hashlib.sha256()
        cases = [(payload, half) for half in range(13, 270)
                 for payload in ([0x37], [0xA5, 0x3C])]
        cases += [(payload, 16) for value in range(256)
                  for payload in ([value], [value, value ^ 255])]
        for payload, half in cases:
            words = generate_spi_master(payload, half_period_cycles=half)
            self.assertEqual(words, generate_spi_master(payload, mode=0, half_period_cycles=half))
            for word in words:
                digest.update(word.to_bytes(2, "big"))
        self.assertEqual(digest.hexdigest(), "57ab81ada84485cd12e677d00dd71b508687849f54781dc15e32163c9f70cf11")

    def test_modes_and_capacity_ranges(self):
        # Boundaries are derived from cycle budgets and the 32-word limit.
        for mode, low, high in ((0, 13, 269), (1, 16, 272), (2, 14, 262), (3, 15, 515)):
            for half in range(low, high + 1):
                for payload in ([0x35], [0x35, 0xC6]):
                    with self.subTest(mode=mode, half=half, length=len(payload)):
                        words = generate_spi_master(payload, mode=mode, half_period_cycles=half)
                        self.assertLessEqual(len(words), 32)
                        self.assertTrue(all(word >> 12 != 0xE for word in words))
            for half in (low - 1, high + 1, -1, 0, 10**12, 16.5, True):
                with self.subTest(mode=mode, half=half), self.assertRaises(ValueError):
                    generate_spi_master([0x35], mode=mode, half_period_cycles=half)

    def test_invalid_modes(self):
        for mode in (-1, 4, True, False, 1.0, "1", None):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                generate_spi_master([0x35], mode=mode)

    def test_cli_modes(self):
        for mode in range(4):
            result = subprocess.run([sys.executable, str(GENERATOR), "0xA5", "60", "--mode", str(mode)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([int(word, 16) for word in result.stdout.split()],
                             generate_spi_master([0xA5, 0x3C], mode=mode))

    def test_all_values_use_the_same_bit_loop(self):
        for mode in range(4):
            template = generate_spi_master([0, 0], mode=mode)
            data_sites = [index for index, word in enumerate(template) if word & 0xFF00 == 0x4000]
            self.assertEqual(len(data_sites), 2)
            for value in range(256):
                words = generate_spi_master([value, value ^ 255], mode=mode)
                expected = template.copy()
                expected[data_sites[0]] |= value
                expected[data_sites[1]] |= value ^ 255
                self.assertEqual(words, expected)
                self.assertLessEqual(len(generate_spi_master([value], mode=mode)), 32)

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
                         ["1", "--half-period", "270"], ["1", "--mode", "4"],
                         ["1", "--mode", "-1"], ["1", "--mode", "1", "--half-period", "15"],
                         ["1", "--mode", "2", "--half-period", "263"],
                         ["1", "--mode", "3", "--half-period", "516"]):
                with self.subTest(args=args):
                    result = subprocess.run([sys.executable, str(GENERATOR), *args, "--output", str(output)],
                                            capture_output=True, text=True, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output.read_text(), "existing firmware\n")


if __name__ == "__main__":
    unittest.main()
