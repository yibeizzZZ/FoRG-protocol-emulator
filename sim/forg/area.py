"""Pre-synthesis area budget for the FoRG architecture.

These are planning numbers, not synthesis results: storage bits are counted
exactly from the architecture parameters, combinational logic uses per-block
estimates. Replace them with Yosys `stat` numbers as soon as RTL exists.
"""

from dataclasses import dataclass, field


@dataclass
class AreaParams:
    num_sm: int = 4
    imem_words: int = 64
    imem_as_sram: bool = False
    fifo_depth: int = 4
    data_width: int = 32
    num_pins: int = 24
    line_unit: bool = True
    crc_width: int = 32
    tiles: int = 24
    cells_per_tile: int = 1000
    routing_margin: float = 0.30


@dataclass
class AreaReport:
    rows: list = field(default_factory=list)
    total: int = 0
    budget: int = 0

    def text(self):
        out = [f"{'block':<34}{'flops':>8}{'logic':>8}{'cells':>8}"]
        for name, ff, logic in self.rows:
            out.append(f"{name:<34}{ff:>8}{logic:>8}{ff + logic:>8}")
        out.append(f"{'total':<34}{'':>8}{'':>8}{self.total:>8}")
        out.append(f"budget after routing margin: {self.budget} cells, utilization {self.total / self.budget:.0%}")
        return "\n".join(out)


def estimate(p: AreaParams = None) -> AreaReport:
    p = p or AreaParams()
    w = p.data_width
    pin_bits = max(1, (p.num_pins - 1).bit_length())
    addr_bits = max(1, (p.imem_words - 1).bit_length())
    rows = []

    core_ff = (
        addr_bits + 4            # pc, delay
        + 2 * w                  # x, y
        + 2 * (w + 6)            # isr/osr + shift counts
        + 24                     # clock divider accumulator
        + 24                     # stall/timeout counter
        + 8 + 2                  # status, edge/irq wait
        + 8 + 24                 # cap last + run length
    )
    cfg_ff = 24 + 5 * pin_bits + 2 * 3 + 2 + 2 + 2 * 5 + 24 + 4 * addr_bits
    core_logic = 900 + 12 * w    # decode, ALU, shifters, condition mux
    rows.append((f"SM core x{p.num_sm}", p.num_sm * (core_ff + cfg_ff), p.num_sm * core_logic))

    fifo_ff = 2 * p.fifo_depth * w
    rows.append((f"FIFOs ({p.fifo_depth} deep) x{p.num_sm}", p.num_sm * fifo_ff, p.num_sm * 2 * (w + 20)))

    if p.line_unit:
        line_ff = p.crc_width * 3 + 1 + 2 + (32 + 6 + 8 + 3 + 3) * 2 + 8 + 8
        line_logic = 4 * p.crc_width + 250
        rows.append((f"line unit + CRC x{p.num_sm}", p.num_sm * line_ff, p.num_sm * line_logic))

    imem_bits = p.imem_words * 16
    if p.imem_as_sram:
        rows.append(("imem (SRAM macro, cell-equivalent)", 0, imem_bits // 4))
    else:
        rows.append(("imem (flops + read mux)", imem_bits, imem_bits // 2 * p.num_sm // 2))

    rows.append(("GPIO out/oe/od, sync, irq", 3 * p.num_pins + 2 * p.num_pins + 8, 6 * p.num_pins * p.num_sm // 2))
    rows.append(("host SPI link + register map", 120, 450))

    total = sum(ff + logic for _, ff, logic in rows)
    budget = int(p.tiles * p.cells_per_tile * (1 - p.routing_margin))
    return AreaReport(rows, total, budget)
