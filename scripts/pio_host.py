"""Transport-independent UI-only PIO loading commands.

Apply each returned byte to ui_in while ena=1 for at least one rising clock.
Never drive uio to load these packets: uio remains connected to the real bus.
Strobes may be held for multiple clocks. Inputs must meet setup/hold timing.
Callers control reset and RUN; do not update RAM during an active transaction.
"""


def _packet(address, value):
    packed = (address << 16) | value
    commands = [0x1e]  # Abort any partial frame and establish strobe=0.
    strobe = 0
    for shift in range(20, -1, -4):
        strobe ^= 0x10
        commands.append(0x20 | strobe | ((packed >> shift) & 15))
    return commands


def program_writes(words, *, extended=False):
    """Atomic 16-bit program writes; mode switch precedes the program image."""
    words = list(words)
    capacity = 128 if extended else 32
    if not 1 <= len(words) <= capacity:
        raise ValueError(f'program must contain 1..{capacity} words')
    if any(type(word) is not int or not 0 <= word <= 65535 for word in words):
        raise ValueError('program words must be 16-bit integers')
    commands = [0] + _packet(255, int(bool(extended)))
    for address, word in enumerate(words):
        commands.extend(_packet(address, word))
    return commands + [0]


def data_writes(data, *, offset=0):
    """Write data RAM without changing the loaded program or GPIO inputs."""
    data = list(data)
    if type(offset) is not int or not 0 <= offset <= 31 or offset + len(data) > 32:
        raise ValueError('data writes must stay within RAM addresses 0..31')
    if any(type(byte) is not int or not 0 <= byte <= 255 for byte in data):
        raise ValueError('data must contain byte integers')
    commands = [0]
    for address, byte in enumerate(data, offset):
        commands.extend(_packet(0x80 | address, byte))
    return commands + [0]
