#!/usr/bin/env python3
"""Generate caves one by one with the cave generator alone (a quick development check)."""
import argparse, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maprando.io.disasm import Disasm
from maprando.model.movement import load_permissions
from maprando.model.catalog import catalog_for
from maprando.generators.caves import CaveGenerator, GenError
from maprando.maps import CAVES

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--disasm', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pokecrystal-speedchoice2'))
    ap.add_argument('--maps', nargs='*', default=CAVES)
    ap.add_argument('--seed', default='1')
    ap.add_argument('--tries', type=int, default=200)
    a = ap.parse_args()
    d = Disasm(a.disasm)
    perms = load_permissions(d)
    cats = {}
    results = {}
    for n in a.maps:
        m = d.maps[n]
        cat = catalog_for(d, m, perms, cats)
        t0 = time.time()
        try:
            g = CaveGenerator(d, m, perms, cat)
            m2, k = g.generate(a.seed, a.tries)
            results[n] = m2
            print(f'OK   {n:28s} try {k:3d}  {time.time()-t0:5.1f}s')
        except GenError as e:
            print(f'FAIL {n:28s} {e}')
            print('      ', dict(g.errors.most_common(6)) if 'g' in dir() and hasattr(g, 'errors') else '')
    return d, results

if __name__ == '__main__':
    main()

