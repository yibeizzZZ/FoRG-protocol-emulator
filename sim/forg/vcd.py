"""Minimal VCD writer that records chip pins and state machine PCs every cycle."""

from .chip import PIN_NAMES


class VcdWriter:
    def __init__(self, chip, path, pins=None, labels=None, sms=True):
        self.chip = chip
        self.f = open(path, "w")
        self.pins = list(range(chip.num_pins)) if pins is None else list(pins)
        self.labels = labels or {}
        self.sms = [m for m in chip.sms if m.enabled] if sms else []
        self.prev = {}
        self.ids = {}
        n = 0

        def ident():
            nonlocal n
            s = ""
            k = n
            while True:
                s += chr(33 + k % 94)
                k //= 94
                if not k:
                    break
            n += 1
            return s

        f = self.f
        f.write("$timescale 1ps $end\n$scope module forg $end\n")
        for p in self.pins:
            self.ids[("pin", p)] = ident()
            name = self.labels.get(p, PIN_NAMES[p])
            if name != PIN_NAMES[p]:
                name = f"{name}_{PIN_NAMES[p]}"
            f.write(f"$var wire 1 {self.ids[('pin', p)]} {name} $end\n")
        for m in self.sms:
            self.ids[("pc", m.index)] = ident()
            f.write(f"$var reg 6 {self.ids[('pc', m.index)]} sm{m.index}_pc $end\n")
        f.write("$upscope $end\n$enddefinitions $end\n")
        chip.watchers.append(self.sample)

    def sample(self, cycle, nets):
        changes = []
        for p in self.pins:
            v = (nets >> p) & 1
            key = ("pin", p)
            if self.prev.get(key) != v:
                self.prev[key] = v
                changes.append(f"{v}{self.ids[key]}")
        for m in self.sms:
            key = ("pc", m.index)
            if self.prev.get(key) != m.pc:
                self.prev[key] = m.pc
                changes.append(f"b{m.pc:b} {self.ids[key]}")
        if changes:
            t = int(round(cycle * self.chip.period_ns * 1000))
            self.f.write(f"#{t}\n" + "\n".join(changes) + "\n")

    def close(self):
        if self.chip.watchers and self.sample in self.chip.watchers:
            self.chip.watchers.remove(self.sample)
        self.f.close()
