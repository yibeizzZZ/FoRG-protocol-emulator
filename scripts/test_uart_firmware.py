"""Generator/CLI contract tests; pin timing is checked independently in cocotb."""

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


GENERATOR = Path(__file__).with_name("uart_firmware.py")
EXAMPLE = GENERATOR.parents[1] / "firmware" / "uart_tx.hex"


class UartFirmwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("uart_firmware", GENERATOR)
        cls.generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.generator)

    def test_all_byte_values_fit_instruction_memory(self):
        for byte in range(256):
            for payload in ([byte], [byte, byte ^ 0xFF]):
                with self.subTest(payload=payload):
                    words = self.generator.generate_uart_tx(payload)
                    self.assertLessEqual(len(words), 32)
                    self.assertTrue(all(0 <= word <= 0xFFFF for word in words))
                    self.assertTrue(all(word >> 12 in {0, 1, 2, 4, 5, 6, 7, 9, 10, 11, 12}
                                        for word in words))

    def test_rejects_invalid_payloads(self):
        for payload in ([], [1, 2, 3], [-1], [256], [1.5], [True], ["55"]):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self.generator.generate_uart_tx(payload)

    def test_example_is_reproducible(self):
        expected = [int(word, 16) for word in EXAMPLE.read_text().split()]
        self.assertEqual(self.generator.generate_uart_tx([0x55, 0xAA]), expected)

    def test_cli_accepts_hex_and_decimal(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "uart.hex"
            result = subprocess.run(
                [sys.executable, str(GENERATOR), "0x55", "170", "--output", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(), EXAMPLE.read_text())

    def test_invalid_cli_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "uart.hex"
            output.write_text("existing firmware\n")
            for args in ([], ["256"], ["-1"], ["garbage"], ["1", "2", "3"]):
                with self.subTest(args=args):
                    result = subprocess.run(
                        [sys.executable, str(GENERATOR), *args, "--output", str(output)],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output.read_text(), "existing firmware\n")


if __name__ == "__main__":
    unittest.main()
