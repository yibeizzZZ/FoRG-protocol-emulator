"""Reusable I2C write/read/combined transaction firmware for extended PIO.

All address, length and payload data live in the 32-byte data RAM. One image
executes any supported transaction without program reloading. SDA=0, SCL=1.
"""
import argparse
import json
from pathlib import Path

if __package__:
    from .pio_firmware import Program
else:
    from importlib.util import module_from_spec, spec_from_file_location
    _spec = spec_from_file_location('pio_firmware', Path(__file__).with_name('pio_firmware.py'))
    _builder = module_from_spec(_spec)
    _spec.loader.exec_module(_builder)
    Program = _builder.Program

ADDRESS, WRITE_LENGTH, READ_LENGTH = 16, 17, 18
STATUS, WRITE_REMAINING, READ_REMAINING, SAVED_BITS = 19, 20, 21, 22
STATUS_NAMES = {0:'ready', 1:'busy', 2:'success', 3:'write_address_nack',
                4:'write_data_nack', 5:'read_address_nack', 6:'clock_timeout',
                7:'bus_busy', 8:'invalid_config', 9:'missing_stop'}


def transaction_data(address, *, write=(), read_count=0):
    """RAM image for 1..15 TX and/or RX bytes; load with UI-only data_writes."""
    write = list(write)
    if type(address) is not int or not 0 <= address <= 127:
        raise ValueError('address must be a seven-bit integer')
    if type(read_count) is not int or not 0 <= read_count <= 15:
        raise ValueError('read_count must be an integer in 0..15')
    if len(write) > 15 or any(type(byte) is not int or not 0 <= byte <= 255 for byte in write):
        raise ValueError('write must contain at most 15 integer bytes')
    if not write and not read_count:
        raise ValueError('transaction needs write data or a read count')
    ram = [0] * 32
    for index, byte in enumerate(write):
        ram[len(write) - index] = byte
    ram[ADDRESS:READ_LENGTH + 1] = [address << 1, len(write), read_count]
    return ram


