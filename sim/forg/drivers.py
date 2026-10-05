"""Host-side drivers: build FIFO command streams for the bundled firmware."""

from . import isa


class HostTimeout(Exception):
    pass


def run_host(chip, sm, words_out, n_in, max_cycles=2_000_000, idle_until=None):
    """Feed `words_out` into SM `sm` while collecting `n_in` RX words."""
    words_out = list(words_out)
    got = []
    start = chip.cycle
    while len(got) < n_in or words_out or (idle_until and not idle_until()):
        if chip.cycle - start > max_cycles:
            raise HostTimeout(f"host timed out: sent {len(words_out)} left, got {len(got)}/{n_in}")
        if words_out and chip.put(sm, words_out[0]):
            words_out.pop(0)
        w = chip.get(sm)
        if w is not None:
            got.append(w)
        chip.step()
    return got


def spi_words(data: bytes):
    """Command stream for spi_cpha0/spi_cpha1: bit count then MSB-aligned words."""
    data = bytes(data)
    if not data:
        raise ValueError("empty SPI transfer")
    words = [len(data) * 8 - 1]
    for i in range(0, len(data), 4):
        chunk = data[i:i + 4].ljust(4, b"\0")
        words.append(int.from_bytes(chunk, "big"))
    return words


def spi_transfer(chip, sm, data, **kw):
    rx = run_host(chip, sm, spi_words(data), len(data), **kw)
    return bytes(w & 0xFF for w in rx)


# I2C: [31] START, [30:23] byte MSB first, [22] ack bit we send, [21] STOP


def i2c_word(byte, start=False, stop=False, ack=1):
    return (int(start) << 31) | ((byte & 0xFF) << 23) | ((ack & 1) << 22) | (int(stop) << 21)


def i2c_decode(word):
    return (word >> 1) & 0xFF, word & 1


def i2c_write(chip, sm, addr, data, stop=True, **kw):
    """Returns the list of ACK bits (0 = ACK) for address and each data byte."""
    data = bytes(data)
    words = [i2c_word(addr << 1, start=True, stop=stop and not data)]
    for i, b in enumerate(data):
        words.append(i2c_word(b, stop=stop and i == len(data) - 1))
    rx = run_host(chip, sm, words, len(words), **kw)
    return [i2c_decode(w)[1] for w in rx]


def i2c_read(chip, sm, addr, n, start=True, **kw):
    """Read n bytes (ACK all but the last, NAK the last, then STOP)."""
    words = [i2c_word((addr << 1) | 1, start=start)]
    for i in range(n):
        last = i == n - 1
        words.append(i2c_word(0xFF, ack=1 if last else 0, stop=last))
    rx = run_host(chip, sm, words, len(words), **kw)
    addr_ack = i2c_decode(rx[0])[1]
    return addr_ack, bytes(i2c_decode(w)[0] for w in rx[1:])


def i2c_write_read(chip, sm, addr, wdata, n, **kw):
    """Write then repeated-START read, the common register-read pattern."""
    words = [i2c_word(addr << 1, start=True)]
    words += [i2c_word(b) for b in bytes(wdata)]
    words.append(i2c_word((addr << 1) | 1, start=True))
    for i in range(n):
        last = i == n - 1
        words.append(i2c_word(0xFF, ack=1 if last else 0, stop=last))
    rx = run_host(chip, sm, words, len(words), **kw)
    acks = [i2c_decode(w)[1] for w in rx[: len(wdata) + 2]]
    return acks, bytes(i2c_decode(w)[0] for w in rx[len(wdata) + 2:])


def jtag_cycles(chip, sm, tms, tdi, **kw):
    """Clock len(tms) TCK cycles (any length). Returns the list of TDO bits."""
    tms, tdi = list(tms), list(tdi)
    if len(tms) != len(tdi):
        raise ValueError("tms and tdi length mismatch")
    out = []
    for i in range(0, len(tms), 32):
        t, d = tms[i:i + 32], tdi[i:i + 32]
        n = len(t)
        pairs = 0
        for k in range(n):
            pairs |= ((t[k] << 1) | d[k]) << (2 * k)
        data = [pairs & 0xFFFFFFFF] + ([pairs >> 32] if n > 16 else [])
        word = run_host(chip, sm, [n - 1] + data, 1, **kw)[0]
        word >>= 32 - n
        out += [(word >> k) & 1 for k in range(n)]
    return out


def jtag_reset(chip, sm):
    jtag_cycles(chip, sm, [1] * 5 + [0], [0] * 6)


def jtag_shift(chip, sm, ir, value, length):
    """From Run-Test/Idle, shift `length` bits through IR (ir=True) or DR, return to RTI."""
    head = [1, 1, 0, 0] if ir else [1, 0, 0]
    bits = [(value >> i) & 1 for i in range(length)]
    tms = head + [0] * (length - 1) + [1, 1, 0]
    tdi = [0] * len(head) + bits + [0, 0]
    tdo = jtag_cycles(chip, sm, tms, tdi)
    captured = tdo[len(head):len(head) + length]
    return sum(b << i for i, b in enumerate(captured))


def is_status(word):
    return bool(word & isa.ST_MARKER) and not word & 0xFFFF0000


def decode_capture(words, pins=8):
    """CAP run-length words -> list of (level, duration_ticks)."""
    mask = (1 << pins) - 1
    return [(w & mask, w >> 8) for w in words]
