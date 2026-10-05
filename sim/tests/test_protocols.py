"""Constrained-random protocol tests: firmware on the golden model vs independent BFMs."""

import zlib

import pytest

from forg import Chip, firmware, isa
from forg.devices.ethernet import Eth10Monitor, frame_words
from forg.devices.i2c import I2cTarget
from forg.devices.jtag import JtagTap
from forg.devices.spi import SpiTarget
from forg.devices.uart import UartDriver, UartMonitor
from forg.devices.usb import (
    UsbLsDriver, UsbLsMonitor, crc16, data_packet, encode_line, handshake_packet, pid_byte, token_packet,
)
from forg.drivers import (
    i2c_read, i2c_write, i2c_write_read, is_status, jtag_reset, jtag_shift, run_host, spi_transfer,
)


@pytest.mark.parametrize("trial", range(4))
def test_uart_tx_random(rng, trial):
    clk = rng.choice([12e6, 24e6, 48e6])
    div = rng.choice([1.0, 1.5, 2.0, 3.25, 6.5])
    baud = clk / (8 * div)
    chip = Chip(clock_hz=clk)
    chip.load(firmware("uart_tx"), 0, clkdiv=div)
    mon = chip.attach(UartMonitor(8, baud))
    msg = bytes(rng.randrange(256) for _ in range(rng.randint(4, 16)))
    run_host(chip, 0, list(msg), 0)
    chip.run_until(lambda: len(mon.frames) == len(msg), 100_000)
    assert mon.bytes == msg
    assert mon.framing_errors == 0
    # fractional dividers jitter by at most one system clock per edge
    assert mon.max_edge_error <= (1e9 / clk) / (1e9 / baud) + 1e-9


@pytest.mark.parametrize("trial", range(4))
def test_uart_rx_random_baud_error(rng, trial):
    clk = 48e6
    div = rng.choice([1.0, 2.0, 2.5, 4.0])
    err = rng.uniform(-0.03, 0.03)
    chip = Chip(clock_hz=clk)
    chip.load(firmware("uart_rx"), 1, clkdiv=div)
    drv = chip.attach(UartDriver(19, clk / (8 * div), baud_error=err, gap_bits=rng.choice([0, 0, 0.5, 3])))
    msg = bytes(rng.randrange(256) for _ in range(rng.randint(4, 20)))
    drv.send(msg)
    got = []
    chip.run_until(lambda: got.extend(w >> 24 for w in chip.drain(1)) or len(got) == len(msg), 200_000)
    assert bytes(got) == msg, f"baud error {err:+.2%}"
    assert not chip.irq & (1 << 4)


def test_uart_rx_framing_error_raises_irq():
    clk = 24e6
    chip = Chip(clock_hz=clk)
    chip.load(firmware("uart_rx"), 1, clkdiv=1)
    drv = chip.attach(UartDriver(19, clk / 8, gap_bits=2))
    drv.send(b"\x00", bad_stop=True)
    drv.send(b"\x42")
    got = []
    chip.run_until(lambda: got.extend(w >> 24 for w in chip.drain(1)) or got, 20_000)
    assert chip.irq & (1 << 4)
    assert got == [0x42]


@pytest.mark.parametrize("cpha", [0, 1])
@pytest.mark.parametrize("cpol", [0, 1])
def test_spi_all_modes_random(rng, cpol, cpha):
    div = rng.choice([1.0, 2.0, 3.0, 5.5])
    chip = Chip(clock_hz=50e6)
    chip.load(firmware(f"spi_cpha{cpha}", CPOL=cpol), 0, clkdiv=div)
    resp = [rng.randrange(256) for _ in range(64)]
    tgt = chip.attach(SpiTarget(8, 9, 20, 10, cpol=cpol, cpha=cpha, respond=lambda i, rx: resp[i]))
    chip.run(10)
    sent = []
    for _ in range(3):
        data = bytes(rng.randrange(256) for _ in range(rng.randint(1, 9)))
        rx = spi_transfer(chip, 0, data)
        assert rx == bytes(resp[: len(data)])
        sent.append(data)
    chip.run(40)
    assert tgt.transactions == sent
    assert tgt.mode_errors == 0
    assert not chip.contention


