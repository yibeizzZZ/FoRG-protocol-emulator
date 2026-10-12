import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest


class UartRxFirmwareTests(unittest.TestCase):
    def module(self):
        path = Path(__file__).with_name('uart_rx_firmware.py')
        self.assertTrue(path.exists(), 'UART RX generator is missing')
        spec = importlib.util.spec_from_file_location('uart_rx_firmware', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_runtime_batch_and_capacity(self):
        module = self.module()
        words = module.generate_uart_rx()
        self.assertLessEqual(len(words), 128)
        for count in range(1, 17):
            data = module.receive_data(count)
            self.assertEqual(len(data), 32)
            self.assertEqual(data[16:20], [count, 0, 0, 0])
            self.assertEqual(module.generate_uart_rx(), words)

    def test_configuration_bounds(self):
        module = self.module()
        for count in (0, 17, -1, True, 1.0):
            with self.assertRaises(ValueError):
                module.receive_data(count)
        for pin in (-1, 8, True):
            with self.assertRaises(ValueError):
                module.generate_uart_rx(rx_pin=pin)

    def test_cli_and_checked_image(self):
        module = self.module()
        script = Path(__file__).with_name('uart_rx_firmware.py')
        result = subprocess.run([sys.executable, '-B', str(script)], check=True,
                                capture_output=True, text=True)
        self.assertEqual([int(w, 16) for w in result.stdout.split()], module.generate_uart_rx())
        image = Path(__file__).parents[1] / 'firmware/uart_rx.hex'
        self.assertEqual([int(w, 16) for w in image.read_text().split()], module.generate_uart_rx())


if __name__ == '__main__':
    unittest.main()
