"""Generate a zero-delay cell model for Icarus functional gate simulation.

IHP CMOS5L models drive delayed_* nets through timing checks, which Icarus
does not implement. Remove specify blocks and connect those nets directly
to their corresponding ports. This is not an SDF timing simulation.
"""
import re
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text()
functional = re.sub(r"\bspecify\b.*?\bendspecify\b", "", source, flags=re.S)
functional = re.sub(r"\bdelayed_(\w+)\b", r"\1", functional)
target = Path(sys.argv[2])
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(functional)
