"""Pin-only ISA regression and differential checks for RTL and gate netlists."""
from pathlib import Path
import random

import cocotb
from cocotb.triggers import Timer


def ins(op, rd=0, rs=0, imm=0):
    return (op << 12) | (rd << 10) | (rs << 8) | imm


async def tick(dut):
    dut.clk.value = 0
    await Timer(5, unit="ns")
    dut.clk.value = 1
    await Timer(5, unit="ns")


async def reset(dut):
    dut.clk.value = 0
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    await tick(dut)
    dut.rst_n.value = 1


async def write_byte(dut, address, high, value):
    dut.ui_in.value = 0x40 | (int(high) << 5) | address
    dut.uio_in.value = value
    await tick(dut)


async def load(dut, words):
    dut.ui_in.value = 0
    await tick(dut)
    for address, word in enumerate(words):
        await write_byte(dut, address, False, word & 255)
        await write_byte(dut, address, True, word >> 8)
    dut.ui_in.value = 0x80
    dut.uio_in.value = 0


async def read_state(dut):
    """Read the documented debug mux without generating clock edges."""
    dut.ui_in.value = 0xA0
    await Timer(1, unit="ns")
    status = int(dut.uo_out.value)
    regs = []
    for index in range(4):
        dut.ui_in.value = 0xC0 | index
        await Timer(1, unit="ns")
        regs.append(int(dut.uo_out.value))
    dut.ui_in.value = 0xE0
    await Timer(1, unit="ns")
    direction = int(dut.uo_out.value)
    dut.ui_in.value = 0x80
    await Timer(1, unit="ns")
    return status, regs, int(dut.uio_out.value), int(dut.uio_oe.value), direction


class Reference:
    """Executable ISA specification; no dependence on RTL internal signals."""
    def __init__(self, words):
        self.words = words
        self.pc = self.wait = self.out = self.direction = 0
        self.regs = [0] * 4
        self.halted = self.fault = False

    def step(self, pins=0, enabled=True):
        if not enabled or self.halted:
            return
        if self.wait:
            self.wait -= 1
            return
        if self.pc >= len(self.words):
            self.fault = self.halted = True
            return
        word = self.words[self.pc]
        op, rd, rs, value = word >> 12, (word >> 10) & 3, (word >> 8) & 3, word & 255
        next_pc = (self.pc + 1) % 32
        if op == 0: self.out = value
        elif op == 1: self.wait = value
        elif op == 2: next_pc = value % 32
        elif op == 3: self.regs[rd] = pins
        elif op == 4: self.regs[rd] = value
        elif op == 5: self.regs[rd] = self.regs[rs]
        elif op == 6:
            if self.regs[rd]: next_pc = value % 32
        elif op == 7: self.regs[rd] //= 2
        elif op == 8: self.regs[rd] = (self.regs[rd] * 2) % 256
        elif op == 9: self.out = self.regs[rd]
        elif op == 10: self.direction = value
        elif op == 11: self.regs[rd] &= value
        elif op == 12: self.regs[rd] = (self.regs[rd] + value) % 256
        elif op in (13, 14):
            self.halted = True
            self.fault = op == 14
            next_pc = self.pc
        self.pc = next_pc

    def state(self):
        status = (int(self.fault) << 7) | (int(self.halted) << 6) | (int(self.wait != 0) << 5) | self.pc
        oe = 0 if self.halted else self.direction
        return status, self.regs, self.out, oe, self.direction


async def run_reference(dut, words, cycles, seed=7):
    await reset(dut)
    await load(dut, words)
    model = Reference(words)
    rng = random.Random(seed)
    for cycle in range(cycles):
        pins = rng.randrange(256)
        enabled = cycle % 7 != 3
        dut.uio_in.value = pins
        dut.ena.value = int(enabled)
        model.step(pins, enabled)
        await tick(dut)
        observed = await read_state(dut)
        assert observed == model.state(), f"cycle={cycle}, expected={model.state()}, observed={observed}"
    return model


