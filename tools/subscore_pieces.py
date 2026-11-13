#!/usr/bin/env python3
"""Add each chamber piece's fewest submoves (runs of one boulder pushed one way, whatever the moves)
to a map's library as `fewest_submoves`; pieces that no longer pass are dropped.

    python3 tools/subscore_pieces.py [--map BlackthornGym2F] [--budget 160]   (resumable)"""
import argparse, collections, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from maprando.parse import load_disasm
from maprando.model import load_permissions
from maprando.gymboulders import BoulderGymScratch
from maprando.chambers import Piece, library_path, piece_kinds, rock_sides_for
ap = argparse.ArgumentParser(); ap.add_argument('--map', default='BlackthornGym2F'); ap.add_argument('--budget', type=float, default=160)
a = ap.parse_args()
d = load_disasm(os.path.join(ROOT, '..', 'pokecrystal-speedchoice2')); p = load_permissions(d)
g = BoulderGymScratch(d, d.maps[a.map], p)
rs = rock_sides_for(g.pats)
kinds = {mode: ([(i, g.unit_pat[w], g.unit_q[w]) for i, w in enumerate(hs)], nx) for mode, hs, nx in piece_kinds(g)}
path = library_path(a.map); lib = json.load(open(path)); t0 = time.time(); keep = []
Piece.weight = 0
for q in lib['pieces']:
    if 'fewest_submoves' in q or time.time() - t0 > a.budget: keep.append(q); continue
    bw, bh, dx, dy, mode = q['key'].split(','); bw, bh, dx, dy = int(bw), int(bh), int(dx), int(dy)
    holes, nx = kinds[mode]
    pc = Piece(bw, bh, (dx, dy), holes, mode, g.pats, extras=nx, rock_sides=rs)
    ids = [h[0] for h in holes] + pc.extras
    st = {'tiles': {(x + 1, y + 1): row[x] for y, row in enumerate(q['tiles']) for x in range(len(row))},
          'hole_at': [tuple(b) for b in q['holes']], 'door': tuple(q['door']),
          'boulders': {ids[i]: tuple(q['boulders'][i]) for i in range(len(ids))}}
    e, _mv, info = pc.evaluate(st)
    if e or not info: continue
    q['fewest_submoves'] = info['submoves']; keep.append(q)
lib['pieces'] = keep; json.dump(lib, open(path, 'w'))
done = [q for q in keep if 'fewest_submoves' in q]
print(f'{len(done)} of {len(keep)} pieces scored ({len(keep) - len(done)} left)')
print('fewest submoves:', sorted(collections.Counter(q['fewest_submoves'] for q in done).items()))
print('submoves at fewest moves:', sorted(collections.Counter(q['submoves'] for q in done).items()))