@pytest.mark.parametrize("mode,tick_us", [("standard", 0.65), ("fast", 0.16)])
def test_i2c_random_transactions(rng, mode, tick_us):
    clk = 50e6
    chip = Chip(clock_hz=clk)
    chip.set_pulls(up=[0, 1])
    chip.load(firmware("i2c_master"), 0, clkdiv=clk * tick_us * 1e-6)
    addr = rng.randrange(0x08, 0x78)
    stretch = rng.choice([0, 2000, 15000])
    tgt = chip.attach(I2cTarget(0, 1, addr, stretch_ns=stretch, mode=mode))
    chip.run(50)
    model = bytearray(256)
    for _ in range(3):
        reg = rng.randrange(250)
        data = bytes(rng.randrange(256) for _ in range(rng.randint(1, 4)))
        assert i2c_write(chip, 0, addr, bytes([reg]) + data) == [0] * (len(data) + 2)
        model[reg:reg + len(data)] = data
        acks, back = i2c_write_read(chip, 0, addr, bytes([reg]), len(data))
        assert acks == [0, 0, 0]
        assert back == bytes(model[reg:reg + len(data)])
    assert bytes(tgt.mem) == bytes(model)
    assert tgt.violations == []
    assert not chip.contention


def test_i2c_nak_on_wrong_address():
    clk = 50e6
    chip = Chip(clock_hz=clk)
    chip.set_pulls(up=[0, 1])
    chip.load(firmware("i2c_master"), 0, clkdiv=clk * 0.65e-6)
    tgt = chip.attach(I2cTarget(0, 1, 0x50))
    assert i2c_write(chip, 0, 0x51, b"\x00") == [1, 1]
    ack, _ = i2c_read(chip, 0, 0x22, 1)
    assert ack == 1
    chip.run(1000)
    assert not [e for e in tgt.events if e[0] == "A"]
    assert [e[0] for e in tgt.events].count("P") == 2
    assert tgt.violations == []


def _usb_tx_words(pid, payload, with_crc=True):
    body = bytes([0x80, pid_byte(pid)]) + bytes(payload)
    pad = body + b"\0" * ((-len(body)) % 4)
    return [len(payload) | (int(with_crc) << 16)] + [
        int.from_bytes(pad[i:i + 4], "little") for i in range(0, len(pad), 4)
    ]


def test_usb_ls_tx_random_packets(rng):
    chip = Chip(clock_hz=60e6)
    chip.set_pulls(up=[2])
    chip.load(firmware("usb_ls_tx"), 0)
    mon = chip.attach(UsbLsMonitor(2, 3))
    expect = []
    for i in range(6):
        n = rng.randint(0, 8)
        payload = bytes(rng.choice([0xFF, 0x7F, 0x00, rng.randrange(256)]) for _ in range(n))
        pid = rng.choice(["DATA0", "DATA1"])
        run_host(chip, 0, _usb_tx_words(pid, payload), 0)
        expect.append(payload)
    run_host(chip, 0, _usb_tx_words("ACK", b"", with_crc=False), 0)
    chip.run_until(lambda: len(mon.packets) == 7, 200_000)
    assert [p.get("payload") for p in mon.packets[:6]] == expect
    assert all(p["ok"] for p in mon.packets), [p["errors"] for p in mon.packets]
    assert mon.packets[6]["pid"] == 0x2
    assert mon.errors == []


def _collect_usb(chip, sm, words):
    pkts, cur = [], []
    for w in words:
        if is_status(w):
            pkts.append((bytes(cur), w))
            cur = []
        else:
            cur.append(w >> 24)
    return pkts


def test_usb_ls_rx_random_with_clock_error(rng):
    chip = Chip(clock_hz=60e6)
    chip.set_pulls(up=[2])
    chip.load(firmware("usb_ls_rx"), 1)
    drv = chip.attach(UsbLsDriver(2, 3, rate_error=rng.uniform(-0.015, 0.015)))
    sent = []
    for _ in range(5):
        kind = rng.choice(["data", "token", "hs"])
        if kind == "data":
            p = data_packet(rng.choice(["DATA0", "DATA1"]), bytes(rng.choice([0xFF, rng.randrange(256)]) for _ in range(rng.randint(0, 8))))
        elif kind == "token":
            p = token_packet(rng.choice(["IN", "OUT", "SETUP"]), rng.randrange(128), rng.randrange(16))
        else:
            p = handshake_packet(rng.choice(["ACK", "NAK", "STALL"]))
        drv.send(p)
        sent.append(p)
    words = []
    chip.run_until(lambda: words.extend(chip.drain(1)) or sum(map(is_status, words)) == len(sent), 300_000)
    pkts = _collect_usb(chip, 1, words)
    assert [p for p, _ in pkts] == sent
    for _, st in pkts:
        assert st & isa.ST_RX_EOP and not st & isa.ST_RX_STUFF_ERR


