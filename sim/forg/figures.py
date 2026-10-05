"""Presentation figures: annotated waveforms, architecture diagram, tolerance shmoo, area budget.

Usage: python -m forg figures [--out figures]
"""

import html
import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from . import assemble, firmware, FIRMWARE_DIR
from .area import estimate
from .chip import Chip
from .devices.ethernet import Eth10Monitor, frame_words
from .devices.i2c import I2cTarget
from .devices.jtag import JtagTap
from .devices.spi import SpiTarget
from .devices.uart import UartDriver, UartMonitor
from .devices.usb import UsbLsMonitor, pid_byte
from .drivers import decode_capture, i2c_write, jtag_reset, jtag_shift, run_host, spi_transfer
from .timing import check_asserts

INK = "#1f2933"
WAVE = "#1d4ed8"
FILL = "#dbeafe"
OK = "#15803d"
BAD = "#b91c1c"
ACCENT = "#b45309"
FIELD_COLORS = ["#e0e7ff", "#fef3c7", "#dcfce7", "#fce7f3", "#e0f2fe", "#ede9fe"]


class Recorder:
    """Stores net values on every change."""

    def __init__(self, chip):
        self.chip = chip
        self.cycles = []
        self.values = []
        chip.watchers.append(self)

    def __call__(self, cycle, nets):
        if not self.values or nets != self.values[-1]:
            self.cycles.append(cycle)
            self.values.append(nets)

    def series(self, pin, t0_us, t1_us):
        ns = self.chip.period_ns
        xs, ys = [], []
        last = None
        for c, v in zip(self.cycles, self.values):
            t = c * ns / 1000
            b = (v >> pin) & 1
            if t <= t0_us:
                last = b
                continue
            if t > t1_us:
                break
            if not xs:
                xs.append(t0_us)
                ys.append(last if last is not None else b)
            if b != ys[-1]:
                xs.append(t)
                ys.append(b)
        if not xs:
            xs, ys = [t0_us], [last or 0]
        xs.append(t1_us)
        ys.append(ys[-1])
        return xs, ys

    def edges(self, pin, rising=True):
        ns = self.chip.period_ns
        out = []
        prev = None
        for c, v in zip(self.cycles, self.values):
            b = (v >> pin) & 1
            if prev is not None and b != prev and b == int(rising):
                out.append(c * ns / 1000)
            prev = b
        return out

    def level_at(self, pin, t_us):
        ns = self.chip.period_ns
        level = 0
        for c, v in zip(self.cycles, self.values):
            if c * ns / 1000 > t_us:
                break
            level = (v >> pin) & 1
        return level


