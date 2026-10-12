"""RAM-configured UART 8N1 RX on extended PIO, 50 MHz / 434 cycles per bit.

RX defaults to uio[1]. All GPIO remain inputs. The existing UART TX image is
independent and unchanged; one PIO engine runs one image at a time.
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

LENGTH, STATUS, RECEIVED, BAD_BYTE, REMAINING = 16, 17, 18, 19, 20
CYCLES_PER_BIT = 434
STATUS_NAMES = {0: 'ready', 1: 'busy', 2: 'success', 3: 'framing_error', 4: 'invalid_config'}


def receive_data(count):
    """Prepare a finite capture of 1..16 bytes in ascending RAM[0..15]."""
    if type(count) is not int or not 1 <= count <= 16:
        raise ValueError('count must be an integer in 1..16')
    data = [0] * 32
    data[LENGTH] = count
    return data


def generate_uart_rx(*, rx_pin=1):
    """Return fixed-rate RX firmware; byte count is runtime RAM configuration.

    Poll every three cycles, confirm START after 217 cycles, then sample data
    at 651 + 434*i cycles and STOP at 4123 cycles from the detecting READ.
    Both bit values take equal time. A bad STOP halts without committing that
    byte. No timeout, oversampling, input synchronizer or autonomous ring buffer.
    """
    if type(rx_pin) is not int or not 0 <= rx_pin <= 7:
        raise ValueError('rx_pin must be an integer in 0..7')
    mask = 1 << rx_pin
    p = Program(extended=True)
    e, label, branch, wait = p.emit, p.label, p.branch, p.wait
    e('DIR', imm=0)
    e('MOVI', rd=0, imm=1)
    e('STA', rd=0, imm=STATUS)
    e('MOVI', rd=2, imm=0)
    e('STA', rd=2, imm=RECEIVED)
    e('STA', rd=2, imm=BAD_BYTE)
    e('LDA', rd=3, imm=LENGTH)
    branch('JZ', 'invalid', rd=3)
    e('MOV', rd=1, rs=3)
    e('ANDI', rd=1, imm=0xf0)
    branch('JZ', 'valid', rd=1)
    e('MOV', rd=1, rs=3)
    e('ADDI', rd=1, imm=240)
    branch('JNZ', 'invalid', rd=1)
    label('valid')
    e('STA', rd=3, imm=REMAINING)
    # Require an observed idle HIGH before the first falling transition.
    label('idle')
    e('READ', rd=1)
    e('ANDI', rd=1, imm=mask)
    branch('JZ', 'idle', rd=1)
    label('start')
    e('READ', rd=1)
    e('ANDI', rd=1, imm=mask)
    branch('JNZ', 'start', rd=1)
    wait(214)                           # Detecting READ to confirmation = 217
    e('READ', rd=1)
    e('ANDI', rd=1, imm=mask)
    branch('JNZ', 'start', rd=1)         # Short false START: return to polling
    e('MOVI', rd=0, imm=0)
    e('MOVI', rd=3, imm=8)
    wait(428)                           # Confirmation to first sample = 434
    label('bit')
    e('SHR', rd=0)
    e('READ', rd=1)
    e('ANDI', rd=1, imm=mask)
    branch('JZ', 'zero', rd=1)
    e('ADDI', rd=0, imm=128)             # Insert newest LSB-first bit at bit7
    branch('JMP', 'joined')
    label('zero')
    wait(2)                             # Balance ADDI + JMP for a one bit
    label('joined')
    wait(427)
    branch('DJNZ', 'bit', rd=3)         # READ-to-READ = 434, including SHR
    wait(1)                             # Replace the next SHR before STOP READ
    e('READ', rd=1)
    e('ANDI', rd=1, imm=mask)
    branch('JZ', 'framing', rd=1)
    e('STB', rd=0, rs=2)
    e('ADDI', rd=2, imm=1)
    e('STA', rd=2, imm=RECEIVED)
    e('LDA', rd=1, imm=REMAINING)
    e('ADDI', rd=1, imm=255)
    e('STA', rd=1, imm=REMAINING)
    branch('JNZ', 'start', rd=1)
    e('MOVI', rd=0, imm=2)
    branch('JMP', 'finish')
    label('invalid')
    e('MOVI', rd=0, imm=4)
    branch('JMP', 'finish')
    label('framing')
    e('STA', rd=0, imm=BAD_BYTE)
    e('MOVI', rd=0, imm=3)
    label('finish')
    e('STA', rd=0, imm=STATUS)
    e('HALT')
    return p.assemble()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rx-pin', type=int, default=1)
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--data-output', type=Path)
    args = parser.parse_args()
    try:
        words = generate_uart_rx(rx_pin=args.rx_pin)
        data = receive_data(args.count)
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
