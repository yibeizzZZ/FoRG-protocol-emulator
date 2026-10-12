"""Transactional loader tests use only Tiny Tapeout pins, including gate runs."""
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import Timer

from test_core import packet_controls
from spi_reference import Mode0Slave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from uart_firmware import generate_uart_tx
from spi_firmware import generate_spi_master
from pio_host import program_writes, data_writes


async def tick(dut):
    dut.clk.value = 0
    await Timer(10, unit='ns')
    dut.clk.value = 1
    await Timer(10, unit='ns')
    return int(dut.uio_out.value), int(dut.uio_oe.value)


async def reset(dut):
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    await tick(dut)
    dut.rst_n.value = 1


async def command(dut, control, hold=1):
    dut.ui_in.value = control
    for _ in range(hold):
        await tick(dut)
        assert int(dut.uio_oe.value) == 0, 'Loader must release all bus pins'


async def packet(dut, address, value, hold=1):
    await command(dut, 0x1E)
    for control in packet_controls(address, value):
        await command(dut, control, hold)


async def status(dut):
    dut.ui_in.value = 0x1D
    await Timer(1, unit='ns')
    return int(dut.uo_out.value)


async def upload(dut, words, extended=False):
    await packet(dut, 0xFE, len(words) | (int(extended) << 8))
    assert await status(dut) == 0x80
    for address, word in enumerate(words):
        await packet(dut, address, word, hold=2)
    await packet(dut, 0xFD, 0)
    assert await status(dut) == 0xC0


async def blocked(dut):
    dut.ui_in.value = 0xA0
    for _ in range(5):
        await tick(dut)
        assert int(dut.uio_oe.value) == 0
        assert int(dut.uio_out.value) == 0
        assert int(dut.uo_out.value) == 0xC0, 'No instruction may execute'


@cocotb.test()
async def test_loader_reset_incomplete_and_recovery(dut):
    await reset(dut)
    assert await status(dut) == 0
    await packet(dut, 0xFE, 3)
    await packet(dut, 0, 0xA0FF)  # Even a fully loaded prefix must not execute.
    await blocked(dut)
    await upload(dut, [0xA001, 0x005A, 0x2002])
    dut.ui_in.value = 0xA0
    for _ in range(3):
        await tick(dut)
    assert int(dut.uio_out.value) == 0x5A
    assert int(dut.uio_oe.value) == 1
    # Replacement invalidates the old image before the first code write.
    await packet(dut, 0xFE, 3)
    await packet(dut, 0, 0xA0FF)
    for control in packet_controls(1, 0x00FF)[:5]:
        await command(dut, control)
    await blocked(dut)
    await reset(dut)
    assert await status(dut) == 0
    await blocked(dut)


@cocotb.test()
async def test_loader_malformed_bounds_and_commit(dut):
    await reset(dut)
    for length in (0, 33, 129, 0x181, 0x201):
        await packet(dut, 0xFE, length)
        assert await status(dut) == 0xA0
        await blocked(dut)
    for address, value in ((1, 0xD000), (0x7F, 0xD000), (0xA0, 0),
                           (0xFF, 1), (0x80, 0x100), (0xFD, 1)):
        await packet(dut, 0xFE, 1)
        await packet(dut, address, value)
        assert await status(dut) == 0xA0
        await blocked(dut)
    await packet(dut, 0xFE, 2)
    await packet(dut, 0, 0xD000)
    await packet(dut, 0xFD, 0)  # Missing final word.
    assert await status(dut) == 0xA0
    await blocked(dut)
    for extra in ((0, 0xD000), (1, 0xD000)):
        await packet(dut, 0xFE, 1)
        await packet(dut, 0, 0xD000)
        await packet(dut, *extra)  # Duplicate or overlong image.
        await packet(dut, 0xFD, 0)
        await blocked(dut)
    # A shorter replacement cannot execute stale tail instructions.
    await upload(dut, [0xF000, 0x00FF, 0xA0FF])
    await upload(dut, [0x2001])
    dut.ui_in.value = 0xA0
    for _ in range(2):
        await tick(dut)
    assert int(dut.uo_out.value) == 0xC1
    assert int(dut.uio_out.value) == 0