def _wave_axes(title, signals, t0, t1, decode_rows=1, width=13, subtitle=None):
    rows = len(signals) + decode_rows
    fig, ax = plt.subplots(figsize=(width, 0.9 * rows + 1.4))
    ax.set_xlim(t0, t1)
    ax.set_ylim(-0.1 - 0.8 * decode_rows, len(signals))
    ax.set_yticks([len(signals) - 1 - i + 0.35 for i in range(len(signals))])
    ax.set_yticklabels([s[1] for s in signals], fontsize=11, color=INK)
    ax.tick_params(axis="y", length=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.set_xlabel("time (us)", color=INK)
    ax.grid(axis="x", color="#e5e7eb", linewidth=0.6)
    ax.set_axisbelow(True)
    fig.suptitle(title, x=0.01, ha="left", fontsize=14, fontweight="bold", color=INK)
    if subtitle:
        ax.set_title(subtitle, loc="left", fontsize=10, color="#52606d")
    return fig, ax


def _draw_waves(ax, rec, signals, t0, t1):
    n = len(signals)
    for i, (pin, _) in enumerate(signals):
        base = n - 1 - i
        xs, ys = rec.series(pin, t0, t1)
        yy = [base + 0.7 * y for y in ys]
        ax.fill_between(xs, base, yy, step="post", color=FILL, linewidth=0)
        ax.step(xs, yy, where="post", color=WAVE, linewidth=1.6)


def _segment(ax, row_y, t0, t1, text, color=None, text_color=INK, fontsize=9):
    color = color or FIELD_COLORS[0]
    ax.add_patch(FancyBboxPatch((t0, row_y - 0.32), max(t1 - t0, 1e-6), 0.64,
                                boxstyle="round,pad=0,rounding_size=0.08", linewidth=0.8,
                                edgecolor="#9aa5b1", facecolor=color, mutation_aspect=0.3))
    ax.text((t0 + t1) / 2, row_y, text, ha="center", va="center", fontsize=fontsize, color=text_color,
            clip_on=True)


def _marker(ax, t, text, y, color=ACCENT):
    ax.axvline(t, color=color, linewidth=1, linestyle="--", alpha=0.8)
    ax.text(t, y, text, color=color, fontsize=9, ha="center", va="center", fontweight="bold",
            bbox=dict(facecolor="white", edgecolor="none", pad=1))


def _save(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def fig_uart(out):
    clk, baud = 24e6, 1e6
    chip = Chip(clock_hz=clk)
    chip.load(firmware("uart_tx"), 0, clkdiv=clk / (8 * baud))
    mon = chip.attach(UartMonitor(8, baud))
    rec = Recorder(chip)
    chip.run(48)
    run_host(chip, 0, list(b"Hi"), 0)
    chip.run_until(lambda: len(mon.frames) == 2, 100_000)
    chip.run(60)
    t0, t1 = 1.0, mon.frames[-1][2] / 1000 + 11.5
    signals = [(8, "TX (uo0)")]
    fig, ax = _wave_axes("UART transmit: firmware on SM0 sends \"Hi\" at 1 Mbaud", signals, t0, t1, 2,
                         subtitle="Decoded by the independent UartMonitor model; bit-edge timing error 0% "
                                  "(proved statically: 8 ticks per bit on every path)")
    _draw_waves(ax, rec, signals, t0, t1)
    bit = 1.0
    for byte, ok, tns in mon.frames:
        ts = tns / 1000
        _segment(ax, -0.5, ts, ts + bit, "start", FIELD_COLORS[1])
        for k in range(8):
            _segment(ax, -0.5, ts + bit * (1 + k), ts + bit * (2 + k), f"b{k}={(byte >> k) & 1}", FIELD_COLORS[0])
        _segment(ax, -0.5, ts + 9 * bit, ts + 10 * bit, "stop", FIELD_COLORS[2] if ok else BAD)
        _segment(ax, -1.25, ts, ts + 10 * bit, f"0x{byte:02X}  '{chr(byte)}'", FIELD_COLORS[4], fontsize=11)
    ax.text(t0, -0.5, "bits ", ha="right", va="center", fontsize=10, color="#52606d")
    ax.text(t0, -1.25, "byte ", ha="right", va="center", fontsize=10, color="#52606d")
    return _save(fig, out / "01_uart.png")


def fig_spi(out):
    chip = Chip(clock_hz=50e6)
    chip.load(firmware("spi_cpha0"), 0, clkdiv=3)
    reply = [0x00, 0xEF, 0x40, 0x18]
    chip.attach(SpiTarget(8, 9, 20, 10, respond=lambda i, rx: reply[i % 4]))
    rec = Recorder(chip)
    chip.run(20)
    rx = spi_transfer(chip, 0, b"\x9f\x00\x00\x00")
    chip.run(40)
    rises = rec.edges(8, True)
    t0, t1 = rises[0] - 0.4, rises[-1] + 0.5
    signals = [(10, "CS_n"), (8, "SCK"), (9, "MOSI"), (20, "MISO")]
    fig, ax = _wave_axes("SPI mode 0: reading a flash chip's JEDEC ID (0x9F command)", signals, t0, t1, 2,
                         subtitle=f"MOSI 9F 00 00 00, MISO read back {rx.hex(' ').upper()} "
                                  "(SPI target model checks mode and bit order)")
    _draw_waves(ax, rec, signals, t0, t1)
    half = (rises[1] - rises[0]) / 2
    for i in range(0, len(rises), 8):
        a, b = rises[i] - half, rises[min(i + 7, len(rises) - 1)] + half
        mosi = sum(rec.level_at(9, rises[i + k]) << (7 - k) for k in range(8))
        miso = sum(rec.level_at(20, rises[i + k] + 0.001) << (7 - k) for k in range(8))
        _segment(ax, -0.5, a, b, f"MOSI 0x{mosi:02X}", FIELD_COLORS[0], fontsize=10)
        _segment(ax, -1.25, a, b, f"MISO 0x{miso:02X}", FIELD_COLORS[2], fontsize=10)
    return _save(fig, out / "02_spi.png")


def fig_i2c(out):
    clk = 50e6
    chip = Chip(clock_hz=clk)
    chip.set_pulls(up=[0, 1])
    chip.load(firmware("i2c_master"), 0, clkdiv=clk * 0.16e-6)
    tgt = chip.attach(I2cTarget(0, 1, 0x50, stretch_ns=4000, mode="fast"))
    rec = Recorder(chip)
    stretch = []
    state = {"on": None}

    def watch(c, n):
        on = tgt.stretch_until is not None
        if on and state["on"] is None:
            state["on"] = c
        elif not on and state["on"] is not None:
            stretch.append((state["on"], c))
            state["on"] = None

    chip.watchers.append(watch)
    chip.run(40)
    acks = i2c_write(chip, 0, 0x50, b"\x20J")
    chip.run(800)
    ns = chip.period_ns
    starts = [t / 1000 for e, t in [(e[0], e[1]) for e in tgt.events if e[0] in ("S", "Sr", "P")]]
    t0, t1 = starts[0] - 3, starts[-1] + 3
    signals = [(1, "SCL (uio1)"), (0, "SDA (uio0)")]
    fig, ax = _wave_axes("I2C fast mode: write 'J' to register 0x20 of an EEPROM at address 0x50",
                         signals, t0, t1, 2,
                         subtitle=f"Open-drain pins with pull-ups. Shaded: the target stretches SCL low and "
                                  f"the firmware waits. ACKs {acks}, protocol timing violations: {len(tgt.violations)}")
    for a, b in stretch:
        ax.axvspan(a * ns / 1000, b * ns / 1000, color="#fde68a", alpha=0.6, linewidth=0)
    _draw_waves(ax, rec, signals, t0, t1)
    for e in tgt.events:
        if e[0] in ("S", "Sr", "P"):
            _marker(ax, e[1] / 1000, {"S": "START", "Sr": "rSTART", "P": "STOP"}[e[0]], -1.25)
    rises = [t for t in rec.edges(1, True) if starts[0] < t < starts[-1]]
    names = ["addr 0x50 + W", "reg 0x20", "data 0x4A 'J'"]
    for i in range(0, len(rises) - 8, 9):
        grp = rises[i:i + 9]
        byte = sum(rec.level_at(0, grp[k]) << (7 - k) for k in range(8))
        ack = rec.level_at(0, grp[8])
        label = names[i // 9] if i // 9 < len(names) else f"0x{byte:02X}"
        _segment(ax, -0.5, grp[0] - 0.4, grp[7] + 0.4, label, FIELD_COLORS[0], fontsize=10)
        _segment(ax, -0.5, grp[8] - 0.4, grp[8] + 0.4, "ACK" if ack == 0 else "NAK",
                 FIELD_COLORS[2] if ack == 0 else "#fecaca", fontsize=9)
    return _save(fig, out / "03_i2c.png")


def _usb_layout(packet):
    """Line-bit index ranges per byte and the indexes of stuffed bits."""
    idx, ones = 0, 0
    spans, stuffed = [], []
    for byte in packet:
        start = idx
        for i in range(8):
            bit = (byte >> i) & 1
            idx += 1
            ones = ones + 1 if bit else 0
            if ones == 6:
                stuffed.append(idx)
                idx += 1
                ones = 0
        spans.append((start, idx))
    return spans, stuffed, idx


def fig_usb(out):
    chip = Chip(clock_hz=60e6)
    chip.set_pulls(up=[2])
    chip.load(firmware("usb_ls_tx"), 0)
    mon = chip.attach(UsbLsMonitor(2, 3))
    rec = Recorder(chip)
    payload = b"\xff\x01"
    body = bytes([0x80, pid_byte("DATA0")]) + payload
    pad = body + b"\0" * ((-len(body)) % 4)
    chip.run(200)
    run_host(chip, 0, [len(payload) | (1 << 16)] + [int.from_bytes(pad[i:i + 4], "little")
                                                      for i in range(0, len(pad), 4)], 0)
    chip.run_until(lambda: mon.packets, 50_000)
    chip.run(100)
    pkt = mon.packets[0]
    full = pkt["raw"]
    bit = 1 / 1.5
    ts = pkt["t"] / 1000
    spans, stuffed, nbits = _usb_layout(full)
    t0, t1 = ts - 2 * bit, ts + (nbits + 5) * bit
    signals = [(3, "D+ (uio3)"), (2, "D- (uio2)")]
    crc = full[-2] | (full[-1] << 8)
    fig, ax = _wave_axes("USB 1.1 low speed: DATA0 packet with payload FF 01, generated by the line unit",
                         signals, t0, t1, 2,
                         subtitle=f"NRZI, bit stuffing and CRC16 (0x{crc:04X}) done in hardware. "
                                  f"Monitor verdict: SYNC/PID/CRC/EOP {'all valid' if pkt['ok'] else pkt['errors']}")
    _draw_waves(ax, rec, signals, t0, t1)
    names = ["SYNC", "PID DATA0"] + [f"0x{b:02X}" for b in payload] + ["CRC16 lo", "CRC16 hi"]
    for i, (a, b) in enumerate(spans):
        _segment(ax, -0.5, ts + a * bit, ts + b * bit, names[i], FIELD_COLORS[i % len(FIELD_COLORS)], fontsize=10)
    _segment(ax, -0.5, ts + nbits * bit, ts + (nbits + 2) * bit, "EOP", "#fecaca", fontsize=9)
    for s in stuffed:
        x = ts + (s + 0.5) * bit
        ax.annotate("stuffed 0", xy=(x, 1.75), xytext=(x, 2.25), ha="center", fontsize=9, color=BAD,
                    arrowprops=dict(arrowstyle="->", color=BAD))
    for i in range(nbits):
        ax.axvline(ts + i * bit, color="#cbd5e1", linewidth=0.4, zorder=0)
    ax.text(t0, -1.25, "  vertical grid = 1.5 MHz bit cells; line unit inserts a 0 after six 1s", fontsize=9,
            color="#52606d", va="center")
    ax.set_ylim(-1.6, 2.6)
    return _save(fig, out / "04_usb.png")


def fig_ethernet(out):
    chip = Chip(clock_hz=60e6)
    chip.load(firmware("eth10_tx", NLP_TICKS=12_000), 0)
    mon = chip.attach(Eth10Monitor(4, 5))
    rec = Recorder(chip)
    frame = bytes.fromhex("ffffffffffff02000000beef0800") + b"hello prof".ljust(46, b"\0")
    chip.run(30_000)
    run_host(chip, 0, frame_words(frame), 0)
    chip.run_until(lambda: mon.frames, 50_000)
    chip.run(30_000)
    f = mon.frames[0]
    ts = f["t"] / 1000

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(13, 6.4), gridspec_kw={"height_ratios": [1, 1.5]})
    fig.suptitle("10BASE-T Ethernet: link pulses while idle, then a Manchester-coded frame", x=0.01, ha="left",
                 fontsize=14, fontweight="bold", color=INK)
    end = chip.cycle * chip.period_ns / 1000
    for ax in (a1, a2):
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    xs, ys = rec.series(4, 0, end)
    xn, yn = rec.series(5, 0, end)
    a1.step(xs, ys, where="post", color=WAVE, linewidth=1)
    a1.step(xn, [y - 1.3 for y in yn], where="post", color="#7c3aed", linewidth=1)
    a1.set_yticks([0.4, -0.9])
    a1.set_yticklabels(["TD+", "TD-"])
    a1.set_xlabel("time (us)")
    a1.set_title(f"Overview: {len(mon.link_pulses)} normal link pulses (100 ns each) from the stall-timeout "
                 f"trap, frame FCS {'valid' if f['fcs_ok'] else 'BAD'} (checked with zlib.crc32)",
                 loc="left", fontsize=10, color="#52606d")
    for t, w in mon.link_pulses:
        a1.annotate("NLP", xy=(t / 1000, 1.0), xytext=(t / 1000, 1.5), ha="center", fontsize=8, color=ACCENT,
                    arrowprops=dict(arrowstyle="->", color=ACCENT))
    a1.annotate("frame", xy=(ts, 1.0), xytext=(ts, 1.5), ha="center", fontsize=9, color=OK, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=OK))
    a1.set_ylim(-1.6, 1.9)

    bit = 0.1
    first, last = 52, 84
    z0, z1 = ts + first * bit, ts + last * bit
    xs, ys = rec.series(4, z0, z1)
    a2.fill_between(xs, 0, ys, step="post", color=FILL, linewidth=0)
    a2.step(xs, ys, where="post", color=WAVE, linewidth=1.6)
    a2.set_xlim(z0, z1)
    a2.set_ylim(-1.5, 1.6)
    a2.set_yticks([0.4])
    a2.set_yticklabels(["TD+"])
    a2.set_xlabel("time (us)")
    a2.set_title("Zoom: end of preamble, SFD, start of destination MAC. Every bit has a mid-bit "
                 "transition; rising = 1, falling = 0", loc="left", fontsize=10, color="#52606d")
    stream = [1 - (i % 2) for i in range(56)] + [(0xD5 >> k) & 1 for k in range(8)]
    for byte in frame[:3]:
        stream += [(byte >> k) & 1 for k in range(8)]
    for i in range(first, last):
        x = ts + (i + 0.5) * bit
        a2.text(x, 1.2, str(stream[i]), ha="center", fontsize=9, color=INK)
        a2.axvline(ts + i * bit, color="#e2e8f0", linewidth=0.5, zorder=0)
    _segment(a2, -0.7, z0, ts + 56 * bit, "preamble 0x55...", FIELD_COLORS[0], fontsize=10)
    _segment(a2, -0.7, ts + 56 * bit, ts + 64 * bit, "SFD 0xD5", FIELD_COLORS[1], fontsize=10)
    _segment(a2, -0.7, ts + 64 * bit, z1, "dst MAC ff:ff:ff:...", FIELD_COLORS[2], fontsize=10)
    return _save(fig, out / "05_ethernet.png")


def fig_jtag(out):
    chip = Chip(clock_hz=50e6)
    chip.load(firmware("jtag"), 0, clkdiv=2)
    tap = chip.attach(JtagTap(11, 13, 12, 21, idcode=0x1BADC0DF))
    rec = Recorder(chip)
    states = []

    def watch(c, n):
        if not states or states[-1][1] != tap.state:
            states.append((c * chip.period_ns / 1000, tap.state))

    chip.watchers.append(watch)
    chip.run(20)
    jtag_reset(chip, 0)
    t_start = chip.cycle * chip.period_ns / 1000
    idcode = jtag_shift(chip, 0, False, 0, 32)
    chip.run(20)
    t0, t1 = t_start - 0.2, chip.cycle * chip.period_ns / 1000
    signals = [(11, "TCK"), (13, "TMS"), (12, "TDI"), (21, "TDO")]
    fig, ax = _wave_axes(f"JTAG: reading the IDCODE register of a target chip -> 0x{idcode:08X}",
                         signals, t0, t1, 1,
                         subtitle="Not in the original brief: added purely in firmware (6 instructions). "
                                  "Bottom row: TAP controller state from the IEEE 1149.1 model")
    _draw_waves(ax, rec, signals, t0, t1)
    pts = [(t, s) for t, s in states if t >= t0] + [(t1, None)]
    for (a, s), (b, _) in zip(pts, pts[1:]):
        if s:
            label = {"SHDR": "Shift-DR: 32 IDCODE bits out on TDO", "RTI": "Idle", "SELDR": "Sel",
                     "CAPDR": "Cap", "EX1DR": "Ex1", "UPDR": "Upd", "TLR": "Reset"}.get(s, s)
            _segment(ax, -0.5, a, b, label, FIELD_COLORS[4 if s == "SHDR" else 0], fontsize=8)
    return _save(fig, out / "06_jtag.png")


def fig_logic_analyzer(out):
    from .chip import Device

    class Square(Device):
        def step(self, chip, cycle, nets):
            self.drive(20, (cycle // 90) & 1)

    clk, baud = 24e6, 1e6
    chip = Chip(clock_hz=clk)
    chip.set_pulls(up=[19])
    drv = chip.attach(UartDriver(19, baud))
    chip.attach(Square())
    chip.run(5)
    chip.load(firmware("logic_analyzer"), 3)
    rec = Recorder(chip)
    words = []
    chip.watchers.append(lambda c, n: words.extend(chip.drain(3)))
    chip.run(30)
    drv.send(b"A")
    chip.run(14 * 24)
    t_end = chip.cycle * chip.period_ns / 1000
    samples = chip.cycle - 5
    runs = decode_capture(words, 4)

    fig, ax = _wave_axes("Logic analyzer mode: one CAP instruction, run-length compressed",
                         [(19, "probe 0 (actual)"), (20, "probe 1 (actual)")], 0.2, t_end, 2,
                         subtitle=f"{samples} samples captured as {len(words)} FIFO words "
                                  f"({samples / max(len(words), 1):.0f}x compression). "
                                  "Bottom rows: waveform rebuilt on the host from those words only")
    _draw_waves(ax, rec, [(19, ""), (20, "")], 0.2, t_end)
    # align the rebuilt trace on the first change, removing the 2-cycle input sync latency
    first_change = next(c for c, v, pv in zip(rec.cycles[1:], rec.values[1:], rec.values)
                        if ((v ^ pv) >> 19) & 0xF)
    t = (first_change - runs[0][1]) * chip.period_ns / 1000
    for row, pin in ((-0.75, 0), (-1.55, 1)):
        xs, ys = [], []
        tt = t
        for level, dur in runs:
            xs.append(tt)
            ys.append(row - 0.3 + 0.6 * ((level >> pin) & 1))
            tt += dur * chip.period_ns / 1000
        if xs:
            xs.append(tt)
            ys.append(ys[-1])
            ax.step(xs, ys, where="post", color=OK, linewidth=1.6)
        ax.text(0.2, row, f"probe {pin} (rebuilt) ", ha="right", va="center", fontsize=10, color=OK)
    ax.set_ylim(-2.1, 2)
    return _save(fig, out / "07_logic_analyzer.png")


def _uart_rx_passes(src, div, err, seed):
    clk = 48e6
    chip = Chip(clock_hz=clk)
    chip.load(src, 1, clkdiv=div)
    drv = chip.attach(UartDriver(19, clk / (8 * div), baud_error=err))
    r = random.Random(seed)
    msg = bytes([0x00, 0xFF, 0x55, 0x80] + [r.randrange(256) for _ in range(4)])
    chip.run(int(20 * 8 * div))
    drv.send(msg)
    got = []
    chip.run_until(lambda: got.extend(w >> 24 for w in chip.drain(1)) or (drv.idle and len(got) >= len(msg)),
                   len(msg) * 12 * 8 * div + 2000)
    chip.run(int(30 * div))
    got += [w >> 24 for w in chip.drain(1)]
    return bytes(got) == msg and not chip.irq & 0x10


def fig_shmoo(out):
    base = (FIRMWARE_DIR / "uart_rx.fasm").read_text()
    with_resync = assemble(base)
    without = assemble(base.replace(" resync", ""))
    errs = [e / 1000 for e in range(-80, 81, 4)]
    divs = [1, 2, 4, 8]
    fig, axes = plt.subplots(1, 2, figsize=(13, 3.6), sharey=True)
    fig.suptitle("UART RX tolerance to baud-rate mismatch (green = all bytes received correctly)",
                 x=0.01, ha="left", fontsize=14, fontweight="bold", color=INK)
    for ax, prog, name in ((axes[0], without, "plain WAIT (PIO-style)"),
                           (axes[1], with_resync, "WAIT ... resync (FoRG)")):
        grid = [[_uart_rx_passes(prog, d, e, 7) for e in errs] for d in divs]
        for yi, row in enumerate(grid):
            for xi, ok in enumerate(row):
                ax.add_patch(plt.Rectangle((errs[xi] * 100 - 0.2, yi - 0.45), 0.4, 0.9,
                                           color=OK if ok else "#fca5a5", linewidth=0))
        passing = [e for e, ok in zip(errs, grid[-1]) if ok]
        span = f"+-{min(abs(min(passing)), max(passing)) * 100:.1f}% at clkdiv 8" if passing else "none"
        ax.set_title(f"{name}: {span}", loc="left", fontsize=11, color=INK)
        ax.set_xlim(-8.4, 8.4)
        ax.set_ylim(-0.6, len(divs) - 0.4)
        ax.set_yticks(range(len(divs)))
        ax.set_yticklabels([f"clkdiv {d}" for d in divs])
        ax.set_xlabel("sender baud error (%)")
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    return _save(fig, out / "08_uart_tolerance_shmoo.png")


def fig_area(out):
    rep = estimate()
    fig, ax = plt.subplots(figsize=(13, 2.8))
    left = 0
    for i, (name, ff, logic) in enumerate(rep.rows):
        w = ff + logic
        ax.barh(0, w, left=left, color=FIELD_COLORS[i % len(FIELD_COLORS)], edgecolor="#64748b",
                label=f"{name}: {w}")
        if w > 3000:
            ax.text(left + w / 2, 0, f"{name.split(' x')[0]}\n{w}", ha="center", va="center", fontsize=8.5,
                    color=INK)
        left += w
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.45), ncol=3, frameon=False, fontsize=8.5)
    ax.axvline(rep.budget, color=BAD, linestyle="--")
    ax.text(rep.budget, 0.48, f"usable budget ~{rep.budget} cells\n(24 tiles, 30% routing margin)",
            color=BAD, fontsize=9, ha="right", va="bottom")
    ax.set_xlim(0, 24000)
    ax.set_ylim(-0.6, 1.0)
    ax.set_yticks([])
    ax.set_xlabel("estimated standard cells (pre-synthesis)")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    fig.suptitle(f"Area budget estimate: {rep.total} of ~{rep.budget} cells ({rep.total / rep.budget:.0%}) "
                 "- to be replaced by Yosys numbers", x=0.01, ha="left", fontsize=14, fontweight="bold", color=INK)
    return _save(fig, out / "09_area_budget.png")


def fig_architecture(out):
    fig, ax = plt.subplots(figsize=(13, 6.2))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 6.2)
    ax.axis("off")

    def box(x, y, w, h, title, lines=(), color="#e0e7ff", title_size=11):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12",
                                    facecolor=color, edgecolor="#475569", linewidth=1.2))
        ax.text(x + w / 2, y + h - 0.22, title, ha="center", va="top", fontsize=title_size, fontweight="bold",
                color=INK)
        for i, ln in enumerate(lines):
            ax.text(x + w / 2, y + h - 0.55 - 0.27 * i, ln, ha="center", va="top", fontsize=8.5, color="#334155")

    def arrow(x0, y0, x1, y1):
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0), arrowprops=dict(arrowstyle="->", color="#475569", lw=1.4))

    fig.suptitle("FoRG Protocol Emulator: architecture", x=0.01, ha="left", fontsize=15, fontweight="bold",
                 color=INK)
    box(0.2, 4.4, 2.4, 1.4, "Host link", ["SPI target on ui0-2 / uo7", "load programs, FIFOs,", "config, IRQs"],
        "#f1f5f9")
    box(3.2, 4.4, 5.9, 1.4, "Shared instruction memory", ["64 x 16-bit words, used by all 4 state machines",
                                                          "10 bundled programs = 101 words; UART+SPI+I2C+",
                                                          "logic analyzer together = 41 words"],
        "#fef3c7")
    arrow(2.6, 5.1, 3.2, 5.1)
    for i in range(4):
        x = 0.4 + i * 3.15
        box(x, 1.75, 2.9, 2.3, f"State machine {i}",
            ["X, Y, ISR, OSR (32-bit)", "TX / RX FIFOs (4 deep)", "fractional clock divider",
             "side-set, delays, timeout trap", "line unit: NRZI, stuffing,", "Manchester, SE0, CRC"],
            "#dbeafe")
        arrow(x + 1.45, 4.4, x + 1.45, 4.05)
        arrow(x + 1.45, 1.75, x + 1.45, 1.35)
    arrow(2.6, 4.75, 3.2, 4.75)
    box(0.4, 0.3, 12.35, 1.05, "GPIO: 24 pins",
        ["uio0-7 bidirectional + open-drain    uo0-7 outputs    ui0-7 inputs    "
         "2-flop input sync (per-pin bypass), registered outputs"], "#dcfce7")
    ax.text(9.4, 5.85, "Implemented in firmware:", fontsize=10, fontweight="bold", color=INK)
    for i, p in enumerate(["UART TX / RX", "SPI modes 0-3", "I2C + clock stretching", "USB 1.1 low speed TX/RX",
                           "10BASE-T Ethernet TX", "JTAG", "logic analyzer"]):
        ax.text(9.45, 5.6 - 0.2 * i, f"- {p}", fontsize=9, color=OK)
    return _save(fig, out / "00_architecture.png")


