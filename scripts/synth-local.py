"""Map a design to a supplied Liberty library; no placement or timing claim."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("target", choices=["core", "uart", "legacy"])
parser.add_argument("--liberty", required=True, type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
sources, top = {
    "core": (["protocol_engine.v", "protocol_top.v"], "tt_um_forg_protocol_engine"),
    "uart": (["uart_tx.v", "uart_baseline.v"], "tt_um_forg_uart_baseline"),
    "legacy": (["project.v"], "tt_um_forg_protocol_emulator"),
}[args.target]
output = root / "build" / "synthesis" / args.target
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="forg-synth-", dir="/tmp") as directory:
    work = Path(directory)
    shutil.copyfile(args.liberty, work / "cells.lib")
    for source in sources:
        shutil.copyfile(root / "src" / source, work / source)
    script = (
        f"read_verilog {' '.join(sources)}\n"
        f"synth -top {top} -flatten\n"
        "dfflibmap -liberty cells.lib\nabc -liberty cells.lib\nclean\n"
        "stat -liberty cells.lib\nwrite_verilog -noattr -noexpr netlist.v\n"
    )
    (work / "synth.ys").write_text(script)
    result = subprocess.run(["yosys", "-Q", "-T", "-s", "synth.ys"], cwd=work,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (output / "synthesis.log").write_text(result.stdout)
    if result.returncode:
        raise SystemExit(f"Synthesis failed; inspect {output / 'synthesis.log'}")
    shutil.copyfile(work / "netlist.v", output / "netlist.v")
    print("\n".join(line for line in result.stdout.splitlines() if "Chip area" in line))
    print(f"Reports: {output}")
