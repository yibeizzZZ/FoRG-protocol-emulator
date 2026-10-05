"""Command line entry point: python -m forg <command>."""

import argparse
import sys
from pathlib import Path

from . import FIRMWARE_DIR, assemble_file
from .area import AreaParams, estimate
from .asm import AsmError
from .timing import check_asserts


def _defines(items):
    out = {}
    for item in items or []:
        k, _, v = item.partition("=")
        out[k] = int(v, 0) if v else 1
    return out


def cmd_asm(args):
    prog = assemble_file(args.file, defines=_defines(args.define))
    print(f"; {prog.name}: {len(prog)} instructions, side_set {prog.side_count}")
    print(prog.disassemble())
    if prog.config:
        print("; config: " + ", ".join(f"{k}={v}" for k, v in prog.config.items()))
    ok = True
    for r in check_asserts(prog):
        print(("; PASS " if r.ok else "; FAIL ") + r.message)
        ok &= r.ok
    if args.hex:
        Path(args.hex).write_text("".join(f"{w:04x}\n" for w in prog.words))
        print(f"; wrote {args.hex}")
    return 0 if ok else 1


def cmd_check(args):
    files = [Path(f) for f in args.files] or sorted(FIRMWARE_DIR.glob("*.fasm"))
    failed = 0
    total_words = 0
    for f in files:
        try:
            prog = assemble_file(f)
        except AsmError as e:
            print(f"FAIL {f.name}: {e}")
            failed += 1
            continue
        total_words += len(prog)
        reports = check_asserts(prog)
        bad = [r for r in reports if not r.ok]
        status = "FAIL" if bad else "ok  "
        print(f"{status} {f.name:<22} {len(prog):>2} words, {len(reports)} timing proofs")
        for r in reports:
            if not r.ok or args.verbose:
                print(f"       {r.message}")
        failed += bool(bad)
    print(f"{len(files)} programs, {total_words} words total, {failed} failing")
    return 1 if failed else 0


def cmd_demo(args):
    from .demos import DEMOS

    names = list(DEMOS) if args.name == "all" else [args.name]
    for n in names:
        vcd = args.vcd if len(names) == 1 else (f"{args.vcd_dir}/{n}.vcd" if args.vcd_dir else None)
        print(f"[{n}] {DEMOS[n](vcd=vcd)}")
        if vcd:
            print(f"      waveform: {vcd}")
    return 0


def cmd_area(args):
    p = AreaParams(
        num_sm=args.sms,
        imem_words=args.imem,
        imem_as_sram=args.sram,
        fifo_depth=args.fifo,
        data_width=args.width,
        line_unit=not args.no_line,
        tiles=args.tiles,
    )
    print(estimate(p).text())
    print("(pre-synthesis estimate; replace with Yosys stat output once RTL exists)")
    return 0


def cmd_figures(args):
    from .figures import build

    build(args.out, args.only)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="forg", description="FoRG protocol emulator toolkit")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("asm", help="assemble, disassemble and timing-check a .fasm file")
    a.add_argument("file")
    a.add_argument("-D", "--define", action="append", help="NAME=VALUE")
    a.add_argument("--hex", help="write $readmemh image")
    a.set_defaults(func=cmd_asm)

    c = sub.add_parser("check", help="assemble all firmware and prove timing assertions")
    c.add_argument("files", nargs="*")
    c.add_argument("-v", "--verbose", action="store_true")
    c.set_defaults(func=cmd_check)

    d = sub.add_parser("demo", help="run an end-to-end protocol demo")
    d.add_argument("name", choices=["all", "uart", "spi", "i2c", "usb", "eth", "jtag", "sniff"])
    d.add_argument("--vcd", help="VCD output path (single demo)")
    d.add_argument("--vcd-dir", help="VCD output directory (all demos)")
    d.set_defaults(func=cmd_demo)

    r = sub.add_parser("area", help="pre-synthesis area budget")
    r.add_argument("--sms", type=int, default=4)
    r.add_argument("--imem", type=int, default=64)
    r.add_argument("--sram", action="store_true")
    r.add_argument("--fifo", type=int, default=4)
    r.add_argument("--width", type=int, default=32)
    r.add_argument("--tiles", type=int, default=24)
    r.add_argument("--no-line", action="store_true")
    r.set_defaults(func=cmd_area)

    f = sub.add_parser("figures", help="render presentation figures and an HTML gallery")
    f.add_argument("--out", default="figures")
    f.add_argument("--only", help="single figure, e.g. usb")
    f.set_defaults(func=cmd_figures)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
