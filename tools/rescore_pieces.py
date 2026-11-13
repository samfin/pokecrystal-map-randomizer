#!/usr/bin/env python3
"""Re-evaluate every piece in the Ice Path B1F chamber library with the current checks and metric
(moves, submoves); drops pieces that no longer pass."""
import json, os, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from maprando.io.disasm import load_disasm
from maprando.model.movement import load_permissions
from maprando.model.catalog import Catalog
from maprando.checks.boulder_room import BoulderRoom
from maprando.generators.gyms.blackthorn import BoulderGymScratch
from maprando.generators.chambers import Piece

def library_path(name):
    return os.path.join(ROOT, 'maprando', 'data', f'{name}_pieces.json')
LIBRARY = library_path('IcePathB1F')
d = load_disasm(os.path.join(ROOT, '..', 'pokecrystal-speedchoice2')); p = load_permissions(d)
m = d.maps['IcePathB1F']; br = BoulderRoom(d, m, p, Catalog(d, m.tileset, p, [m]))
g = BoulderGymScratch(d, m, p, free=br.free, room=br.room, move_stairs=False)
holes = [(0, g.unit_pat[2], g.unit_q[2]), (1, g.unit_pat[3], g.unit_q[3])]
lib = json.load(open(LIBRARY)); keep = []; changed = 0
for q in lib['pieces']:
    bw, bh, dx, dy, mode = q['key'].split(','); bw, bh, dx, dy = int(bw), int(bh), int(dx), int(dy)
    pc = Piece(bw, bh, (dx, dy), holes, mode, g.pats)
    st = {'tiles': {(x + 1, y + 1): row[x] for y, row in enumerate(q['tiles']) for x in range(len(row))},
          'hole_at': [tuple(b) for b in q['holes']], 'door': tuple(q['door']),
          'boulders': {i: tuple(q['boulders'][i]) for i in range(2)}}
    e, mv, info = pc.evaluate(st)
    if e: continue
    if mv != q['moves']: changed += 1
    q['moves'], q['submoves'] = mv, info['submoves']; keep.append(q)
print(f'{len(keep)} of {len(lib["pieces"])} pieces kept; moves changed on {changed}')
print('(moves, submoves):', sorted(collections.Counter((q['moves'], q['submoves']) for q in keep).items()))
lib['pieces'] = keep; json.dump(lib, open(LIBRARY, 'w'))
