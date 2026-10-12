"""UART RX RTL verification with an asynchronous, independent wire source."""
import json
from pathlib import Path
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer
from cocotb.utils import get_sim_time
from uart_rx_reference import SerialSource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from uart_rx_firmware import generate_uart_rx, receive_data
from pio_host import program_writes, data_writes


async def cycles(dut, count=1):
    await ClockCycles(dut.clk, count)
    await Timer(1, unit='ns')


async def setup(dut, *, pin=1, words=None):
    dut.clk.value = 0
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 1 << pin
    cocotb.start_soon(Clock(dut.clk, 20, unit='ns').start())
    await cycles(dut, 3)
    dut.rst_n.value = 1
    await cycles(dut)
    await load(dut, generate_uart_rx(rx_pin=pin) if words is None else words)


async def load(dut, words):
    for command in program_writes(words, extended=True, transactional=True):
        dut.ui_in.value = command
        await cycles(dut)
        assert int(dut.uio_oe.value) == 0
    dut.ui_in.value = 0x1d
    await Timer(1, unit='ns')
    assert int(dut.uo_out.value) == 0xc0, 'Transactional image did not commit'


async def ram(dut, address):
    dut.ui_in.value = 0x80 | address
    await Timer(1, unit='ns')
    result = int(dut.uo_out.value)
    dut.ui_in.value = 0xa0
    await Timer(1, unit='ns')
    return result


async def configure(dut, count, *, pin=1, raw=False):
    data = receive_data(count) if not raw else [0] * 32
    data[:16] = [0xcc] * 16
    data[16] = count
    dut.uio_in.value = 1 << pin
    for command in data_writes(data):
        dut.ui_in.value = command
        await cycles(dut)
        assert int(dut.uio_oe.value) == 0
    dut.ui_in.value = 0xa0
    await cycles(dut, 40)


async def halt(dut, limit=1000):
    dut.ui_in.value = 0xa0
    for _ in range(limit):
        await cycles(dut)
        status = int(dut.uo_out.value)
        assert not status & 0x80, 'Hardware execution fault'
        assert int(dut.uio_oe.value) == 0, 'RX drove a GPIO'
        if status & 0x40:
            return
    raise AssertionError('RX did not halt')


async def capture(dut, payload, *, pin=1, configure_first=True, expected_status=2, **timing):
    if configure_first:
        await configure(dut, len(payload), pin=pin)
    assert await ram(dut, 17) == 1, 'RX not busy before the first byte'
    errors = []
    async def guard():
        while True:
            await FallingEdge(dut.clk)
            if int(dut.uio_oe.value):
                errors.append('RX drove a GPIO')
    task = cocotb.start_soon(guard())
    source = SerialSource(dut.uio_in, pin=pin)
    try:
        await source.send(payload, **timing)
        await halt(dut)
    finally:
        task.cancel()
    assert not errors, errors
    assert source.decoded() == [(b, int(i not in timing.get('bad_stops', ()))) for i, b in enumerate(payload)]
    status = await ram(dut, 17)
    assert status == expected_status, f'Status {status}, expected {expected_status}; committed {await ram(dut, 18)} bytes'
    count = len(payload) if expected_status == 2 else min(timing['bad_stops'])
    assert await ram(dut, 18) == count, 'Wrong committed byte count'
    assert await ram(dut, 20) == len(payload) - count
    actual = [await ram(dut, i) for i in range(count)]
    assert actual == list(payload[:count]), f'RX RAM {actual}, expected {list(payload[:count])}'
    if expected_status == 3:
        assert await ram(dut, 19) == payload[count], 'Framing-error byte not reported'
        assert await ram(dut, count) == 0xcc, 'Invalid frame was committed'
    return source


def report(name, data):
    path = ROOT / 'test/output'
    path.mkdir(exist_ok=True)
    (path / f'uart-rx-{name}.json').write_text(json.dumps(data, indent=2) + '\n')


@cocotb.test()
async def test_payloads_consecutive_and_ram_only(dut):
    await setup(dut)
    # One program load; every byte value, all batch lengths, no extra stop gaps.
    for first in range(0, 256, 16):
        await capture(dut, list(range(first, first + 16)))
    for count in range(1, 17):
        await capture(dut, [(i * 73 + 0x81) & 255 for i in range(count)],
                      phase_ns=13.7, gap_bits=0 if count % 2 else 2.5)


