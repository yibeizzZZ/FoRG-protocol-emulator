"""Full RAM-configured I2C firmware tests against an independent memory target."""
import json
import os
from pathlib import Path
import sys

import cocotb
from cocotb.triggers import Timer
from i2c_master_reference import I2CMemorySlave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from i2c_master import generate_i2c_master, transaction_data
from pio_host import program_writes, data_writes

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
    dut.noise.value = 0
    dut.slave_sda_low.value = 0
    dut.slave_scl_low.value = 0
    for _ in range(60):
        await tick(dut)
        assert int(dut.uio_oe.value) == 0
    dut.rst_n.value = 1
    await tick(dut)


async def load_program(dut, words=None):
    words = generate_i2c_master() if words is None else words
    assert len(words) <= 128
    dut._log.debug('Loading %d firmware words through UI-only packets', len(words))
    # UI-only loading leaves the resolved SDA/SCL continuously connected.
    for command in program_writes(words, extended=True):
        dut.ui_in.value = command
        await tick(dut)
        assert int(dut.uio_oe.value) == 0, 'Program loading drove the bus'
        assert int(dut.sda.value) == int(dut.scl.value) == 1, 'Program loading disturbed idle pull-ups'


async def configure(dut, data):
    assert len(data) == 32
    dut.ui_in.value = 0
    dut.slave_sda_low.value = 0
    dut.slave_scl_low.value = 0
    for _ in range(60):
        await tick(dut)
    for command in data_writes(data):
        dut.ui_in.value = command
        await tick(dut)
        assert int(dut.uio_oe.value) == 0, 'RAM loading drove the bus'
        assert int(dut.sda.value) == int(dut.scl.value) == 1, 'RAM loading disturbed idle pull-ups'
    for _ in range(300):
        await tick(dut)
    assert int(dut.sda.value) == int(dut.scl.value) == 1


async def ram(dut, address):
    dut.ui_in.value = 0x80 | address
    await Timer(1, unit='ns')
    result = int(dut.uo_out.value)
    dut.ui_in.value = 0xa0
    await Timer(1, unit='ns')
    return result


async def sample(dut, slave, cycle, trace=None):
    dut.noise.value = (cycle * 28) & 252
    await tick(dut)
    values = (cycle, int(dut.sda.value), int(dut.scl.value),
              int(dut.uio_out.value), int(dut.uio_oe.value))
    if trace is not None:
        trace.append(values)
    sda_low, scl_low = slave.observe(*values)
    dut.slave_sda_low.value = sda_low
    dut.slave_scl_low.value = scl_low
    assert int(dut.uo_out.value) & 0x80 == 0, 'PIO execution fault'


async def run(dut, slave, *, limit=250000, pause=False, trace=None, busy_stop_phase=None):
    dut.ui_in.value = 0xa0
    paused = False
    resume = None
    checked_busy = False
    for cycle in range(limit):
        await sample(dut, slave, cycle, trace)
        if pause and not paused and len(slave.rising_edges) == 4:
            dut.ena.value = 0
            frozen = (int(dut.uio_out.value), int(dut.uio_oe.value))
            paused = True
            resume = cycle + 93
        if resume is not None:
            assert (int(dut.uio_out.value), int(dut.uio_oe.value)) == frozen, 'ENA pause changed bus drive'
            if cycle == resume:
                dut.ena.value = 1
                resume = None
        if (busy_stop_phase is not None and not checked_busy
                and len(slave.falling_edges) == busy_stop_phase
                and cycle - slave.falling_edges[-1] >= 100):
            assert not slave.complete and int(dut.scl.value) == 0
            assert await ram(dut, 19) == 1, 'Firmware published completion before physical STOP'
            checked_busy = True
        if int(dut.uo_out.value) & 0x40:
            assert busy_stop_phase is None or checked_busy, 'Stretched STOP check was not reached'
            for extra in range(1, 81):
                await sample(dut, slave, cycle + extra, trace)
                assert int(dut.uio_oe.value) == 0, 'HALT did not release pins'
            return await ram(dut, 19)
    raise AssertionError('Full I2C transaction did not halt')