FIGURES = [
    ("Architecture", fig_architecture,
     "Four cycle-exact state machines share one program memory. Each has a hardware line unit that does the "
     "bit-level work USB and Ethernet need."),
    ("UART", fig_uart, "Firmware sends \"Hi\". An independent receiver model decodes it and measures bit timing."),
    ("SPI", fig_spi, "Reads a flash chip's ID. The bottom rows show the bytes on each data line."),
    ("I2C", fig_i2c, "Writes to an EEPROM model, which stretches the clock (shaded) to slow the controller down."),
    ("USB low speed", fig_usb, "A full USB data packet. The line unit inserts stuffed bits and the CRC16 automatically."),
    ("10BASE-T Ethernet", fig_ethernet, "Link pulses while idle, then a real Ethernet frame. The zoom shows Manchester coding."),
    ("JTAG", fig_jtag, "Reads the ID register of a chip through its debug port. Added using only firmware."),
    ("Logic analyzer", fig_logic_analyzer, "One instruction captures pins with run-length compression. The host rebuilds the waveform."),
    ("Verification: tolerance shmoo", fig_shmoo,
     "Thousands of simulated frames over a baud-error sweep. Left: a PIO-style wait. Right: FoRG's resync wait."),
    ("Area budget", fig_area, "Hand estimate of the chip area against the 6x4 tile budget."),
]