@cocotb.test()
async def test_count_only_ram_requests(dut):
    await setup(dut)
    # BEGIN invalidated RAM. Only the requested count is initialized by host;
    # firmware initializes its own status/counters and validates buffer stores.
    for count in (1, 16, 3):
        dut.uio_in.value = 2
        for command in data_writes([count], offset=16):
            dut.ui_in.value = command
            await cycles(dut)
        dut.ui_in.value = 0xa0
        await cycles(dut, 40)
        await capture(dut, [(i * 19 + 0x69) & 255 for i in range(count)], configure_first=False)


@cocotb.test()
async def test_baud_mismatch_and_start_phase(dut):
    await setup(dut)
    records = []
    for scale in (0.96, 0.98, 1.0, 1.02, 1.04):
        for phase in (0.1, 9.9, 20.1, 39.9, 59.9):
            bit_ns = 8680 / scale
            source = await capture(dut, [0, 255, 0x55, 0xaa, 0x81, 0x7e],
                                   bit_ns=bit_ns, phase_ns=phase)
            records.append(dict(baud_scale=scale, bit_ps=source.frames[0]['bit_ps'],
                                actual_baud=1e12/source.frames[0]['bit_ps'], phase_ns=phase))
    report('baud', records)


@cocotb.test()
async def test_framing_errors_and_rearm(dut):
    await setup(dut)
    for index in (0, 1, 7, 15):
        payload = [(i * 53 + 0x96) & 255 for i in range(16)]
        await capture(dut, payload, bad_stops=(index,), expected_status=3)
        await capture(dut, [0xaa, 0x55])


@cocotb.test()
async def test_false_start_idle_and_invalid_config(dut):
    await setup(dut)
    await configure(dut, 1)
    source = SerialSource(dut.uio_in)
    for width in (20, 1000, 4000):
        source.drive(0)
        await Timer(width, unit='ns')
        source.drive(1)
        await Timer(10000, unit='ns')
        assert await ram(dut, 18) == 0 and await ram(dut, 17) == 1
    await capture(dut, [0xa5], configure_first=False)
    for count in (0, 17, 31, 128, 255):
        await configure(dut, count, raw=True)
        await halt(dut)
        assert await ram(dut, 17) == 4 and await ram(dut, 18) == 0


@cocotb.test()
async def test_reset_stop_and_partial_capture(dut):
    await setup(dut)
    for action in ('stop', 'reset'):
        await configure(dut, 2)
        source = SerialSource(dut.uio_in)
        await source.send([0x96])
        assert await ram(dut, 18) == 1
        source.drive(0)
        await Timer(17000, unit='ns')
        if action == 'stop':
            dut.ui_in.value = 0
        else:
            dut.rst_n.value = 0
        await cycles(dut, 3)
        assert int(dut.uio_oe.value) == 0
        source.drive(1)
        if action == 'reset':
            dut.rst_n.value = 1
            dut.ui_in.value = 0xa0
            await cycles(dut)
            assert int(dut.uo_out.value) & 0xc0 == 0xc0, 'Reset must invalidate image'
            await load(dut, generate_uart_rx())
        else:
            assert await ram(dut, 0) == 0x96
            assert await ram(dut, 1) == 0xcc
        await capture(dut, [0x69, 0x81])


@cocotb.test()
async def test_checked_image_and_input_pin_selection(dut):
    words = [int(w, 16) for w in (ROOT / 'firmware/uart_rx.hex').read_text().split()]
    await setup(dut, words=words)
    await capture(dut, [0x12, 0x69, 0xa7])
    for pin in (0, 7):
        await load(dut, generate_uart_rx(rx_pin=pin))
        await capture(dut, [0xff, 0x00, 0xa5, 0x96], pin=pin)


@cocotb.test()
async def test_sampling_aperture_and_stop_boundaries(dut):
    await setup(dut)
    records = []
    for phase in (0.1, 10.1, 20.1, 30.1, 40.1, 50.1):
        hits = []
        # Twenty-nanosecond HIGH probes at each expected data center. A real
        # UART normally holds data for the entire bit; this deliberately noisy
        # waveform locates the receiver's actual aperture from RAM outcomes.
        for offset in range(-40, 101, 20):
            await configure(dut, 1)
            source = SerialSource(dut.uio_in)
            await Timer(round(phase * 1000), unit='ps')
            start = int(get_sim_time(unit='ps'))
            source.drive(0)
            for bit in range(8):
                probe = start + (13020 + 8680 * bit + offset) * 1000
                await Timer(probe - int(get_sim_time(unit='ps')), unit='ps')
                source.drive(1)
                await Timer(20, unit='ns')
                source.drive(0)
            await Timer(start + 78120000 - int(get_sim_time(unit='ps')), unit='ps')
            source.drive(1)
            await Timer(8680, unit='ns')
            await halt(dut)
            assert await ram(dut, 17) == 2
            byte = await ram(dut, 0)
            assert byte in (0, 255), 'Sample spacing depends on bit value or is not 8680 ns'
            if byte:
                hits.append(offset)
        assert len(hits) == 1 and 0 <= hits[0] <= 60, f'Unexpected sampling aperture: {hits}'
        records.append(dict(phase_ns=phase, first_sample_offset_ns=[13020+hits[0],13040+hits[0]],
                            sample_spacing_ns=8680))
        for shift, expected in ((-80, 2), (80, 3)):
            await configure(dut, 1)
            source = SerialSource(dut.uio_in)
            await Timer(round(phase * 1000), unit='ps')
            source.drive(0)
            await Timer(82460 + shift, unit='ns')
            source.drive(1)
            await Timer(1000, unit='ns')
            await halt(dut)
            assert await ram(dut, 17) == expected, 'STOP sample is not near the bit center'
            assert await ram(dut, 18) == int(expected == 2)
    report('aperture', records)