async def transact(dut, address, write=b'', read_count=0, *, slave=None,
                   expected_status=2, pause=False, trace=None, busy_stop_phase=None):
    slave = I2CMemorySlave(address) if slave is None else slave
    memory = bytearray(slave.memory)
    pointer = slave.pointer
    if write:
        pointer = write[0]
        for value in write[1:]:
            memory[pointer] = value
            pointer = (pointer + 1) & 255
    expected_read = bytes(memory[(pointer + offset) & 255] for offset in range(read_count))
    await configure(dut, transaction_data(address, write=write, read_count=read_count))
    status = await run(dut, slave, pause=pause, trace=trace, busy_stop_phase=busy_stop_phase)
    assert status == expected_status, f'Firmware status {status}, expected {expected_status}'
    slave.check(address, write, expected_read, status=status)
    if status == 2:
        assert await ram(dut, 20) == await ram(dut, 21) == 0, 'Successful transfer left bytes pending'
        received = bytes([await ram(dut, read_count - i) for i in range(read_count)])
        assert received == expected_read, 'RAM read buffer differs from target bytes'
        assert slave.memory == memory, 'Register write did not update target memory'
    elif status == 3:
        assert await ram(dut, 20) == len(write), 'Address NACK consumed payload'
        assert await ram(dut, 21) == read_count
    elif status == 4:
        assert await ram(dut, 20) == len(write) - slave.nack_data, 'Data NACK remaining count lost unacknowledged byte'
        assert await ram(dut, 21) == read_count
    elif status == 5:
        assert await ram(dut, 20) == 0
        assert await ram(dut, 21) == read_count, 'Read address NACK consumed bytes'
    assert int(dut.sda.value) == int(dut.scl.value) == 1, 'STOP did not leave resolved bus idle'
    assert await ram(dut, 19) == status, 'Completion status did not persist'
    return slave


@cocotb.test()
async def test_register_write_readback_smoke(dut):
    """Short, independently checked workload also used on the routed netlist."""
    await reset(dut)
    await load_program(dut)
    written = await transact(dut, 0x50, b'\x20\x00\xff\x96\x69')
    target = I2CMemorySlave(0x50, memory=written.memory, pointer=0x20)
    await transact(dut, 0x50, read_count=4, slave=target)
    target = I2CMemorySlave(0x50, memory=written.memory, stretch_all=900)
    await transact(dut, 0x50, b'\x20', 4, slave=target)
    assert len(target.starts) == 2 and len(target.stops) == 1


@cocotb.test()
async def test_arbitrary_writes_reads_and_ram_only_reconfiguration(dut):
    await reset(dut)
    await load_program(dut)
    # Program is loaded exactly once; all changes below are RAM-only requests.
    writes = (b'\x00', b'\x80\x00\xff\x55\xaa', bytes(range(15)),
              b'\xf9\x81\x7e\xa5\x5a\x01\xfe')
    for address, payload in zip((0x08, 0x23, 0x50, 0x77), writes):
        await transact(dut, address, payload)
    for count in (1, 2, 7, 15):
        slave = I2CMemorySlave(0x39, pointer=0xfa,
                               memory=bytes((i * 73 + 19) & 255 for i in range(256)))
        await transact(dut, 0x39, read_count=count, slave=slave)
    for count in (1, 3, 15):
        slave = await transact(dut, 0x52, b'\xa3', count)
        assert len(slave.starts) == 2 and len(slave.stops) == 1
    # A real register update persists into a separate read transaction.
    slave = await transact(dut, 0x42, b'\x60\x00\xff\x96\x69')
    slave = I2CMemorySlave(0x42, memory=slave.memory)
    await transact(dut, 0x42, b'\x60', 4, slave=slave)
    await transact(dut, 0x33, bytes(range(15)), 15)


@cocotb.test()
async def test_address_data_and_read_address_nacks(dut):
    await reset(dut)
    await load_program(dut)
    await transact(dut, 0x50, b'\x10\xaa\x55', 3,
                   slave=I2CMemorySlave(0x51), expected_status=3)
    for index in (0, 1, 3):
        await transact(dut, 0x50, b'\x10\xaa\x55\xff', 2,
                       slave=I2CMemorySlave(0x50, nack_data=index), expected_status=4)
    for payload in (b'', b'\x20'):
        await transact(dut, 0x50, payload, 3,
                       slave=I2CMemorySlave(0x50, acknowledge_read=False), expected_status=5)
    await transact(dut, 0x50, b'\x20', 3)


