"""End-to-end demos used by the CLI. Each returns a short human-readable summary."""

from . import firmware
from .chip import Chip
from .devices.ethernet import Eth10Monitor, frame_words
from .devices.i2c import I2cTarget
from .devices.jtag import JtagTap
from .devices.spi import SpiTarget
from .devices.uart import UartDriver, UartMonitor
from .devices.usb import UsbLsDriver, UsbLsMonitor, data_packet, pid_byte, token_packet
from .drivers import (
    decode_capture, i2c_write, i2c_write_read, is_status, jtag_reset, jtag_shift, run_host, spi_transfer,
)
from .vcd import VcdWriter


def _vcd(chip, path, labels):
    return VcdWriter(chip, path, labels=labels) if path else None


def demo_uart(vcd=None):
    clk, baud = 48e6, 3e6
    chip = Chip(clock_hz=clk)
    chip.load(firmware("uart_tx"), 0, clkdiv=clk / (8 * baud))
    chip.load(firmware("uart_rx"), 1, clkdiv=clk / (8 * baud))
    mon = chip.attach(UartMonitor(8, baud))
    drv = chip.attach(UartDriver(19, baud, baud_error=0.02))
    v = _vcd(chip, vcd, {8: "tx", 19: "rx"})
    msg = b"FoRG says hi"
    drv.send(msg)
    pending = list(msg)
    got = []

    def host():
        if pending and chip.put(0, pending[0]):
            pending.pop(0)
        got.extend(w >> 24 for w in chip.drain(1))
        return len(got) >= len(msg) and len(mon.frames) >= len(msg)

    chip.run_until(host, 200_000)
    if v:
        v.close()
    return (f"TX decoded by monitor: {mon.bytes!r} (max edge error {mon.max_edge_error:.1%} of a bit)\n"
            f"RX from +2% fast sender: {bytes(got)!r}")


def demo_spi(vcd=None):
    chip = Chip(clock_hz=50e6)
    chip.load(firmware("spi_cpha0", CPOL=0), 0, clkdiv=2)
    tgt = chip.attach(SpiTarget(8, 9, 20, 10, respond=lambda i, rx: [0x9F, 0xEF, 0x40, 0x18][i % 4]))
    v = _vcd(chip, vcd, {8: "sck", 9: "mosi", 10: "cs_n", 20: "miso"})
    chip.run(10)
    rx = spi_transfer(chip, 0, b"\x9f\x00\x00\x00")
    chip.run(20)
    if v:
        v.close()
    return f"MOSI seen by target: {tgt.transactions[0].hex()}  MISO read: {rx.hex()}"


def demo_i2c(vcd=None):
    clk = 50e6
    chip = Chip(clock_hz=clk)
    chip.set_pulls(up=[0, 1])
    chip.load(firmware("i2c_master"), 0, clkdiv=clk * 0.16e-6)
    tgt = chip.attach(I2cTarget(0, 1, 0x50, stretch_ns=3000, mode="fast"))
    v = _vcd(chip, vcd, {0: "sda", 1: "scl"})
    chip.run(50)
    acks = i2c_write(chip, 0, 0x50, b"\x20JS")
    racks, data = i2c_write_read(chip, 0, 0x50, b"\x20", 2)
    chip.run(500)
    if v:
        v.close()
    return (f"write ACKs {acks}, read back {data!r} (ACKs {racks}), "
            f"timing violations: {len(tgt.violations)}")


def demo_usb(vcd=None):
    chip = Chip(clock_hz=60e6)
    chip.set_pulls(up=[2])
    chip.load(firmware("usb_ls_tx"), 0)
    mon = chip.attach(UsbLsMonitor(2, 3))
    v = _vcd(chip, vcd, {2: "dm", 3: "dp"})
    payload = b"\xff\xff\xffUSB"
    body = bytes([0x80, pid_byte("DATA0")]) + payload
    pad = body + b"\0" * ((-len(body)) % 4)
    words = [len(payload) | (1 << 16)] + [int.from_bytes(pad[i:i + 4], "little") for i in range(0, len(pad), 4)]
    run_host(chip, 0, words, 0)
    chip.run_until(lambda: mon.packets, 50_000)
    if v:
        v.close()
    p = mon.packets[0]
    return f"monitor packet: pid={p.get('pid')} payload={p.get('payload')!r} errors={p['errors'] or 'none'}"


def demo_eth(vcd=None):
    chip = Chip(clock_hz=60e6)
    chip.load(firmware("eth10_tx", NLP_TICKS=20_000), 0)
    mon = chip.attach(Eth10Monitor(4, 5))
    v = _vcd(chip, vcd, {4: "td_p", 5: "td_n"})
    frame = bytes.fromhex("ffffffffffff02000000beef0800") + b"hello from a 6x4 tile chip".ljust(46, b"\0")
    chip.run(25_000)
    run_host(chip, 0, frame_words(frame), 0)
    chip.run_until(lambda: mon.frames, 50_000)
    chip.run(25_000)
    if v:
        v.close()
    f = mon.frames[0]
    return (f"frame FCS ok={f['fcs_ok']}, {len(f['data'])} bytes, preamble bits={f['preamble_bits']}, "
            f"link pulses={len(mon.link_pulses)}")


def demo_jtag(vcd=None):
    chip = Chip(clock_hz=50e6)
    chip.load(firmware("jtag"), 0, clkdiv=2)
    chip.attach(JtagTap(11, 13, 12, 21))
    v = _vcd(chip, vcd, {11: "tck", 12: "tdi", 13: "tms", 21: "tdo"})
    jtag_reset(chip, 0)
    idcode = jtag_shift(chip, 0, False, 0, 32)
    if v:
        v.close()
    return f"IDCODE = {idcode:#010x}"


def demo_sniff(vcd=None):
    clk, baud = 24e6, 1e6
    chip = Chip(clock_hz=clk)
    chip.set_pulls(up=[19])
    drv = chip.attach(UartDriver(19, baud))
    chip.run(5)
    chip.load(firmware("logic_analyzer"), 3)
    v = _vcd(chip, vcd, {19: "probe0"})
    chip.run(20)
    drv.send(b"\x55")
    words = []
    chip.run_until(lambda: words.extend(chip.drain(3)) or (drv.idle and len(words) >= 6), 10_000)
    chip.run(100)
    words.extend(chip.drain(3))
    if v:
        v.close()
    runs = decode_capture(words, 1)
    bit = clk / baud
    return "runs (level, bits): " + ", ".join(f"({lvl},{n / bit:.1f})" for lvl, n in runs)


DEMOS = {
    "uart": demo_uart,
    "spi": demo_spi,
    "i2c": demo_i2c,
    "usb": demo_usb,
    "eth": demo_eth,
    "jtag": demo_jtag,
    "sniff": demo_sniff,
}
