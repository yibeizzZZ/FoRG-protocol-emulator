"""I2C address-probe RTL verification on an independently resolved open-drain bus."""
import json
import os
from pathlib import Path
import sys

import cocotb
from cocotb.triggers import Timer
from i2c_reference import AddressProbeSlave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from i2c_firmware import generate_i2c_probe

DELAYS = tuple(int(os.environ.get(name, 0)) for name in
               ('I2C_RISE_NS', 'I2C_SCL_FALL_NS', 'I2C_SDA_FALL_NS'))


async def tick(dut):
    dut.clk.value = 0
    await Timer(10, unit='ns')
    dut.clk.value = 1
    await Timer(10, unit='ns')


async def reset(dut):
    dut.clk.value = 0
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.host_data.value = 0
    dut.noise.value = 0
    dut.slave_sda_low.value = 0
    dut.slave_scl_low.value = 0
    for _ in range(60):
        await tick(dut)
        assert int(dut.uio_oe.value) == 0
    dut.rst_n.value = 1


async def qualify_idle(dut, timeout=2000):
    # Mandatory host precondition, not a feature implemented by the firmware.
    # Include one extra sample to measure 235 full intervals continuously HIGH.
    high_samples = 0
    for _ in range(timeout):
        await tick(dut)
        assert int(dut.uio_oe.value) == 0
        high_samples = high_samples + 1 if int(dut.sda.value) and int(dut.scl.value) else 0
        if high_samples >= 236:
            return
    raise TimeoutError('Host must not start: bus was not continuously idle for 4.7 us')


async def load_program(dut, words):
    dut.ui_in.value = 0
    await tick(dut)
    for address, word in enumerate(words):
        for high in (0, 1):
            dut.ui_in.value = 0x40 | (high << 5) | address
            dut.host_data.value = (word >> (high * 8)) & 255
            await tick(dut)
            assert int(dut.uio_oe.value) == 0
    dut.ui_in.value = 0
    await qualify_idle(dut)
    dut.ui_in.value = 0xA0


async def sample(dut, slave, cycle):
    dut.noise.value = (cycle * 28) & 252  # READ masking must ignore unrelated inputs.
    await tick(dut)
    assert int(dut.uo_out.value) & 0x80 == 0, 'I2C engine faulted'
    sda_low, scl_low = slave.observe(cycle, int(dut.sda.value), int(dut.scl.value),
                                     int(dut.uio_out.value), int(dut.uio_oe.value))
    dut.slave_sda_low.value = sda_low
    dut.slave_scl_low.value = scl_low


async def result(dut):
    dut.ui_in.value = 0xC3
    await Timer(1, unit='ns')
    value = int(dut.uo_out.value)
    dut.ui_in.value = 0xA0
    await Timer(1, unit='ns')
    return value


async def probe(dut, address, *, acknowledge=True, target_address=None, period=250,
                stretches=None, words=None, pause=False):
    words = generate_i2c_probe(address, half_period_cycles=period) if words is None else words
    await load_program(dut, words)
    slave = AddressProbeSlave(address if target_address is None else target_address,
                              acknowledge=acknowledge, stretches=stretches)
    paused = False
    pause_cycles = 0
    for cycle in range(20000):
        await sample(dut, slave, cycle + pause_cycles)
        if pause and not paused and len(slave.rising_edges) == 4:
            before = (int(dut.uio_out.value), int(dut.uio_oe.value), int(dut.uo_out.value))
            dut.ena.value = 0
            for offset in range(1, 74):
                await sample(dut, slave, cycle + offset)
                assert (int(dut.uio_out.value), int(dut.uio_oe.value), int(dut.uo_out.value)) == before
            dut.ena.value = 1
            paused = True
            pause_cycles = 73
        if slave.complete:
            break
    else:
        raise AssertionError('I2C probe did not complete')
    assert slave.byte == address << 1, f'Address/RW mismatch: {slave.byte:#04x}'
    expected_ack = int(not (acknowledge and slave.address == address))
    assert slave.ack == expected_ack, 'Resolved ACK/NACK mismatch'
    assert await result(dut) == expected_ack, 'Firmware ACK/NACK readback mismatch'
    assert int(dut.uo_out.value) & 0xC0 == 0x40, 'Completion must HALT without fault'
    for _ in range(60):
        await tick(dut)
        assert int(dut.uio_oe.value) == 0, 'Completed probe must release the bus'
        assert int(dut.sda.value) == int(dut.scl.value) == 1
    assert await result(dut) == expected_ack, 'ACK result must persist after HALT'
    return slave


