#!/usr/bin/env python3
"""Phase 0: extract per-map requirement matrices from the disassembly."""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maprando.parse import Disasm
from maprando.model import build_model, load_permissions
from maprando.reach import requirement_matrix, mask_names

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--disasm', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pokecrystal-speedchoice2'))
    ap.add_argument('--maps', nargs='*')
    ap.add_argument('--env', nargs='*', default=['ROUTE', 'CAVE', 'DUNGEON'])
    ap.add_argument('--out', default='out/analysis.json')
    ap.add_argument('--show', action='store_true')
    a = ap.parse_args()
    d = Disasm(a.disasm)
    perms = load_permissions(d)
    names = a.maps or sorted(n for n, m in d.maps.items() if m.environment in a.env)
    result = {}
    t0 = time.time()
    for n in names:
        m = d.maps[n]
        mm = build_model(d, m, perms)
        mat = requirement_matrix(mm)
        result[n] = {
            'environment': m.environment, 'tileset': m.tileset,
            'size_blocks': [m.width, m.height], 'features': sorted(mm.features),
            'anchors': {an.id: {'kind': an.kind, 'detail': an.detail, 'entry': an.is_entry} for an in mm.anchors},
            'matrix': {e: {aid: [mask_names(mm, x) for x in ms] for aid, ms in row.items()} for e, row in mat.items()},
        }
        if a.show:
            print(f'== {n} ({m.environment}, {m.width}x{m.height}) features={sorted(mm.features)}')
            for e, row in mat.items():
                print(f'  from {e}:')
                for an in mm.anchors:
                    if an.id == e: continue
                    ms = row.get(an.id)
                    s = 'UNREACHABLE' if not ms else ' | '.join('+'.join(mask_names(mm, x)) or '-' for x in ms)
                    print(f'    {an.id:28s} {s:40s} {an.detail}')
    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    with open(a.out, 'w') as f: json.dump(result, f, indent=1)
    print(f'{len(names)} maps analyzed in {time.time()-t0:.1f}s -> {a.out}')

if __name__ == '__main__':
    main()