@cocotb.test()
async def test_loader_pause_abort_maximum_and_legacy_lock(dut):
    await reset(dut)
    await packet(dut, 0xFE, 0x180)
    # Paused command samples do not advance the frame; held strobes count once.
    dut.ena.value = 0
    await packet(dut, 0, 0xFFFF, hold=3)
    dut.ena.value = 1
    await command(dut, 0x1E)
    for control in packet_controls(0, 0xFFFF)[:3]:
        await command(dut, control)
    await command(dut, 0x1E)  # Discard an incomplete packet, not the transaction.
    for address in range(128):
        await packet(dut, address, 0x207F if address == 0 else 0xD000, hold=3)
    await packet(dut, 0xFD, 0)
    assert await status(dut) == 0xC0
    dut.ui_in.value = 0xA0
    for _ in range(2):
        await tick(dut)
    assert int(dut.uo_out.value) == 0x5F  # HALT at 127.
    for control in (0x40, 0x1F):
        await upload(dut, [0xA0FF, 0x00FF])
        await command(dut, control)
        assert await status(dut) == 0xA0
        await blocked(dut)


@cocotb.test()
async def test_uart_then_spi_same_build_no_reset(dut):
    await reset(dut)
    dut.uio_in.value = 0xA5  # Upload never uses these pins for data.
    await upload(dut, generate_uart_tx([0x96]))
    dut.ui_in.value = 0xA0
    for _ in range(450):
        pins, oe = await tick(dut)
        if oe == 1 and pins == 0:
            break
    else:
        raise AssertionError('UART start bit missing')
    samples = [0]
    for _ in range(4340 - 1):
        pins, oe = await tick(dut)
        assert oe == 1
        samples.append(pins)
    for index, bit in enumerate([0] + [(0x96 >> i) & 1 for i in range(8)] + [1]):
        assert samples[index * 434:(index + 1) * 434] == [bit] * 434
    # Exact same DUT and build, no reset: replace UART with full-duplex SPI.
    await upload(dut, generate_spi_master([0x3C], half_period_cycles=16))
    dut.ui_in.value = 0xA0
    dut.uio_in.value = 0
    slave = Mode0Slave([0xA7], 16)
    for cycle in range(400):
        pins, oe = await tick(dut)
        assert int(dut.uo_out.value) & 0xC0 == 0
        dut.uio_in.value = (pins & oe) | slave.observe(cycle, pins, oe)
        if slave.complete:
            break
    assert slave.complete and slave.received == [0x3C]
    dut.ui_in.value = 0xC0
    await Timer(1, unit='ns')
    assert int(dut.uo_out.value) == 0xA7


@cocotb.test()
async def test_loader_host_codec_data_and_reset(dut):
    await reset(dut)
    for control in program_writes([0xE600, 0x9000, 0xD000], extended=True,
                                  transactional=True):
        await command(dut, control)
    assert await status(dut) == 0xC0
    for selector, expected in ((0x1B, 3), (0x1C, 3)):
        dut.ui_in.value = selector
        await Timer(1, unit='ns')
        assert int(dut.uo_out.value) == expected
    for control in data_writes([0x5A]):
        await command(dut, control)
    dut.ui_in.value = 0xA0
    for _ in range(3):
        await tick(dut)
    assert int(dut.uio_out.value) == 0x5A
    assert int(dut.uo_out.value) == 0x42
    # A new image invalidates old RAM as well as code.
    await upload(dut, [0xE600], extended=True)
    await blocked(dut)
    # Reset dominates ENA, even in the middle of a packet.
    await packet(dut, 0xFE, 1)
    for control in packet_controls(0, 0xD000)[:3]:
        await command(dut, control)
    dut.ena.value = 0
    dut.rst_n.value = 0
    await tick(dut)
    assert await status(dut) == 0
    dut.rst_n.value = 1
    dut.ena.value = 1
    await blocked(dut)


@cocotb.test()
async def test_loader_commit_gate_and_post_commit_edits(dut):
    await reset(dut)
    await packet(dut, 0xFE, 1)
    await packet(dut, 0, 0xA0FF)
    await blocked(dut)  # All words present is insufficient without COMMIT.
    for edit in ('word', 'partial', 'duplicate_commit'):
        await upload(dut, [0xA0FF, 0x00FF])
        if edit == 'word':
            await packet(dut, 0, 0xD000)
        elif edit == 'partial':
            for control in packet_controls(0xFE, 1)[:5]:
                await command(dut, control)
        else:
            await packet(dut, 0xFD, 0)
        await blocked(dut)
    # A paused COMMIT's last nibble is accepted once when ENA resumes.
    await packet(dut, 0xFE, 1)
    await packet(dut, 0, 0xD000)
    await command(dut, 0x1E)
    controls = packet_controls(0xFD, 0)
    for control in controls[:5]:
        await command(dut, control)
    dut.ena.value = 0
    await command(dut, controls[5], hold=3)
    assert await status(dut) == 0x80
    dut.ena.value = 1
    await command(dut, controls[5], hold=3)
    assert await status(dut) == 0xC0
    dut.ui_in.value = 0xA0
    await tick(dut)
    assert int(dut.uo_out.value) == 0x40