def test_usb_ls_rx_detects_stuff_error():
    chip = Chip(clock_hz=60e6)
    chip.set_pulls(up=[2])
    chip.load(firmware("usb_ls_rx"), 1)
    drv = chip.attach(UsbLsDriver(2, 3))
    states = encode_line(bytes([0x80, 0x4B]))[:-3]
    states += [states[-1]] * 8 + ["0", "0", "J"]  # eight 1s with no stuff bit
    drv.queue.append((states, 4))
    words = []
    chip.run_until(lambda: words.extend(chip.drain(1)) or any(map(is_status, words)), 50_000)
    st = [w for w in words if is_status(w)][0]
    assert st & isa.ST_RX_STUFF_ERR


def test_usb_crc_reference_vectors():
    assert crc16(b"123456789") == 0xB4C8
    assert token_packet("SETUP", 0, 0)[2:] == bytes([0x00, 0x10])


def test_ethernet_random_frames_and_link_pulses(rng):
    chip = Chip(clock_hz=60e6)
    chip.load(firmware("eth10_tx", NLP_TICKS=6000), 0)
    mon = chip.attach(Eth10Monitor(4, 5))
    chip.run(14_000)
    frames = []
    for _ in range(3):
        n = rng.randint(60, 90)
        f = bytes(rng.randrange(256) for _ in range(n))
        run_host(chip, 0, frame_words(f), 0)
        frames.append(f)
    chip.run_until(lambda: len(mon.frames) == 3, 200_000)
    assert [f["data"] for f in mon.frames] == frames
    assert all(f["fcs_ok"] and f["preamble_bits"] == 56 for f in mon.frames)
    assert mon.errors == []
    assert len(mon.link_pulses) >= 2
    assert all(80 <= w <= 120 for _, w in mon.link_pulses)
    gaps = [b[0] - a[0] for a, b in zip(mon.link_pulses, mon.link_pulses[1:])]
    nominal = 6000 * 1e9 / 60e6
    assert gaps and all(abs(g - nominal) / nominal < 0.01 for g in gaps if g < 2 * nominal)


def test_jtag_idcode_ir_and_user_dr(rng):
    chip = Chip(clock_hz=50e6)
    chip.load(firmware("jtag"), 0, clkdiv=rng.choice([1, 2, 3]))
    idcode = (rng.randrange(1 << 31) << 1) | 1
    tap = chip.attach(JtagTap(11, 13, 12, 21, idcode=idcode))
    jtag_reset(chip, 0)
    assert jtag_shift(chip, 0, False, 0, 32) == idcode
    assert jtag_shift(chip, 0, True, 0b0010, 4) == 0b0101
    v1, v2 = rng.randrange(1 << 16), rng.randrange(1 << 16)
    jtag_shift(chip, 0, False, v1, 16)
    assert tap.user == v1
    assert jtag_shift(chip, 0, False, v2, 16) == v1
    assert tap.state == "RTI"


def test_four_protocols_concurrently(rng):
    """UART, SPI, I2C and a logic analyzer share one imem and run at once."""
    clk = 48e6
    chip = Chip(clock_hz=clk)
    chip.set_pulls(up=[0, 1, 19])
    chip.load(firmware("uart_tx"), 0, clkdiv=2)
    # SPI moved to SCK=uo4, MOSI=uo5, CS_n=uo6 purely through config
    chip.load(firmware("spi_cpha0"), 1, clkdiv=2, side_base=12, out_base=13, set_base=14, pins=1 << 14)
    chip.load(firmware("i2c_master"), 2, clkdiv=clk * 0.16e-6)
    chip.load(firmware("logic_analyzer"), 3)
    assert sum(u is not None for u in chip.used) <= isa.IMEM_SIZE
    uart = chip.attach(UartMonitor(8, clk / 16))
    spi = chip.attach(SpiTarget(12, 13, 20, 14))
    i2c = chip.attach(I2cTarget(0, 1, 0x3C, mode="fast"))
    probe = chip.attach(UartDriver(19, 1e6))
    probe.send(b"\xa5")
    captured = []
    chip.watchers.append(lambda c, n: captured.extend(chip.drain(3)))

    msg = bytes(rng.randrange(256) for _ in range(4))
    for b in msg:
        chip.put(0, b)
    assert i2c_write(chip, 2, 0x3C, b"\x05\x99") == [0, 0, 0]
    assert spi_transfer(chip, 1, b"\x01\x02\x03") == b"\xa5\xa6\xa7"
    chip.run_until(lambda: len(uart.frames) == len(msg), 100_000)
    chip.run(200)
    assert uart.bytes == msg
    assert i2c.mem[5] == 0x99
    assert spi.transactions == [b"\x01\x02\x03"]
    assert len(captured) >= 9
    assert not chip.contention
