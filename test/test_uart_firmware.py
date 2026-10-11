"""Cycle-accurate pin tests for M2 firmware, with an isolated fixed-UART oracle."""

from pathlib import Path
import random
import sys

import cocotb
from cocotb.triggers import Timer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from uart_firmware import generate_uart_tx

# Independent requirement constants: do not derive expected timing from firmware.
BIT_CYCLES = 434
FRAME_CYCLES = 4340


async def tick(dut):
    dut.clk.value = 0
    await Timer(10, unit="ns")
    dut.clk.value = 1
    await Timer(10, unit="ns")  # 20 ns period = 50 MHz
    return int(dut.uio_out.value), int(dut.uio_oe.value)


async def reset(dut):
    dut.clk.value = 0
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.ref_rst_n.value = 0
    dut.ref_start.value = 0
    dut.ref_data.value = 0
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
            assert (await tick(dut))[1] == 0, "TX must be released during loading"
    dut.ui_in.value = 0xA0  # RUN and external status readback; never write in RUN.
    dut.uio_in.value = 0


async def driven_tick(dut):
    value, oe = await tick(dut)
    assert oe == 1, f"Only TX should drive, got OE={oe:#04x}"
    assert value in (0, 1), f"Unexpected upper output bits: {value:#04x}"
    assert int(dut.uo_out.value) & 0xC0 == 0, "Engine halted or faulted"
    return value


async def first_start(dut):
    for _ in range(16):
        value, oe = await tick(dut)
        if oe:
            assert oe == 1 and value == 1, "TX must first drive HIGH, without a low glitch"
            break
    else:
        raise AssertionError("Firmware never enabled TX")
    idle_cycles = 1
    for _ in range(BIT_CYCLES + 1):
        if await driven_tick(dut) == 0:
            assert idle_cycles == BIT_CYCLES, f"Idle preamble was {idle_cycles} cycles"
            return
        idle_cycles += 1
    raise AssertionError("Firmware never produced a start bit")


def check_frame(samples, byte):
    assert len(samples) == FRAME_CYCLES
    bits = [0] + [(byte >> bit) & 1 for bit in range(8)] + [1]
    for symbol, value in enumerate(bits):
        interval = samples[symbol * BIT_CYCLES:(symbol + 1) * BIT_CYCLES]
        assert all(sample == value for sample in interval), (
            f"UART waveform mismatch: byte={byte:#04x}, symbol={symbol}, expected={value}"
        )
    decoded = sum(samples[(bit + 1) * BIT_CYCLES + BIT_CYCLES // 2] << bit
                  for bit in range(8))
    assert decoded == byte, f"Receiver decoded {decoded:#04x}, expected {byte:#04x}"


async def capture_firmware(dut, payload, words=None):
    await load_program(dut, generate_uart_tx(payload) if words is None else words)
    await first_start(dut)
    samples = [0]  # The first start edge was sampled by first_start.
    for _ in range(len(payload) * FRAME_CYCLES - 1):
        samples.append(await driven_tick(dut))
    # Contiguous slices enforce exactly one stop bit, with no inter-byte gap.
    for index, byte in enumerate(payload):
        check_frame(samples[index * FRAME_CYCLES:(index + 1) * FRAME_CYCLES], byte)
    for _ in range(2 * BIT_CYCLES):
        assert await driven_tick(dut) == 1, "TX must remain driven HIGH after the last byte"
    return samples


async def compare_fixed_uart(dut, payload, firmware_samples):
    dut.ref_rst_n.value = 1
    for index, byte in enumerate(payload):
        dut.ref_data.value = byte
        dut.ref_start.value = 1
        samples = []
        for cycle in range(FRAME_CYCLES):
            assert await driven_tick(dut) == 1  # M2 independently remains idle.
            assert int(dut.ref_busy.value) == 1
            samples.append(int(dut.ref_tx.value))
            if cycle == 0:
                dut.ref_start.value = 0
                dut.ref_data.value = byte ^ 255  # The reference latches its payload.
        check_frame(samples, byte)
        assert samples == firmware_samples[index * FRAME_CYCLES:(index + 1) * FRAME_CYCLES]
        await driven_tick(dut)
        assert int(dut.ref_busy.value) == 0 and int(dut.ref_tx.value) == 1


@cocotb.test()
async def test_single_byte_values(dut):
    await reset(dut)
    for byte in (0x00, 0x55, 0xAA, 0xFF):
        samples = await capture_firmware(dut, [byte])
        await compare_fixed_uart(dut, [byte], samples)


@cocotb.test()
async def test_consecutive_bytes(dut):
    await reset(dut)
    rng = random.Random(3)
    payloads = [[0x00, 0xFF], [0xFF, 0x00], [0x55, 0xAA], [0xAA, 0xAA]]
    payloads += [[rng.randrange(256), rng.randrange(256)] for _ in range(4)]
    for payload in payloads:
        samples = await capture_firmware(dut, payload)
        await compare_fixed_uart(dut, payload, samples)
    dut._log.info("M3: 434 cycles/bit, 4340 cycles/frame and between consecutive starts")


@cocotb.test()
async def test_checked_in_firmware(dut):
    await reset(dut)
    words = [int(word, 16) for word in (ROOT / "firmware/uart_tx.hex").read_text().split()]
    samples = await capture_firmware(dut, [0x55, 0xAA], words)
    await compare_fixed_uart(dut, [0x55, 0xAA], samples)
    dut._log.info("M3 image: %d words, %d bytes", len(words), 2 * len(words))


@cocotb.test()
async def test_reset_stop_and_reprogram(dut):
    await reset(dut)
    await load_program(dut, generate_uart_tx([0x55, 0xAA]))
    await first_start(dut)
    dut.ui_in.value = 0
    await Timer(1, unit="ns")
    assert int(dut.uio_oe.value) == 0, "Leaving RUN must release TX"
    # Reprogram after aborting, without resetting the engine.
    await capture_firmware(dut, [0xAA, 0x55])
    await load_program(dut, generate_uart_tx([0xFF]))
    await first_start(dut)
    dut.rst_n.value = 0
    await Timer(1, unit="ns")
    assert int(dut.uio_oe.value) == 0, "Reset must release TX immediately"
    await tick(dut)
    dut.rst_n.value = 1
    await tick(dut)
    assert int(dut.uo_out.value) & 0xC0 == 0xC0, "Reset invalidates firmware"
    assert int(dut.uio_oe.value) == 0
    await capture_firmware(dut, [0x00, 0xFF])


@cocotb.test()
async def test_checker_detects_bad_firmware(dut):
    await reset(dut)
    for mutation in ("wait", "payload"):
        words = generate_uart_tx([0x55, 0xAA])
        if mutation == "wait":
            words[9] += 1  # Extend start-bit WAIT by one cycle.
        else:
            words[1] ^= 1  # Flip the first byte's LSB in MOVI R0.
        try:
            await capture_firmware(dut, [0x55, 0xAA], words)
        except AssertionError as error:
            assert "UART waveform mismatch" in str(error), str(error)
        else:
            raise AssertionError(f"Checker accepted the {mutation} mutation")
