#!/usr/bin/env python3
"""Compare vanilla and generated layouts: mean walk entry->anchor, mean farthest anchor, openness.

    python3 metrics.py [--seed S] [--maps A,B] [--tries N]"""
import argparse, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from maprando.io.disasm import Disasm, load_disasm
from maprando.model.movement import load_permissions, build_model
from maprando.model.catalog import Catalog
from maprando.generators.caves import CaveGenerator, GenError
from maprando.model.metrics import steps, openness
from maprando.maps import ALL_MAPS, KNOWN_UNSUPPORTED

ap = argparse.ArgumentParser(); ap.add_argument('--seed', default='1'); ap.add_argument('--maps')
ap.add_argument('--tries', type=int, default=150); a = ap.parse_args()
names = a.maps.split(',') if a.maps else [n for n in ALL_MAPS if n not in KNOWN_UNSUPPORTED]
d = load_disasm(os.path.join(HERE, '..', 'pokecrystal-speedchoice2')); perms = load_permissions(d); cats = {}
S = {'v': [0, 0, 0], 'g': [0, 0, 0]}; k = 0
print(f'{"map":26s} {"walk v->g":>14s} {"farthest v->g":>16s} {"open% v->g":>12s}  s')
for n in names:
    m = d.maps[n]; cats.setdefault(m.tileset, Catalog(d, m.tileset, perms, list(d.maps.values())))
    t = time.time()
    try: m2, _ = CaveGenerator(d, m, perms, cats[m.tileset]).generate(a.seed, a.tries)
    except GenError: print(f'{n:26s} failed'); continue
    v = steps(build_model(d, m, perms)) + (openness(build_model(d, m, perms)),)
    g = steps(build_model(d, m2, perms)) + (openness(build_model(d, m2, perms)),)
    for i in range(3): S['v'][i] += v[i]; S['g'][i] += g[i]
    k += 1
    print(f'{n:26s} {v[0]:6.1f}->{g[0]:6.1f} {v[1]:7.1f}->{g[1]:7.1f} {100*v[2]:5.0f}->{100*g[2]:4.0f}  {time.time()-t:4.1f}', flush=True)
if k:
    print(f'{"MEAN":26s} {S["v"][0]/k:6.1f}->{S["g"][0]/k:6.1f} {S["v"][1]/k:7.1f}->{S["g"][1]/k:7.1f} '
          f'{100*S["v"][2]/k:5.0f}->{100*S["g"][2]/k:4.0f}')
