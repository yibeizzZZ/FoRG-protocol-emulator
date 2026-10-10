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


async def select_bank(dut, bank, extended=True):
    dut.ui_in.value = 0x1F
    dut.uio_in.value = (0x80 if extended else 0) | bank
    await tick(dut)


async def load_extended(dut, words, data=()):
    for bank in range((len(words) + 31) // 32):
        await select_bank(dut, bank)
        for address, word in enumerate(words[bank * 32:(bank + 1) * 32]):
            await write_byte(dut, address, False, word & 255)
            await write_byte(dut, address, True, word >> 8)
    if data:
        await select_bank(dut, 4)
        for address, value in enumerate(data):
            await write_byte(dut, address, False, value)
    dut.ui_in.value = 0x80
    dut.uio_in.value = 0


async def read_mux(dut, control):
    dut.ui_in.value = control
    await Timer(1, unit="ns")
    value = int(dut.uo_out.value)
    dut.ui_in.value = 0x80
    return value


async def execute(dut, count):
    for _ in range(count):
        await tick(dut)


@cocotb.test()
async def test_extended_banks_wrap_validity_and_mode(dut):
    # A branch beyond 31 must fetch its own bank, and 127 wraps to zero.
    await reset(dut)
    words = [0x207E] + [0xEFFF] * 125 + [0x005A, 0xF000]
    await load_extended(dut, words)
    await tick(dut)
    assert await read_mux(dut, 0xE1) == 126
    await tick(dut)
    assert int(dut.uio_out.value) == 0x5A
    await tick(dut)
    assert await read_mux(dut, 0xE1) == 0
    # Missing one half in the fourth bank cannot borrow bank-zero validity.
    await reset(dut)
    await load_extended(dut, [0x207F])
    await select_bank(dut, 3)
    await write_byte(dut, 31, False, 0x11)
    dut.ui_in.value = 0x80
    await execute(dut, 2)
    assert await read_mux(dut, 0xA0) == 0xDF
    assert await read_mux(dut, 0xE1) == 127
    # Mode command accepts disable; old branch width and every F field remain legacy.
    await select_bank(dut, 0, extended=False)
    await load(dut, [0x203F] + [0xF000] * 30 + [0xFFAB])
    await execute(dut, 2)
    assert (await read_state(dut))[0] == 0


@cocotb.test()
async def test_extended_memory_lifetime_and_host_banks(dut):
    await reset(dut)
    # LDA R1,[0]; STA R1,[31]; LDI R2,31; LDB R3,[R2]; STB R3,[R0].
    await load_extended(dut, [0xE640, 0xE75F, 0x481F, 0xE80E, 0xE90C, 0xD000], [0xA5])
    await execute(dut, 6)
    state = await read_state(dut)
    assert state[0] == 0x45 and state[1] == [0, 0xA5, 31, 0xA5]
    assert await read_mux(dut, 0x9F) == 0xA5
    # Stopping clears execution state but leaves code, bank selection, and data.
    dut.ui_in.value = 0
    await tick(dut)
    assert await read_mux(dut, 0xC1) == 0
    assert await read_mux(dut, 0x9F) == 0xA5
    await write_byte(dut, 0, True, 0xFF)  # Bank 4 high bytes ignored.
    await write_byte(dut, 0, False, 0x3C)
    dut.ui_in.value = 0x80
    await execute(dut, 6)
    assert await read_mux(dut, 0x9F) == 0x3C
    for bank in (5, 6, 7):
        await select_bank(dut, bank)
        await write_byte(dut, 0, False, 0xFF)
        await write_byte(dut, 0, True, 0xFF)
    dut.ui_in.value = 0x80
    await execute(dut, 6)
    assert await read_mux(dut, 0x9F) == 0x3C
    await reset(dut)
    await select_bank(dut, 4)
    assert await read_mux(dut, 0x9F) == 0


@cocotb.test()
async def test_extended_memory_faults_release_gpio(dut):
    # Reads of invalid bytes and indirect reads/writes outside RAM fault.
    for bad in ([0xE600], [0xE801], [0x4420, 0xE801], [0x4420, 0xE901],
                [0x44FF, 0xE801], [0x44FF, 0xE901]):
        await reset(dut)
        await load_extended(dut, [0xA0FF] + bad + [0xD000])
        await execute(dut, len(bad) + 1)
        assert (await read_mux(dut, 0xA0)) & 0xC0 == 0xC0
        assert int(dut.uio_oe.value) == 0


@cocotb.test()
async def test_extended_calls_and_stack_faults(dut):
    await reset(dut)
    # Four nested calls are legal; returns restore the complete seven-bit PC.
    words = [0xEA40, 0xD000] + [0xEFFF] * 62
    words += [0xEA44, 0xC001, 0xEB00, 0xEFFF,
              0xEA48, 0xC002, 0xEB00, 0xEFFF,
              0xEA4C, 0xC004, 0xEB00, 0xEFFF, 0xC008, 0xEB00]
    await load_extended(dut, words)
    await execute(dut, 13)
    assert await read_mux(dut, 0xC0) == 15
    assert await read_mux(dut, 0xA0) == 0x41
    for words, cycles, pc in [([0xA0FF, 0xEB00], 2, 1), ([0xA0FF, 0xEA01], 6, 1)]:
        await reset(dut)
        await load_extended(dut, words)
        await execute(dut, cycles)
        assert await read_mux(dut, 0xA0) == 0xC0 | pc
        assert int(dut.uio_oe.value) == 0


@cocotb.test()
async def test_extended_gpio_bit_loop_and_conditions(dut):
    await reset(dut)
    # JZ skips a fault; OR and DIRR build a mask; OESET/OECLR preserve other bits.
    words = [0xE002, 0xEFFF, 0x4001, 0x4480, 0xEC01, 0xED00,
             0xE402, 0xE580, 0x4803, 0xFC1A, 0xFA09, 0x9C00,
             0xE30F, 0xD000, 0xEFFF, 0xEFFF]
    await load_extended(dut, words)
    dut.uio_in.value = 4
    await execute(dut, 8)
    assert int(dut.uio_oe.value) == 3
    assert await read_mux(dut, 0xC0) == 0x81
    await execute(dut, 9)
    assert await read_mux(dut, 0xC3) == 7
    assert await read_mux(dut, 0xE2) == 7
    assert await read_mux(dut, 0xA0) == 0x4D
    # DJNZ zero wraps to 255; the branch uses all seven target bits.
    await reset(dut)
    await load_extended(dut, [0xF840] + [0xEFFF] * 63 + [0xD000])
    await execute(dut, 2)
    assert await read_mux(dut, 0xC0) == 255
    assert await read_mux(dut, 0xE1) == 64
    for word in (0xEE00, 0xEF00, 0xFD00, 0xFE00, 0xFF00):
        await reset(dut)
        await load_extended(dut, [word])
        await tick(dut)
        assert await read_mux(dut, 0xA0) == 0xC0


@cocotb.test()
async def test_extended_wait_full_width_and_pause(dut):
    for delay in (0, 1, 256, 4095):
        await reset(dut)
        await load_extended(dut, [0x1000 | delay, 0x005A, 0xD000])
        await tick(dut)
        dut.ena.value = 0
        await execute(dut, 3)
        assert await read_mux(dut, 0xA0) == (0x21 if delay else 1)
        dut.ena.value = 1
        await execute(dut, delay)
        assert int(dut.uio_out.value) == 0
        assert await read_mux(dut, 0xA0) == 1
        await tick(dut)
        assert int(dut.uio_out.value) == 0x5A


@cocotb.test()
async def test_extended_host_pause_and_readback(dut):
    await reset(dut)
    await load_extended(dut, [0xE600, 0xD000], [0x5A])
    dut.ena.value = 0
    await select_bank(dut, 0, extended=False)  # Paused command must not change mode/bank.
    await write_byte(dut, 0, False, 0xFF)
    assert await read_mux(dut, 0x80) == 0x5A
    dut.ena.value = 1
    await write_byte(dut, 0, False, 0x33)  # Still bank 4, not code bank 0.
    dut.ui_in.value = 0x80
    await execute(dut, 2)
    assert await read_mux(dut, 0xC0) == 0x33
    assert await read_mux(dut, 0xA0) == 0x41
    dut.ena.value = 0
    await execute(dut, 2)
    assert await read_mux(dut, 0x80) == 0x33
    # Reset invalidates data and restores code bank zero and legacy mode.
    await reset(dut)
    await load(dut, [0x005A, 0xD000])
    await tick(dut)
    assert await read_mux(dut, 0x80) == 0x5A
    await select_bank(dut, 4)
    assert await read_mux(dut, 0x80) == 0


@cocotb.test()
async def test_extended_conditional_targets_and_bit_values(dut):
    # Every JZ/DJNZ register selector and INBIT zero/one use pin-visible results.
    for reg in range(4):
        await reset(dut)
        words = [0xE040 | (reg << 8)] + [0xEFFF] * 63
        words += [ins(4, rd=reg, imm=0x80), 0xFC00 | (reg << 3) | 7,
                  0xFC00 | (reg << 3), 0xF843 | (reg << 8), 0xD000]
        await load_extended(dut, words)
        dut.uio_in.value = 0x80
        await execute(dut, 3)
        assert await read_mux(dut, 0xC0 | reg) == 1
        await tick(dut)
        assert await read_mux(dut, 0xC0 | reg) == 2
        await execute(dut, 3)
        assert await read_mux(dut, 0xC0 | reg) == 0
        assert await read_mux(dut, 0xE1) == 68
        assert await read_mux(dut, 0xA0) == 0x44


def packet_controls(address, value):
    """Six UI samples: address byte and word, most significant nibble first."""
    frame = (address << 16) | value
    return [0x20 | (0x10 if index % 2 == 0 else 0) | ((frame >> shift) & 15)
            for index, shift in enumerate((20, 16, 12, 8, 4, 0))]


async def send_packet(dut, address, value, hold=1):
    for control in packet_controls(address, value):
        dut.ui_in.value = control
        await execute(dut, hold)


@cocotb.test()
async def test_ui_packet_bootstrap_and_atomic_writes(dut):
    await reset(dut)
    dut.uio_in.value = 0xC3  # External bus inputs never carry host data.
    await send_packet(dut, 255, 1, hold=3)
    await send_packet(dut, 0, 0x207F, hold=3)
    await send_packet(dut, 127, 0xE61F, hold=3)
    await send_packet(dut, 0x9F, 0x005A, hold=3)
    dut.ui_in.value = 0x80
    await execute(dut, 2)
    assert await read_mux(dut, 0xC0) == 0x5A
    assert await read_mux(dut, 0xE1) == 0
    # A partial replacement is invisible. RUN discards it, and the old word runs.
    for control in packet_controls(0, 0xEFFF)[:5]:
        dut.ui_in.value = control
        await tick(dut)
    dut.ui_in.value = 0x80
    await tick(dut)
    assert await read_mux(dut, 0xE1) == 127
    # A complete replacement becomes executable on its sixth accepted nibble.
    await send_packet(dut, 0, 0xD000)
    dut.ui_in.value = 0x80
    await tick(dut)
    assert await read_mux(dut, 0xA0) == 0x40


@cocotb.test()
async def test_ui_packet_abort_pause_and_toggle(dut):
    await reset(dut)
    dut.uio_in.value = 0
    await send_packet(dut, 255, 1)
    await send_packet(dut, 0x80, 0x0012)
    controls = packet_controls(0x80, 0x0034)
    for control in controls[:5]:
        dut.ui_in.value = control
        await tick(dut)
    assert await read_mux(dut, 0x80) == 0x12
    dut.ui_in.value = controls[5]
    dut.ena.value = 0
    await execute(dut, 3)
    assert await read_mux(dut, 0x80) == 0x12
    dut.ui_in.value = controls[5]
    dut.ena.value = 1
    await tick(dut)
    assert await read_mux(dut, 0x80) == 0x34
    # Holding the last strobe while changing its nibble cannot start another frame.
    dut.ui_in.value = 0x2F
    await execute(dut, 3)
    await send_packet(dut, 0x80, 0x0056)
    assert await read_mux(dut, 0x80) == 0x56
    for control in controls[:3]:
        dut.ui_in.value = control
        await tick(dut)
    dut.ui_in.value = 0x1E
    await execute(dut, 3)
    assert await read_mux(dut, 0x80) == 0x56
    await send_packet(dut, 0x80, 0x0078)
    assert await read_mux(dut, 0x80) == 0x78
    # A paused first edge is accepted exactly once when enable resumes.
    dut.ena.value = 0
    dut.ui_in.value = 0x30
    await execute(dut, 3)
    dut.ena.value = 1
    await send_packet(dut, 0, 0xD000)
    dut.ui_in.value = 0x80
    await tick(dut)
    assert await read_mux(dut, 0xA0) == 0x40


@cocotb.test()
async def test_ui_packet_invalid_frames_mode_and_reset(dut):
    await reset(dut)
    dut.uio_in.value = 0xFF
    await send_packet(dut, 255, 1)
    await send_packet(dut, 0x80, 0x005A)
    for address, value in ((0xA0, 0xD000), (0xFE, 0xFFFF), (0x80, 0xFF33), (255, 2)):
        await send_packet(dut, address, value)
    assert await read_mux(dut, 0x80) == 0x5A
    # Packet mode writes reset the parallel loader bank to zero.
    await select_bank(dut, 4)
    dut.uio_in.value = 0xFF
    await send_packet(dut, 255, 0)
    await write_byte(dut, 0, False, 0xA5)
    await write_byte(dut, 0, True, 0)
    dut.ui_in.value = 0x80
    await tick(dut)
    assert await read_mux(dut, 0x80) == 0xA5
    # Reset clears a partial packet and invalidates code; a partial packet alone
    # cannot mark a reset word valid.
    for control in packet_controls(0, 0x0055)[:3]:
        dut.ui_in.value = control
        await tick(dut)
    await reset(dut)
    for control in packet_controls(0, 0xD000)[:5]:
        dut.ui_in.value = control
        await tick(dut)
    dut.ui_in.value = 0x80
    await tick(dut)
    assert await read_mux(dut, 0xA0) == 0xC0
    await send_packet(dut, 0, 0xD000)
    dut.ui_in.value = 0x80
    await tick(dut)
    assert await read_mux(dut, 0xA0) == 0x40
