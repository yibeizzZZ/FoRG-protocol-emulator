# SPDX-License-Identifier: Apache-2.0
"""Load firmware through the input pins, then check the output waveform."""
from pathlib import Path

import cocotb
from cocotb.triggers import Timer


async def tick(dut):
    # Drive before the rising edge; sample after sequential logic settles.
    dut.clk.value = 0
    await Timer(5, unit="ns")
    dut.clk.value = 1
    await Timer(5, unit="ns")
    return int(dut.uo_out.value)


async def load_program(dut, program):
    assert 1 <= len(program) <= 8
    assert all(0 <= byte <= 255 for byte in program)
    dut.ui_in.value = 0  # Stop execution and rewind to address 0.
    dut.uio_in.value = 0
    await tick(dut)
    # Fill unused locations with JMP 0; never execute uninitialized memory.
    padded = program + [0x20] * (8 - len(program))
    for address, byte in enumerate(padded):
        dut.ui_in.value = 0x40 | address  # RUN=0, WRITE=1, address[2:0]
        dut.uio_in.value = byte
        await tick(dut)
    dut.ui_in.value = 0x80  # RUN=1, WRITE=0
    dut.uio_in.value = 0


async def check_blink(dut, program):
    # This check permits edits to the two WAIT operands in blink.hex.
    assert len(program) == 5
    assert program[0] == 0x01 and program[2] == 0x00 and program[4] == 0x20
    assert program[1] >> 4 == 1 and program[3] >> 4 == 1
    high_cycles = (program[1] & 0x0F) + 2  # SET + WAIT + extra wait cycles
    low_cycles = (program[3] & 0x0F) + 3   # Also includes JMP
    expected = ([1] * high_cycles + [0] * low_cycles) * 2
    observed = [await tick(dut) for _ in expected]
    assert observed == expected, f"expected {expected}, got {observed}"
    assert int(dut.uio_oe.value) == 0
    assert int(dut.uio_out.value) == 0
    dut._log.info("Output: %s", "".join(map(str, observed)))


@cocotb.test(timeout_time=100, timeout_unit="us")
async def test_load_execute_and_reprogram(dut):
    dut.clk.value = 0
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    assert await tick(dut) == 0
    dut.rst_n.value = 1

    path = Path(__file__).resolve().parents[1] / "firmware" / "blink.hex"
    program = [int(word, 16) for word in path.read_text().split()]
    await load_program(dut, program)
    await check_blink(dut, program)

    # Pausing via ena must hold all execution state.
    dut.ena.value = 0
    for _ in range(3):
        assert await tick(dut) == 0
    dut.ena.value = 1
    assert await tick(dut) == 1

    # Reset rewinds the core but preserves already loaded program memory.
    dut.rst_n.value = 0
    assert await tick(dut) == 0
    dut.rst_n.value = 1
    assert await tick(dut) == 1

    # Same hardware, new firmware. Reprogram without a reset.
    changed = program.copy()
    changed[1] = 0x10 | (((program[1] & 0x0F) + 3) % 16)
    changed[3] = 0x10 | (((program[3] & 0x0F) + 3) % 16)
    await load_program(dut, changed)
    await check_blink(dut, changed)