@cocotb.test()
async def test_timing_stretch_all_phases_and_pause(dut):
    await reset(dut)
    measurements = []
    for period in (250, 267):
        await load_program(dut, generate_i2c_master(half_period_cycles=period))
        slave = await transact(dut, 0x55, b'\x20\x00\xff', 3)
        measurements.append(dict(period=period, low_cycles=slave.low_cycles,
                                 high_cycles=slave.high_cycles,
                                 rising_edges=slave.rising_edges,
                                 starts=slave.starts, stops=slave.stops))
    await load_program(dut)
    for payload, count in ((b'\x30\x96', 0), (b'', 2), (b'\x30', 2)):
        slave = await transact(dut, 0x6a, payload, count,
                               slave=I2CMemorySlave(0x6a, stretch_all=900))
        assert all(low >= 900 for low in slave.low_cycles), 'Some low phase ignored clock stretching'
    await transact(dut, 0x4b, b'\x30\x96', 2, pause=True)
    await transact(dut, 0x50, b'\x20', slave=I2CMemorySlave(0x50, stretches={19: 2000}),
                   busy_stop_phase=19)
    path = ROOT / 'test/output' / ('i2c-master-' + '-'.join(map(str, DELAYS)) + '.json')
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(measurements, indent=2) + '\n')


@cocotb.test()
async def test_timeout_busy_bus_invalid_configuration_and_missing_stop(dut):
    await reset(dut)
    await load_program(dut)
    await configure(dut, transaction_data(0x50, write=b'\x20'))
    slave = I2CMemorySlave(0x50, stretches={1: 200000})
    assert await run(dut, slave) == 6, 'Stuck clock did not report timeout'
    assert not slave.complete and not slave.rising_edges
    assert int(dut.uio_oe.value) == 0
    # Initial bus ownership is checked by firmware, even if host bypasses idle qualification.
    for line in ('slave_scl_low', 'slave_sda_low'):
        await configure(dut, transaction_data(0x50, write=b'\x20'))
        getattr(dut, line).value = 1
        for _ in range(60):
            await tick(dut)
        dut.ui_in.value = 0xa0
        for _ in range(3000):
            await tick(dut)
            if int(dut.uo_out.value) & 0x40:
                break
        assert await ram(dut, 19) == 7, 'Initially busy bus did not report bus-busy'
        assert int(dut.uio_oe.value) == 0
        getattr(dut, line).value = 0
    for address, value in ((17, 0), (17, 16), (18, 16)):
        data = bytearray(transaction_data(0x50, write=b'\x20'))
        data[address] = value
        await configure(dut, data)
        slave = I2CMemorySlave(0x50)
        assert await run(dut, slave) == 8
        assert not slave.starts, 'Invalid request touched the bus'
    await configure(dut, transaction_data(0x50, write=b'\x00'))
    # Hold SDA after the final data ACK: a released master is not proof of STOP.
    slave = I2CMemorySlave(0x50)
    dut.ui_in.value = 0xa0
    for cycle in range(30000):
        await sample(dut, slave, cycle)
        if len(slave.falling_edges) >= 19:
            dut.slave_sda_low.value = 1
        if int(dut.uo_out.value) & 0x40:
            break
    assert await ram(dut, 19) == 9, 'Missing physical STOP was reported as success'
    assert not slave.complete and int(dut.uio_oe.value) == 0
    dut.slave_sda_low.value = 0
    await transact(dut, 0x22, b'\x35', 2)


@cocotb.test()
async def test_reset_and_host_stop_during_stretch(dut):
    await reset(dut)
    for abort in ('reset', 'run'):
        await load_program(dut)
        await configure(dut, transaction_data(0x50, write=b'\x20\xa5', read_count=2))
        slave = I2CMemorySlave(0x50, stretches={4: 100000})
        dut.ui_in.value = 0xa0
        for cycle in range(10000):
            await sample(dut, slave, cycle)
            if len(slave.falling_edges) == 4 and int(dut.scl.value) == 0:
                break
        assert len(slave.falling_edges) == 4
        if abort == 'reset':
            dut.rst_n.value = 0
        else:
            dut.ui_in.value = 0
        await Timer(1, unit='ns')
        assert int(dut.uio_oe.value) == 0, 'Abort did not immediately release bus pins'
        await reset(dut)
    await load_program(dut)
    await transact(dut, 0x32, b'\x10\xa5', 2)


