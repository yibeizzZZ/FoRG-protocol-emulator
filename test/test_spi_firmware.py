"""Full-duplex SPI firmware verified through GPIO and the public debug mux."""

import json
from pathlib import Path
import random
import sys

import cocotb
from cocotb.triggers import Timer

from spi_reference import SpiSlave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from spi_firmware import generate_spi_master

# Independent expectations, including both sides of WAIT-size transitions.
TIMING_SWEEPS = (
    (13, 14, 16, 32, 262, 263, 269),
    (16, 17, 32, 259, 260, 271, 272),
    (14, 15, 16, 32, 261, 262),
    (15, 16, 32, 259, 260, 271, 272, 514, 515),
)


async def tick(dut):
    dut.clk.value = 0
    await Timer(10, unit="ns")
    dut.clk.value = 1
    await Timer(10, unit="ns")
    return int(dut.uio_out.value), int(dut.uio_oe.value)


async def reset(dut):
    dut.clk.value = 0
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    assert (await tick(dut))[1] == 0
    dut.rst_n.value = 1


async def load_program(dut, words):
    assert 1 <= len(words) <= 32
    dut.ui_in.value = 0
    assert (await tick(dut))[1] == 0
    for address, word in enumerate(words):
        for high in (0, 1):
            dut.ui_in.value = 0x40 | (high << 5) | address
            dut.uio_in.value = (word >> (8 * high)) & 255
            assert (await tick(dut))[1] == 0, "SPI pins must be released during loading"
    dut.ui_in.value = 0xA0  # RUN, with external halt/fault/PC status selected.
    dut.uio_in.value = 0


async def read_register(dut, register):
    dut.ui_in.value = 0xC0 | register
    await Timer(1, unit="ns")
    value = int(dut.uo_out.value)
    dut.ui_in.value = 0xA0
    await Timer(1, unit="ns")
    return value


async def transfer(dut, tx, reply, half_period=16, words=None, *, mode=0,
                   sample_hold_cycles=None, response_delay_cycles=0):
    assert len(tx) == len(reply)
    words = generate_spi_master(tx, half_period_cycles=half_period, mode=mode) if words is None else words
    await load_program(dut, words)
    slave = SpiSlave(reply, half_period, mode, sample_hold_cycles=sample_hold_cycles,
                     response_delay_cycles=response_delay_cycles)
    # Only the observed wire edges drive the slave, never firmware PCs or delays.
    for cycle in range(len(tx) * 16 * half_period + 64):
        pins, oe = await tick(dut)
        assert int(dut.uo_out.value) & 0xC0 == 0, "SPI engine halted or faulted"
        miso = slave.observe(cycle, pins, oe)
        # Include readback of driven pins and noisy unused inputs: ANDI must
        # isolate MISO instead of treating any nonzero input byte as a one.
        noise = (cycle * 0x18) & 0x78
        dut.uio_in.value = (pins & oe) | noise | miso
        if slave.complete:
            break
    else:
        raise AssertionError("SPI transaction did not complete")
    assert slave.received == tx, f"SPI MOSI bytes mismatch: {slave.received} != {tx}"
    last_rx = await read_register(dut, 0)
    first_rx = await read_register(dut, 3)
    received = [first_rx, last_rx] if len(tx) == 2 else [last_rx]
    assert received == reply, f"SPI RX bytes mismatch: {received} != {reply}"
    if len(tx) == 1:
        assert first_rx == 0, "Single-byte transfer must not use the second-byte dispatch"
    hold = 10 if mode & 1 else 6
    assert slave.cs_released - slave.trailing_edges[-1] == hold, "SPI CS hold mismatch"
    assert slave.leading_edges[0] - slave.cs_asserted == half_period, "SPI CS setup mismatch"
    assert slave.cs_released - slave.cs_asserted == len(tx) * 16 * half_period + hold
    assert all(b - a == 2 * half_period for a, b in zip(slave.rising_edges, slave.rising_edges[1:])), (
        "SPI bit periods, including the byte boundary, must be uniform"
    )
    for _ in range(2 * half_period):
        pins, oe = await tick(dut)
        assert (pins, oe) == (0x04 | (0x02 if mode >> 1 else 0), 0x86), "SPI must retain deselected idle outputs"
        assert int(dut.uo_out.value) & 0xC0 == 0
    assert await read_register(dut, 0) == last_rx, "Final RX must persist until reprogram/reset"
    assert await read_register(dut, 3) == first_rx, "First RX must persist until reprogram/reset"
    return slave


