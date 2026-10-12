"""Read completed LibreLane reports; never launch or alter an EDA run."""
import argparse
import json
from pathlib import Path
import re

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('run', type=Path)
args = parser.parse_args()
reports = []
for step in sorted(args.run.glob('*-openroad-sta*')):
    if not (step / 'state_out.json').exists():
        continue
    state = json.loads((step / 'state_out.json').read_text(encoding='utf-8'))
    metrics = state.get('metrics', {})
    for path in sorted(list(step.glob('nom_*/max.rpt')) + list(step.glob('max.rpt'))):
        text = path.read_text(encoding='utf-8')
        blocks = text.split('Startpoint: ')[1:]
        candidates = []
        for block in blocks:
            slack = re.search(r'([-\d.]+)\s+slack\s+\(', block)
            if slack:
                candidates.append((float(slack[1]), block))
        if not candidates:
            continue
        slack, block = min(candidates, key=lambda pair: pair[0])
        explicit_corner = re.search(r'Corner: (\S+)', block)
        corner = explicit_corner[1] if explicit_corner else path.parent.name
        row = dict(stage=step.name, corner=corner, setup_slack_ns=slack,
                   startpoint=block.splitlines()[0],
                   endpoint=re.search(r'Endpoint: (.*)', block)[1],
                   report=str(path))
        arrival = re.search(r'([-\d.]+)\s+data arrival time', block)
        if arrival:
            row['arrival_ns'] = float(arrival[1])
        row['metrics'] = {k: v for k, v in metrics.items()
                          if ('cap' in k or 'slew' in k or 'hold' in k)
                          and 'violation' in k and
                          f'__corner:{corner}' in k}
        # List output-pin load/slew rows from the selected path.
        row['path_loads'] = [dict(fanout=int(a), cap_pf=float(b), slew_ns=float(c),
                                 delay_ns=float(d), pin=f, cell=g)
            for a, b, c, d, _, f, g in re.findall(
                r'\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([-\d.]+)\s+'
                r'([-\d.]+)\s+[\^v] (\S+) \(([^)]+)\)', block)]
        reports.append(row)
print(json.dumps(reports, indent=2))