@cocotb.test()
async def test_addresses_ack_nack_and_reprogram(dut):
    await reset(dut)
    # Every seven-bit encoding, including boundary bit patterns. Reserved encodings
    # are wire-level tests only; the model does not implement their special meanings.
    for address in range(128):
        await probe(dut, address, acknowledge=address % 2 == 0)
    for address in (0x08, 0x25, 0x50, 0x77):
        await probe(dut, address, acknowledge=False)
        await probe(dut, address)
    await probe(dut, 0x50, target_address=0x51)  # Unaddressed slave leaves a real NACK.
    words = [int(word, 16) for word in (ROOT / 'firmware/i2c_probe.hex').read_text().split()]
    await probe(dut, 0x50, words=words)


@cocotb.test()
async def test_timing_and_measurements(dut):
    await reset(dut)
    measurements = []
    for period in (250, 251, 260, 267):
        for acknowledge in (True, False):
            slave = await probe(dut, 0x55, acknowledge=acknowledge, period=period)
            if DELAYS == (0, 0, 0):
                bits = slave.bits
                previous = [0] + bits
                assert slave.low_cycles[:9] == [period + 4 - bit for bit in previous]
                assert slave.high_cycles == [period + 1 - bit for bit in bits] + [period]
                assert slave.falling_edges[0] - slave.start == 217
                assert slave.low_cycles[9] == period + 1 - slave.ack
                assert slave.stop - slave.rising_edges[9] == period - 4
            intervals = [b - a for a, b in zip(slave.rising_edges[:9], slave.rising_edges[1:9])]
            measurements.append(dict(budget_cycles=period, ack=slave.ack, words=32,
                                     low_cycles=slave.low_cycles, high_cycles=slave.high_cycles,
                                     rising_period_cycles=intervals,
                                     max_scl_hz=50_000_000 / min(intervals)))
    path = ROOT / 'test/output' / ('i2c-firmware-' + '-'.join(map(str, DELAYS)) + '.json')
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(measurements, indent=2) + '\n')
    dut._log.info('I2C timing measurements: %s', measurements)


@cocotb.test()
async def test_stretch_every_phase_including_ack_and_stop(dut):
    await reset(dut)
    for acknowledge in (True, False):
        slave = await probe(dut, 0x69, acknowledge=acknowledge,
                            stretches={phase: 650 + phase * 7 for phase in range(1, 11)})
        assert all(low >= 650 for low in slave.low_cycles)
        assert len(slave.rising_edges) == 10
    await probe(dut, 0x36, stretches={3: 2000, 9: 1500, 10: 1700})