@cocotb.test()
async def test_reference_rejects_data_timing_ack_and_electrical_mutations(dut):
    await reset(dut)
    await load_program(dut)
    trace = []
    slave = await transact(dut, 0x50, b'\x00\x55', 2, trace=trace)
    # Replay a recorded external waveform through fresh independent models.
    # Mutations touch only wire observations; no firmware PCs/words enter the oracle.
    def replay(rows):
        model = I2CMemorySlave(0x50)
        for row in rows:
            model.observe(*row)
        model.check(0x50, b'\x00\x55', b'\x01\x02')

    replay(trace)
    mutations = [('timing', [(c // 2, sda, scl, out, oe) for c, sda, scl, out, oe in trace])]
    # First data byte MSB changes 0->1 with its complete setup/high/hold phase.
    lo, hi = slave.falling_edges[9] + 21, slave.falling_edges[10] + 20
    mutations.append(('write data', [(c, 1 if lo <= c <= hi else sda, scl, out, oe)
                                     for c, sda, scl, out, oe in trace]))
    # Final read NACK changes to ACK, including setup and hold around its ninth clock.
    # Last low phase is STOP preparation, immediately preceded by the final ACK slot.
    lo, hi = slave.falling_edges[-2] + 21, slave.falling_edges[-1] + 20
    mutations.append(('master ACK', [(c, 0 if lo <= c <= hi else sda, scl, out, oe)
                                     for c, sda, scl, out, oe in trace]))
    mutations.append(('active HIGH', [(c, sda, scl, out | 1, oe | 1) if c == 100 else row
                                      for row in trace for c, sda, scl, out, oe in [row]]))
    for name, rows in mutations:
        try:
            replay(rows)
        except AssertionError as error:
            dut._log.info('Independent reference rejected %s mutation: %s', name, error)
        else:
            raise AssertionError(f'Independent reference missed {name} mutation')


@cocotb.test()
async def test_real_firmware_mutations_are_rejected(dut):
    baseline = generate_i2c_master()
    cases = []
    words = baseline.copy()
    words[words.index(0x8000)] = 0xf000  # Suppress TX shift.
    cases.append(('transmitted data', words, b'\x10\x55', 0, {}))
    words = baseline.copy()
    words[words.index(0xfc00)] = 0xfc01  # Assemble received bits from SCL instead of SDA.
    cases.append(('received RAM data', words, b'', 2, {}))
    words = [0x1000 if word == 0x10f9 else word for word in baseline]
    cases.append(('required timing waits', words, b'\x10', 0, {}))
    words = baseline.copy()
    master_ack = max(index for index, word in enumerate(words) if word == 0xe401)
    words[master_ack] = 0xf000  # NACK every read byte instead of only the final byte.
    cases.append(('master read ACK', words, b'', 2, {}))
    words = baseline.copy()
    sample_ack = max(index for index, word in enumerate(words) if word == 0xb401)
    words[sample_ack] = 0xb400  # Hide a real target NACK.
    cases.append(('target ACK sampling', words, b'\x10', 0, dict(acknowledge=False)))
    words = baseline.copy()
    clock_poll = max(index for index, word in enumerate(words) if word == 0xb402)
    words[clock_poll] = 0x4402  # Claim SCL HIGH regardless of the resolved stretched bus.
    cases.append(('stretch observation', words, b'\x10', 0, dict(stretches={1: 2000})))
    words = baseline.copy()
    words[0] = 0x0003  # Drive HIGH when output enable is asserted later.
    cases.append(('open drain ownership', words, b'\x10', 0, {}))
    for name, words, payload, count, options in cases:
        await reset(dut)
        await load_program(dut, words)
        try:
            await transact(dut, 0x50, payload, count, slave=I2CMemorySlave(0x50, **options),
                           expected_status=3 if options.get('acknowledge') is False else 2)
        except AssertionError as error:
            dut._log.info('Independent target rejected firmware %s mutation: %s', name, error)
        else:
            raise AssertionError(f'Firmware {name} mutation escaped verification')
