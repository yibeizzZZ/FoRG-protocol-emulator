"""Exercise public generator entry points without discovery's sys.path changes."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
GENERATORS = (
    ("uart_firmware", "generate_uart_tx", "uart_tx.hex", [0x55, 0xAA]),
    ("spi_firmware", "generate_spi_master", "spi_master.hex", [0xA5, 0x3C]),
)


class FirmwareImportTests(unittest.TestCase):
    def expected(self, image):
        return [int(word, 16) for word in (ROOT / "firmware" / image).read_text().split()]

    def test_namespace_import_from_repository_root(self):
        for module, function, image, payload in GENERATORS:
            with self.subTest(module=module):
                code = f"from scripts.{module} import {function}; import json; print(json.dumps({function}({payload!r})))"
                result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), self.expected(image))

    def test_file_import_from_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            for module, function, image, payload in GENERATORS:
                with self.subTest(module=module):
                    path = ROOT / "scripts" / f"{module}.py"
                    code = (
                        "import importlib.util, json; "
                        f"spec = importlib.util.spec_from_file_location('external_generator', {str(path)!r}); "
                        "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
                        f"print(json.dumps(module.{function}({payload!r})))"
                    )
                    result = subprocess.run([sys.executable, "-c", code], cwd=directory,
                                            capture_output=True, text=True, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), self.expected(image))

    def test_module_cli_from_repository_root(self):
        for module, _, image, payload in GENERATORS:
            with self.subTest(module=module):
                result = subprocess.run([sys.executable, "-m", f"scripts.{module}", *map(str, payload)],
                                        cwd=ROOT, capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([int(word, 16) for word in result.stdout.split()], self.expected(image))


if __name__ == "__main__":
    unittest.main()