@cocotb.test()
@cocotb.parametrize(mode=[0, 1, 2, 3])
async def test_arbitrary_full_duplex_bytes(dut, mode):
    await reset(dut)
    rng = random.Random(381)
    cases = [(0x00, 0xFF), (0xFF, 0x00), (0x55, 0xAA), (0xAA, 0x55), (0x81, 0x96), (0x3C, 0x69)]
    cases += [(rng.randrange(256), rng.randrange(256)) for _ in range(8)]
    for tx, rx in cases:
        await transfer(dut, [tx], [rx], mode=mode)


@cocotb.test()
@cocotb.parametrize(mode=[0, 1, 2, 3])
async def test_two_bytes_and_timing_sweep(dut, mode):
    await reset(dut)
    rng = random.Random(382)
    measurements = []
    for half_period in TIMING_SWEEPS[mode]:
        await transfer(dut, [0x81], [0x69], half_period, mode=mode)
        cases = [([0x00, 0xFF], [0xFF, 0x00]), ([0xFF, 0x00], [0x00, 0xFF]),
                 ([0x55, 0xAA], [0x3C, 0xA5]),
                 ([rng.randrange(256), rng.randrange(256)], [rng.randrange(256), rng.randrange(256)])]
        for tx, rx in cases:
            slave = await transfer(dut, tx, rx, half_period, mode=mode)
        period = slave.rising_edges[1] - slave.rising_edges[0]
        measurements.append({
            "mode": mode,
            "half_period_cycles": half_period,
            "firmware_words": len(generate_spi_master([0, 0], half_period_cycles=half_period, mode=mode)),
            "measured_high_cycles": slave.high_cycles[0],
            "measured_low_cycles": slave.low_cycles[0],
            "cycles_per_bit": period,
            "sclk_hz_at_50mhz": 50_000_000 / period,
            "transactions_verified": len(cases) + 1,
        })
    output = ROOT / f"test/output/spi-mode{mode}-measurements.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(measurements, indent=2) + "\n")
    dut._log.info("SPI measured timing and size: %s", measurements)


@cocotb.test()
@cocotb.parametrize(mode=[0, 1, 2, 3])
async def test_example_and_successive_transactions(dut, mode):
    await reset(dut)
    words = ([int(word, 16) for word in (ROOT / "firmware/spi_master.hex").read_text().split()]
             if mode == 0 else generate_spi_master([0xA5, 0x3C], mode=mode))
    await transfer(dut, [0xA5, 0x3C], [0x12, 0x34], words=words, mode=mode)
    # New transaction, no hardware reset. Old RX and host loading state must clear.
    await transfer(dut, [0x8D, 0x76], [0x44, 0x87], mode=mode)
    await transfer(dut, [0x00], [0x69], mode=mode)


async def wait_for_active_clock(dut, mode):
    for _ in range(64):
        pins, oe = await tick(dut)
        if oe == 0x86 and pins & 0x06 == (0 if mode >> 1 else 0x02):
            return
    raise AssertionError("SPI did not reach an active clock phase")


@cocotb.test()
@cocotb.parametrize(mode=[0, 1, 2, 3])
async def test_reset_stop_and_reprogram(dut, mode):
    await reset(dut)
    for abort in ("reset", "stop"):
        await load_program(dut, generate_spi_master([0xA5, 0x3C], mode=mode))
        await wait_for_active_clock(dut, mode)
        if abort == "reset":
            dut.rst_n.value = 0
        else:
            dut.ui_in.value = 0
        await Timer(1, unit="ns")
        assert int(dut.uio_oe.value) == 0, "Aborting must release all SPI outputs"
        await tick(dut)
        if abort == "reset":
            dut.rst_n.value = 1
            await tick(dut)
            assert int(dut.uo_out.value) & 0xC0 == 0xC0, "Reset must invalidate firmware"
        await transfer(dut, [0x37, 0x92], [0x81, 0x7E], mode=mode)