def _check_rows():
    rows = []
    for f in sorted(FIRMWARE_DIR.glob("*.fasm")):
        prog = assemble(f.read_text(), filename=str(f))
        reps = check_asserts(prog)
        rows.append((f.stem, len(prog), len(reps), all(r.ok for r in reps), [r.message for r in reps]))
    return rows


def build(out_dir="figures", only=None):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for title, fn, caption in FIGURES:
        if only and fn.__name__ != f"fig_{only}":
            continue
        path = fn(out)
        made.append((title, Path(path).name, caption))
        print(f"wrote {path}")
    if only:
        return None
    rows = _check_rows()
    table = "".join(
        f"<tr><td>{html.escape(n)}</td><td>{w}</td><td>{k}</td>"
        f"<td class='{'ok' if ok else 'bad'}'>{'pass' if ok else 'FAIL'}</td>"
        f"<td><small>{'<br>'.join(html.escape(m) for m in msgs)}</small></td></tr>"
        for n, w, k, ok, msgs in rows
    )
    cards = "".join(
        f"<section><h2>{html.escape(t)}</h2><p>{html.escape(c)}</p><img src='{p}' alt='{html.escape(t)}'></section>"
        for t, p, c in made
    )
    (out / "index.html").write_text(f"""<!doctype html><meta charset="utf-8">
<title>FoRG Protocol Emulator: simulation results</title>
<style>
body {{ font-family: -apple-system, Helvetica, sans-serif; max-width: 1180px; margin: 32px auto; color: #1f2933; }}
section {{ margin: 36px 0; }} img {{ width: 100%; border: 1px solid #e5e7eb; border-radius: 8px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
td, th {{ border-bottom: 1px solid #e5e7eb; padding: 6px 8px; text-align: left; vertical-align: top; }}
.ok {{ color: #15803d; font-weight: 600; }} .bad {{ color: #b91c1c; font-weight: 600; }}
</style>
<h1>FoRG Protocol Emulator: simulation results</h1>
<p>Every waveform below comes from the cycle-accurate golden model running real firmware. The annotations
come from separate protocol reference models that only look at the pins.</p>
{cards}
<section><h2>Static timing proofs</h2>
<p>For each program, every possible path through the code is checked to take exactly the required number of cycles.</p>
<table><tr><th>firmware</th><th>words</th><th>proofs</th><th>result</th><th>details</th></tr>{table}</table>
</section>
""")
    print(f"wrote {out / 'index.html'}")
    return out / "index.html"