@cocotb.test()
async def test_baud_tolerance_boundaries(dut):
    await setup(dut)
    records = []
    for period in (8280, 9160):
        for phase in (0.1, 10.1, 20.1, 30.1, 40.1, 50.1):
            await capture(dut, [0, 255, 0x55, 0xaa, 0x81, 0x7e, 0, 0] * 2, bit_ns=period, phase_ns=phase)
        records.append(dict(bit_ns=period, baud=1e9/period, result='pass', phases=6))
    # At 8260 ns/bit each frame is shorter than the full detect-to-rearm path:
    # byte bookkeeping accumulates latency over a zero-gap burst. At 8240 ns
    # even the first STOP is too late; at 9180 ns it falls before STOP begins.
    for period, count in ((8260, 16), (8240, 2), (9180, 2)):
        for phase in (0.1, 20.1, 40.1):
            await configure(dut, count)
            source = SerialSource(dut.uio_in)
            await source.send([0] * count, bit_ns=period, phase_ns=phase)
            assert source.decoded() == [(0, 1)] * count
            await halt(dut)
            assert await ram(dut, 17) == 3 and await ram(dut, 18) < count
        records.append(dict(bit_ns=period, baud=1e9/period, result='framing_error', phases=3))
    report('boundaries', records)


@cocotb.test()
async def test_initial_low_break_and_idle_wait(dut):
    await setup(dut)
    await configure(dut, 1)
    # Restart while RX is already LOW: no edge has been observed yet.
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    await cycles(dut, 3)
    dut.ui_in.value = 0xa0
    await cycles(dut, 5000)
    assert await ram(dut, 17) == 1 and await ram(dut, 18) == 0
    dut.uio_in.value = 2
    await cycles(dut, 1000)
    await capture(dut, [0x96], configure_first=False)
    # A break after arming produces one framing error, not repeated zero bytes.
    await configure(dut, 2)
    dut.uio_in.value = 0
    await cycles(dut, 5000)
    await halt(dut)
    assert await ram(dut, 17) == 3 and await ram(dut, 18) == 0
    await cycles(dut, 5000)
    assert await ram(dut, 18) == 0
    await configure(dut, 1)
    await cycles(dut, 5000)
    assert await ram(dut, 17) == 1, 'Idle waiting unexpectedly timed out'
    await capture(dut, [0x69], configure_first=False)


@cocotb.test()
async def test_real_firmware_mutations_are_rejected(dut):
    await setup(dut)
    baseline = generate_uart_rx()
    cases = []
    words = baseline.copy()
    words[words.index(0x7000)] = 0xf000  # Remove LSB-first assembly shift.
    cases.append(('shift', words, [0x96], {}))
    words = baseline.copy()
    words[words.index(0x11aa)] -= 100     # Move every later sample far too early.
    cases.append(('sample timing', words, [0x55, 0xaa], {}))
    words = baseline.copy()
    words[words.index(0xe902)] = 0xf000  # Remove buffer store.
    cases.append(('RAM store', words, [0x96], {}))
    words = baseline.copy()
    stop_read = max(i for i, w in enumerate(words) if w == 0x3400)
    words[stop_read + 2] = 0xf000        # Ignore bad STOP.
    cases.append(('STOP validation', words, [0x96], dict(bad_stops=(0,), expected_status=3)))
    for name, words, payload, options in cases:
        await load(dut, words)
        try:
            await capture(dut, payload, **options)
        except AssertionError as error:
            dut._log.info('Independent RX scoreboard rejected %s: %s', name, error)
        else:
            raise AssertionError(f'Checker missed firmware {name} mutation')