@cocotb.test()
async def test_pause_reset_stop_and_stuck_bus(dut):
    await reset(dut)
    await probe(dut, 0x4B, pause=True)
    # Stop/reset in the middle of a stretched low phase must release the pins;
    # neither is claimed to finish a legal transaction or recover a stuck target.
    for abort in ('reset', 'run'):
        await load_program(dut, generate_i2c_probe(0x50))
        slave = AddressProbeSlave(0x50, stretches={1: 100000})
        for cycle in range(1400):
            await sample(dut, slave, cycle)
        assert len(slave.falling_edges) == 1 and not slave.rising_edges
        assert int(dut.scl.value) == 0 and int(dut.uo_out.value) & 0xC0 == 0
        if abort == 'reset':
            dut.rst_n.value = 0
        else:
            dut.ui_in.value = 0
        await Timer(1, unit='ns')
        assert int(dut.uio_oe.value) == 0
        await tick(dut)
        dut.slave_scl_low.value = 0
        dut.slave_sda_low.value = 0
        if abort == 'reset':
            dut.rst_n.value = 1
            await tick(dut)
            assert int(dut.uo_out.value) & 0xC0 == 0xC0, 'Reset must invalidate the program'
        await probe(dut, 0x23)
    # An abnormal target that retains SDA after ACK prevents physical STOP.
    # HALT is not bus-idle evidence: host qualification must still reject it.
    await load_program(dut, generate_i2c_probe(0x50))
    slave = AddressProbeSlave(0x50)
    for cycle in range(6000):
        await sample(dut, slave, cycle)
        if len(slave.falling_edges) >= 9:
            dut.slave_sda_low.value = 1
    assert int(dut.uo_out.value) & 0xC0 == 0x40
    assert not slave.complete and int(dut.sda.value) == 0
    assert int(dut.uio_oe.value) == 0
    dut.ui_in.value = 0
    try:
        await qualify_idle(dut, timeout=300)
    except TimeoutError:
        pass
    else:
        raise AssertionError('Host accepted missing physical STOP')
    dut.slave_sda_low.value = 0
    await qualify_idle(dut)
    # Host qualification is tested explicitly, not attributed to the firmware.
    dut.ui_in.value = 0
    for line in ('slave_scl_low', 'slave_sda_low'):
        getattr(dut, line).value = 1
        for _ in range(60):
            await tick(dut)
        try:
            await qualify_idle(dut, timeout=300)
        except TimeoutError:
            pass
        else:
            raise AssertionError('Host accepted a busy bus')
        getattr(dut, line).value = 0
        await qualify_idle(dut)
    await probe(dut, 0x62)


@cocotb.test()
async def test_mutations_detect_data_timing_ack_and_electrical_errors(dut):
    cases = []
    words = generate_i2c_probe(0x50)
    words[0] ^= 0x20
    cases.append(('address data', 0x50, True, None, words))
    words = generate_i2c_probe(0x50)
    words[12] = words[19] = 0x1000
    cases.append(('low timing', 0x50, True, None, words))
    words = generate_i2c_probe(0x50)
    words[25] = 0x1000
    cases.append(('high timing', 0x50, True, None, words))
    words = generate_i2c_probe(0)
    words[30] = 0xF000  # No shifting: controller drives the ninth ACK slot LOW.
    cases.append(('ACK ownership', 0, False, None, words))
    words = generate_i2c_probe(0)
    words[29] &= 0xFF00  # Force sampled NACK to zero.
    cases.append(('ACK sampling', 0, False, None, words))
    words = generate_i2c_probe(0x50)
    words[0] = 0x0003  # Actively drive HIGH instead of using open drain.
    cases.append(('active HIGH', 0x50, True, None, words))
    words = generate_i2c_probe(0x50)
    words[24] = 0xF000  # Stop waiting for the observed SCL HIGH.
    cases.append(('stretch polling', 0x50, True, {1: 2000}, words))
    words = generate_i2c_probe(0x50)
    words[5] = 0xA002  # Release SDA on the same edge that lowers SCL.
    # A slow pull-up can legitimately filter this brief release pulse. Its
    # electrical failure is required in the ideal-bus configuration.
    if DELAYS == (0, 0, 0):
        cases.append(('SDA hold', 0x50, True, None, words))
    for name, address, ack, stretches, words in cases:
        await reset(dut)
        try:
            await probe(dut, address, acknowledge=ack, stretches=stretches, words=words)
        except AssertionError as error:
            dut._log.info('Detected %s mutation: %s', name, error)
        else:
            raise AssertionError(f'Undetected {name} mutation')
