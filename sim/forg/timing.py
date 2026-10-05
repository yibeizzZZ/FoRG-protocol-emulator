"""Static cycle-timing analysis of FoRG firmware.

Every FoRG instruction takes exactly 1 + delay SM ticks unless it stalls, so the
cycle count of any control-flow path is known at assembly time. This module
enumerates every path between two labels and checks `.assert_cycles`
constraints, turning "is my baud rate right on every branch?" into a proof
instead of a simulation sample.
"""

from dataclasses import dataclass, field

from . import isa


@dataclass
class PathResult:
    start: str
    end: str
    costs: set = field(default_factory=set)
    stalls: set = field(default_factory=set)
    unbounded: bool = False
    dynamic: bool = False

    @property
    def min(self):
        return min(self.costs) if self.costs else None

    @property
    def max(self):
        return max(self.costs) if self.costs else None


def _can_stall(ins, cfg):
    op, a = ins.op, ins.arg
    if op == isa.OP_WAIT:
        return True
    if op == isa.OP_IN:
        return bool(cfg.get("autopush")) or (a >> 6) == 7
    if op == isa.OP_OUT:
        return bool(cfg.get("autopull")) or (a >> 6) == 7
    if op == isa.OP_CTL:
        sub = a >> 7
        if sub in (isa.CTL_PUSH, isa.CTL_PULL):
            return bool(a & 0x20)
        if sub == isa.CTL_LINE:
            return bool(a & isa.LINE_DRAIN)
        return bool(a & 0x20) and not a & 0x40
    return False


def _successors(prog, pc):
    ins = isa.decode(prog.words[pc])
    nxt = prog.wrap_target if pc == prog.wrap else pc + 1
    if ins.op == isa.OP_JMP:
        cond = ins.arg >> 6
        target = ins.arg & 0x3F
        return [target] if cond == 0 else [target, nxt]
    if ins.op == isa.OP_OUT and (ins.arg >> 6) == 5:
        return None
    if ins.op == isa.OP_MOV and (ins.arg >> 6) == 4:
        return None
    if nxt >= len(prog.words):
        return []
    return [nxt]


def instr_cost(prog, pc):
    _, delay = isa.split_ds(isa.decode(prog.words[pc]).ds, prog.side_count)
    return 1 + delay


def analyze_path(prog, start, end, max_visits=2, max_paths=100_000):
    """All path costs (in SM ticks) from executing `start` until `end` is next reached."""
    s = prog.labels[start]
    ends = {prog.labels[n] for n in end.split("|")}
    res = PathResult(start, end)
    cfg = prog.config
    stack = [(s, 0, {})]
    explored = 0
    while stack:
        pc, cost, visits = stack.pop()
        explored += 1
        if explored > max_paths:
            res.unbounded = True
            break
        ins = isa.decode(prog.words[pc])
        if _can_stall(ins, cfg):
            res.stalls.add(pc)
        cost += instr_cost(prog, pc)
        succ = _successors(prog, pc)
        if succ is None:
            res.dynamic = True
            continue
        if not succ:
            res.unbounded = True
            continue
        for n in succ:
            if n in ends:
                res.costs.add(cost)
                continue
            v = visits.get(n, 0)
            if v >= max_visits:
                res.unbounded = True
                continue
            nv = dict(visits)
            nv[n] = v + 1
            stack.append((n, cost, nv))
    return res


@dataclass
class AssertReport:
    assertion: object
    result: PathResult
    ok: bool
    message: str


def check_asserts(prog):
    reports = []
    div = float(prog.config.get("clkdiv", 1.0))
    for a in prog.asserts:
        r = analyze_path(prog, a.start, a.end)
        problems = []
        if r.unbounded:
            problems.append("unbounded path (loop without reaching end label)")
        if r.dynamic:
            problems.append("computed jump on path")
        if not r.costs:
            problems.append("end label unreachable")
        elif r.min < a.min_cycles or r.max > a.max_cycles:
            problems.append(f"cycles {r.min}..{r.max} outside {a.min_cycles}..{a.max_cycles}")
        span = f"{r.min}" if r.min == r.max else f"{r.min}..{r.max}"
        msg = f"{a.start} -> {a.end}: {span} ticks"
        if div != 1.0 and r.costs:
            msg += f" ({r.min * div:g}..{r.max * div:g} sys cycles)"
        if r.stalls:
            msg += f" [may stall at pc {sorted(r.stalls)}; bound assumes no stall]"
        ok = not problems
        if problems:
            msg += " FAIL: " + "; ".join(problems)
        reports.append(AssertReport(a, r, ok, msg))
    return reports