@cocotb.test()
@cocotb.parametrize(mode=[0, 1, 2, 3])
async def test_pause_holds_outputs(dut, mode):
    await reset(dut)
    await load_program(dut, generate_spi_master([0xAA], mode=mode))
    await wait_for_active_clock(dut, mode)
    before = (int(dut.uio_out.value), int(dut.uio_oe.value), int(dut.uo_out.value))
    dut.ena.value = 0
    for _ in range(5):
        await tick(dut)
        assert (int(dut.uio_out.value), int(dut.uio_oe.value), int(dut.uo_out.value)) == before
    # A pause stretches wire timing. Abort rather than claiming it was a valid frame.
    dut.ui_in.value = 0
    dut.ena.value = 1
    await transfer(dut, [0x55], [0xB4], mode=mode)


@cocotb.test()
@cocotb.parametrize(mode=[0, 1, 2, 3])
async def test_mutations_detect_data_timing_and_cs_errors(dut, mode):
    await reset(dut)
    # Locate instruction sites solely to corrupt the loaded program. The
    # independent wire checker knows nothing about these addresses/opcodes.
    baseline = generate_spi_master([0xA5, 0x3C], mode=mode)
    read = baseline.index(0x3400)
    rx_branch = next(i for i, word in enumerate(baseline) if word & 0xFF00 == 0x6400)
    mutations = [
        ("TX MSB", 2, 0x4025, "MOSI bytes mismatch"),
        ("initial WAIT", 5, baseline[5] + 1, "half-period mismatch"),
        ("MISO mask", read + 1, 0xB400, "RX bytes mismatch"),
        ("missing READ", read, 0xF000, "RX bytes mismatch"),
        ("early CS", read, 0x0004 | (0x02 if mode >> 1 else 0), "SPI CS"),
        ("RX branch", rx_branch, 0xF000, "RX bytes mismatch"),
    ]
    for name, address, replacement, expected_error in mutations:
        words = baseline.copy()
        assert words[address] != replacement, name
        words[address] = replacement
        try:
            await transfer(dut, [0xA5, 0x3C], [0x96, 0x69], words=words, mode=mode)
        except AssertionError as error:
            assert expected_error in str(error), f"{name}: unexpected failure {error}"
        else:
            raise AssertionError(f"Checker accepted the {name} mutation")


@cocotb.test()
@cocotb.parametrize(mode=[0, 1, 2, 3])
async def test_miso_sample_window(dut, mode):
    """Late replies and short hold windows expose premature and late READs."""
    await reset(dut)
    for half in (TIMING_SWEEPS[mode][0], TIMING_SWEEPS[mode][-1]):
        for tx, rx in (([0xA5], [0x96]), ([0x81, 0x7E], [0x69, 0x96])):
            await transfer(dut, tx, rx, half, mode=mode, sample_hold_cycles=1,
                           response_delay_cycles=half - 1)
    # Capture into R3 during the balance delay, then use that value instead
    # of READ at the sample edge. Shorten WAIT by one to preserve wire timing;
    # relocate branch targets after inserting READ. Use one byte because R3
    # is the mutation's scratch register. Every image still fits 32 words.
    half = TIMING_SWEEPS[mode][0]
    words = generate_spi_master([0xA5], mode=mode, half_period_cycles=half)
    read = words.index(0x3400)
    assert words[6] == 0x1003
    words[6] = 0x1002
    words[read] = 0x5700  # MOV R1,R3
    words = [word + 1 if word >> 12 in (2, 6) and word & 0xFF >= 7 else word
             for word in words]
    words.insert(7, 0x3C00)  # READ R3
    try:
        await transfer(dut, [0xA5], [0x00], half, words=words, mode=mode,
                       sample_hold_cycles=1, response_delay_cycles=half - 1)
    except AssertionError as error:
        assert "RX bytes mismatch" in str(error), f"Early READ: unexpected failure {error}"
    else:
        raise AssertionError("Checker accepted premature MISO capture")


@cocotb.test()
@cocotb.parametrize(mode=[0, 1, 2, 3])
async def test_wrong_mode_rejected(dut, mode):
    """A checker that silently accepts another CPOL/CPHA must fail this test."""
    await reset(dut)
    for wrong_mode in range(4):
        if wrong_mode == mode:
            continue
        words = generate_spi_master([0xA5, 0x3C], mode=wrong_mode)
        try:
            await transfer(dut, [0xA5, 0x3C], [0x96, 0x69], words=words, mode=mode)
        except AssertionError as error:
            assert "SPI" in str(error)
        else:
            raise AssertionError(f"Mode {mode} checker accepted mode {wrong_mode}")