@cocotb.test()
async def test_all_instructions_and_branches(dut):
    words = [
        ins(10, imm=0xA5), ins(0, imm=0x5A), ins(4, rd=0, imm=255),
        ins(12, rd=0, imm=1),  # 255 + 1 wraps to zero.
        ins(6, rd=0, imm=31),  # Not taken.
        ins(4, rd=1, imm=0x81), ins(5, rd=2, rs=1),
        ins(8, rd=1), ins(7, rd=2), ins(11, rd=2, imm=0x0F),
        ins(3, rd=3), ins(9, rd=3), ins(1, imm=0), ins(1, imm=1),
        ins(4, rd=0, imm=3), ins(12, rd=0, imm=255),
        ins(6, rd=0, imm=15), ins(2, imm=19), ins(14), ins(15), ins(13)
    ]
    result = await run_reference(dut, words, 65)
    assert result.halted and not result.fault


@cocotb.test()
async def test_wait_boundaries_and_pause(dut):
    for delay in [0, 1, 15, 255]:
        result = await run_reference(dut, [ins(10, imm=1), ins(0, imm=1), ins(1, imm=delay), ins(0), ins(13)],
                                     (delay + 8) * 2)
        assert result.halted and result.out == 0


@cocotb.test()
async def test_faults_loading_and_reset(dut):
    await reset(dut)
    dut.ui_in.value = 0x80
    await tick(dut)
    assert (await read_state(dut))[0] == 0xC0, "Unloaded fetch must fault"
    await reset(dut)
    await write_byte(dut, 0, False, 0x55)
    dut.ui_in.value = 0x80
    await tick(dut)
    assert (await read_state(dut))[0] == 0xC0, "A partially loaded word must fault"
    await reset(dut)
    dut.ena.value = 0
    await write_byte(dut, 0, False, 0x55)
    await write_byte(dut, 0, True, 0x00)
    dut.ena.value = 1
    dut.ui_in.value = 0x80
    await tick(dut)
    assert (await read_state(dut))[0] == 0xC0, "Paused writes must not load memory"
    for words in [[ins(14)], [ins(2, imm=31)]]:
        result = await run_reference(dut, words, 5)
        assert result.fault
    # Reset during a running program invalidates program storage.
    await reset(dut)
    await load(dut, [ins(10, imm=255), ins(0, imm=255), ins(1, imm=255)])
    for _ in range(3): await tick(dut)
    dut.rst_n.value = 0
    await tick(dut)
    assert int(dut.uio_oe.value) == 0
    dut.rst_n.value = 1
    await tick(dut)
    assert (await read_state(dut))[0] == 0xC0


@cocotb.test()
async def test_firmware_reprogram_and_wrap(dut):
    root = Path(__file__).resolve().parents[1]
    blink = [int(w, 16) for w in (root / 'firmware/engine_blink.hex').read_text().split()]
    countdown = [int(w, 16) for w in (root / 'firmware/engine_countdown.hex').read_text().split()]
    await run_reference(dut, blink, 35)
    dut.ena.value = 1
    await load(dut, countdown)  # Reprogram without a reset.
    model = Reference(countdown)
    for cycle in range(20):
        model.step()
        await tick(dut)
        assert await read_state(dut) == model.state(), f"Reprogram mismatch at {cycle}"
    assert model.halted and model.regs[0] == 0
    result = await run_reference(dut, [ins(15)] * 32, 80)
    assert not result.halted


@cocotb.test()
async def test_randomized_instruction_streams(dut):
    rng = random.Random(20261001)
    for trial in range(6):
        words = [ins(10, imm=rng.randrange(256))]
        for _ in range(30):
            op = rng.choice([0, 1, 3, 4, 5, 7, 8, 9, 10, 11, 12, 15])
            words.append(ins(op, rd=rng.randrange(4), rs=rng.randrange(4),
                             imm=rng.randrange(4) if op == 1 else rng.randrange(256)))
        words.append(ins(2))
        result = await run_reference(dut, words, 100, seed=trial)
        assert not result.fault
