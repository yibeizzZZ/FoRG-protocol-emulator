"""Select the independent M1 UART top for a disposable CI checkout."""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
info = root / "info.yaml"
text = info.read_text()
text = re.sub(r'(top_module:\s*)"[^"]+"', r'\1"tt_um_forg_uart_baseline"', text)
text = re.sub(r'  source_files:\n(?:    - .*\n)+', '  source_files:\n    - "uart_tx.v"\n    - "uart_baseline.v"\n', text)
text = re.sub(r'(clock_hz:\s*)\d+', r'\g<1>50000000', text)
info.write_text(text)
makefile = root / "test" / "Makefile"
makefile.write_text("TARGET ?= uart\n" + makefile.read_text())
