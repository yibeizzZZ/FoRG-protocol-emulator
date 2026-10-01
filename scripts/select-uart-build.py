"""Select the independent M1 UART top for a disposable CI checkout."""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
info = root / "info.yaml"
text = info.read_text()
text = re.sub(r'(top_module:\s*)"[^"]+"', r'\1"tt_um_forg_uart_baseline"', text)
text = re.sub(r'  source_files:\n(?:    - .*\n)+', '  source_files:\n    - "uart_tx.v"\n    - "uart_baseline.v"\n', text)
text = re.sub(r'(clock_hz:\s*)\d+', r'\g<1>50000000', text)
text = re.sub(r'(title:\s*)"[^"]+"', r'\1"FoRG UART Baseline"', text)
text = re.sub(r'(description:\s*)"[^"]+"', r'\1"Independent fixed 8N1 UART transmitter"', text)
for i in range(8):
    for group, label in [('ui', f'data_{i}'), ('uo', 'tx' if i == 0 else 'busy' if i == 1 else ''),
                         ('uio', 'start' if i == 0 else '')]:
        text = re.sub(rf'({group}\[{i}\]:\s*)"[^"]*"', lambda m: m[1] + '"' + label + '"', text)
info.write_text(text)
(root / "docs" / "info.md").write_text((root / "docs" / "uart-baseline.md").read_text())
makefile = root / "test" / "Makefile"
makefile.write_text("TARGET ?= uart\n" + makefile.read_text())