def generate_i2c_master(*, half_period_cycles=250, stretch_polls=255):
    """Protocol loops, independent of address/payload. 50 MHz Standard-mode.

    The configured half-period is a minimum software delay; instruction overhead
    and physical edges reduce actual frequency. Stretch timeout is bounded by
    stretch_polls unsuccessful samples, separated by a 256-cycle WAIT.
    """
    if type(half_period_cycles) is not int or not 250 <= half_period_cycles <= 4096:
        raise ValueError('half_period_cycles must be an integer in 250..4096')
    if type(stretch_polls) is not int or not 1 <= stretch_polls <= 255:
        raise ValueError('stretch_polls must be an integer in 1..255')
    p = Program(extended=True)
    e, label, branch, wait = p.emit, p.label, p.branch, p.wait
    def call(target):
        branch('CALL', target)
    # All directions are LOW or released because stopped mode zeros pins_out.
    e('MOVI', rd=0, imm=1)
    e('STA', rd=0, imm=STATUS)
    for source, remaining in ((WRITE_LENGTH, WRITE_REMAINING), (READ_LENGTH, READ_REMAINING)):
        e('LDA', rd=2, imm=source)
        e('STA', rd=2, imm=remaining)
        e('MOV', rd=1, rs=2)
        e('ANDI', rd=1, imm=0xF0)
        branch('JNZ', 'invalid', rd=1)
    e('LDA', rd=1, imm=ADDRESS)
    e('ANDI', rd=1, imm=1)
    branch('JNZ', 'invalid', rd=1)
    # Check idle, delay bus-free time, then check again. Single-controller bus.
    call('idle_check')
    wait(300)
    call('idle_check')
    e('LDA', rd=2, imm=WRITE_LENGTH)
    branch('JZ', 'read_only', rd=2)
    call('start')
    e('LDA', rd=0, imm=ADDRESS)
    call('tx_byte')
    branch('JNZ', 'write_address_nack', rd=1)
    label('write_loop')
    e('LDB', rd=0, rs=2)
    call('tx_byte')
    branch('JNZ', 'write_data_nack', rd=1)
    e('ADDI', rd=2, imm=255)
    e('STA', rd=2, imm=WRITE_REMAINING)
    branch('JNZ', 'write_loop', rd=2)
    e('LDA', rd=2, imm=READ_LENGTH)
    branch('JZ', 'success', rd=2)
    e('OECLR', imm=1)                    # Repeated START: SDA release while SCL LOW
    call('clock_high')
    call('start')
    branch('JMP', 'read_address')
    label('read_only')
    e('LDA', rd=2, imm=READ_LENGTH)
    branch('JZ', 'invalid', rd=2)
    call('start')
    label('read_address')
    e('LDA', rd=0, imm=ADDRESS)
    e('ADDI', rd=0, imm=1)
    call('tx_byte')
    branch('JNZ', 'read_address_nack', rd=1)
    label('read_loop')
    call('rx_byte')
    e('STB', rd=0, rs=2)
    e('ADDI', rd=2, imm=255)
    e('STA', rd=2, imm=READ_REMAINING)
    branch('JNZ', 'read_loop', rd=2)
    label('success')
    e('MOVI', rd=0, imm=2)
    label('stop')
    e('OESET', imm=1)
    call('clock_high')
    e('OECLR', imm=1)
    wait(256)                           # Allow maximum tested passive SDA rise
    e('READ', rd=1)
    e('ANDI', rd=1, imm=1)
    branch('JNZ', 'abort', rd=1)
    e('MOVI', rd=0, imm=9)
    label('abort')
    e('STA', rd=0, imm=STATUS)
    label('complete')
    e('HALT')
    for name, code in (('write_address_nack',3), ('write_data_nack',4), ('read_address_nack',5)):
        label(name)
        e('MOVI', rd=0, imm=code)
        branch('JMP', 'stop')
    label('invalid')
    e('MOVI', rd=0, imm=8)
    branch('JMP', 'abort')
    label('idle_check')
    e('READ', rd=1)
    e('ANDI', rd=1, imm=3)
    e('ADDI', rd=1, imm=253)
    branch('JZ', 'idle_ok', rd=1)
    e('MOVI', rd=0, imm=7)
    branch('JMP', 'abort')
    label('idle_ok')
    e('RET')
    label('start')
    e('OESET', imm=1)
    wait(250)
    e('RET')
    label('fall')
    e('OESET', imm=2)
    wait(16)
    e('RET')
    label('tx_byte')
    e('MOVI', rd=3, imm=8)
    label('tx_bit')
    call('fall')
    e('MOV', rd=1, rs=0)
    e('ANDI', rd=1, imm=128)
    branch('JNZ', 'tx_one', rd=1)
    e('OESET', imm=1)
    branch('JMP', 'tx_clock')
    label('tx_one')
    e('OECLR', imm=1)
    label('tx_clock')
    call('clock_high')
    e('SHL', rd=0)
    branch('DJNZ', 'tx_bit', rd=3)
    call('fall')
    e('OECLR', imm=1)                   # Target owns the ninth bit
    call('clock_high')
    e('READ', rd=1)
    e('ANDI', rd=1, imm=1)
    call('fall')
    e('RET')
    label('rx_byte')
    e('MOVI', rd=0, imm=0)
    e('MOVI', rd=3, imm=8)
    e('OECLR', imm=1)
    label('rx_bit')
    call('clock_high')
    e('INBIT', rd=0, imm=0)
    call('fall')
    branch('DJNZ', 'rx_bit', rd=3)
    e('MOV', rd=1, rs=2)
    e('ADDI', rd=1, imm=255)
    branch('JZ', 'master_nack', rd=1)
    e('OESET', imm=1)
    label('master_nack')
    call('clock_high')
    call('fall')
    e('OECLR', imm=1)
    e('RET')
    label('clock_high')
    wait(half_period_cycles)
    e('OECLR', imm=2)
    e('STA', rd=3, imm=SAVED_BITS)
    e('MOVI', rd=3, imm=stretch_polls)
    label('stretch')
    e('READ', rd=1)
    e('ANDI', rd=1, imm=2)
    branch('JNZ', 'clock_ready', rd=1)
    wait(256)
    branch('DJNZ', 'stretch', rd=3)
    e('MOVI', rd=0, imm=6)
    branch('JMP', 'abort')
    label('clock_ready')
    e('LDA', rd=3, imm=SAVED_BITS)
    wait(half_period_cycles)
    e('RET')
    return p.assemble()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--half-period', type=int, default=250)
    parser.add_argument('--stretch-polls', type=int, default=255)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--address', type=lambda v:int(v,0))
    parser.add_argument('--write', nargs='*', type=lambda v:int(v,0), default=[])
    parser.add_argument('--read-count', type=int, default=0)
    parser.add_argument('--data-output', type=Path)
    args = parser.parse_args()
    try:
        words = generate_i2c_master(half_period_cycles=args.half_period, stretch_polls=args.stretch_polls)
        data = transaction_data(args.address, write=args.write, read_count=args.read_count) if args.address is not None else None
        if args.data_output and data is None:
            raise ValueError('--data-output requires --address')
    except ValueError as error:
        parser.error(str(error))
    content = ''.join(f'{word:04x}\n' for word in words)
    if args.output:
        args.output.write_text(content)
    else:
        print(content, end='')
    if args.data_output:
        args.data_output.write_text(json.dumps(data) + '\n')


if __name__ == '__main__':
    main()
