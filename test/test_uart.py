"""Independent pin-level UART checks; valid for RTL and gate netlists."""
import os
import random

import cocotb
from cocotb.triggers import Timer

DIVISOR = int(os.getenv("UART_CLKS_PER_BIT", "434"))


async def tick(dut):
    dut.clk.value = 0
    await Timer(5, unit="ns")
    dut.clk.value = 1
    await Timer(5, unit="ns")
    return int(dut.uo_out.value)


async def reset(dut):
    dut.clk.value = 0
    dut.rst_n.value = 0
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    assert await tick(dut) == 1
    dut.rst_n.value = 1


async def send_and_decode(dut, byte, inject_busy_request=False):
    dut.ui_in.value = byte
    dut.uio_in.value = 1
    samples = [await tick(dut)]
    dut.uio_in.value = 0
    # Changing the input after acceptance must not change the latched payload.
    dut.ui_in.value = byte ^ 0xFF
    for cycle in range(1, 10 * DIVISOR):
        dut.uio_in.value = int(inject_busy_request and cycle == 3 * DIVISOR)
        samples.append(await tick(dut))
    dut.uio_in.value = 0
    bits = [0] + [(byte >> i) & 1 for i in range(8)] + [1]
    for bit_index, bit in enumerate(bits):
        interval = samples[bit_index * DIVISOR:(bit_index + 1) * DIVISOR]
        assert all(s == (2 | bit) for s in interval), (
            f"byte={byte:#04x}, bit={bit_index}, expected TX={bit}, samples={interval}"
        )
    # Decode at bit centers as a separate receiver would.
    decoded = sum((samples[(i + 1) * DIVISOR + DIVISOR // 2] & 1) << i for i in range(8))
    assert decoded == byte
    assert await tick(dut) == 1, "Busy did not clear after the complete stop bit"
    assert int(dut.uio_oe.value) == 0
    assert int(dut.uio_out.value) == 0


@cocotb.test()
async def test_payloads_and_timing(dut):
    await reset(dut)
    rng = random.Random(20261001)
    for byte in [0x00, 0x55, 0xAA, 0xFF] + [rng.randrange(256) for _ in range(8)]:
        await send_and_decode(dut, byte, inject_busy_request=True)


@cocotb.test()
async def test_reset_abort_and_enable(dut):
    await reset(dut)
    dut.ena.value = 0
    dut.uio_in.value = 1
    for _ in range(3):
        assert await tick(dut) == 1
    dut.ena.value = 1
    dut.ui_in.value = 0x55
    assert await tick(dut) == 2
    dut.uio_in.value = 0
    for _ in range(2 * DIVISOR):
        await tick(dut)
    dut.rst_n.value = 0
    assert await tick(dut) == 1
    dut.rst_n.value = 1
    await send_and_decode(dut, 0xAA)


@cocotb.test()
async def test_held_start_retries_when_idle(dut):
    await reset(dut)
    dut.ui_in.value = 0xFF
    dut.uio_in.value = 1
    assert await tick(dut) == 2
    for _ in range(10 * DIVISOR - 1):
        await tick(dut)
    assert await tick(dut) == 1
    assert await tick(dut) == 2, "Held start must be accepted on the next idle edge"
