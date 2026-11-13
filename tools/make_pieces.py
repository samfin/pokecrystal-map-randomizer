#!/usr/bin/env python3
"""Build the library of boulder-room chambers for a map (maprando/data/<map>_pieces.json).

    python3 tools/make_pieces.py [--map IcePathB1F|BlackthornGym2F] [--per 10] [--budget 160]

Every piece size, doorway side and kind (open / gated) gets `--per` pieces needing at least 3 boulder
moves (2 boulders). The run stops after --budget seconds and picks up where it left off next time
(the file is written after every piece). Pieces are deterministic: piece k of a kind always comes
from the same seeds."""
import argparse, json, os, random, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from maprando.io.disasm import load_disasm
from maprando.model.movement import load_permissions
from maprando.model.catalog import Catalog
from maprando.checks.boulder_room import BoulderRoom
from maprando.generators.gyms.blackthorn import BoulderGymScratch
from maprando.generators.chambers import Piece, library_path, SIZES_FOR, SIDES, piece_kinds, rock_sides_for

ap = argparse.ArgumentParser(); ap.add_argument('--per', type=int, default=10); ap.add_argument('--budget', type=float, default=160)
ap.add_argument('--min-moves', type=int, default=3); ap.add_argument('--map', default='IcePathB1F')
a = ap.parse_args()
d = load_disasm(os.path.join(ROOT, '..', 'pokecrystal-speedchoice2')); p = load_permissions(d)
m = d.maps[a.map]
if a.map == 'IcePathB1F':
    br = BoulderRoom(d, m, p, Catalog(d, m.tileset, p, [m]))
    g = BoulderGymScratch(d, m, p, free=br.free, room=br.room, move_stairs=False)
else:
    g = BoulderGymScratch(d, m, p)
LIBRARY = library_path(a.map)
rs = rock_sides_for(g.pats)
# the holes of each kind, in order (a build maps them onto the map's holes of that kind)
kinds = {mode: ([(i, g.unit_pat[w], g.unit_q[w]) for i, w in enumerate(hs)], nx) for mode, hs, nx in piece_kinds(g)}
lib = json.load(open(LIBRARY)) if os.path.exists(LIBRARY) else {'pieces': [], 'tried': {}}
t0 = time.time()
for mode in [k for k in ('gated', 'open') if k in kinds]:
    holes, nx = kinds[mode]
    for bw, bh in SIZES_FOR[a.map]:
        for side in SIDES:
            key = f'{bw},{bh},{side[0]},{side[1]},{mode}'
            have = sum(1 for q in lib['pieces'] if q['key'] == key)
            k = lib['tried'].get(key, 0)
            while have < a.per and k < a.per * 12:
                if time.time() - t0 > a.budget:
                    json.dump(lib, open(LIBRARY, 'w')); print('budget spent; run again to continue'); sys.exit(0)
                pc = Piece(bw, bh, side, holes, mode, g.pats, extras=nx, rock_sides=rs)
                r = pc.generate(random.Random(f'{key}-{k}'))
                k += 1; lib['tried'][key] = k
                if r and r[2]['moves'] >= a.min_moves:
                    st = r[1]
                    lib['pieces'].append(dict(key=key, moves=r[2]['moves'], submoves=r[2]['submoves'],
                        tiles=[''.join(st['tiles'][(x, y)] for x in range(1, bw * 2 + 1)) for y in range(1, bh * 2 + 1)],
                        holes=[list(b) for b in st['hole_at']],
                        boulders=[list(st['boulders'][w]) for w in [h[0] for h in holes] + pc.extras], door=list(st['door'])))
                    have += 1
                json.dump(lib, open(LIBRARY, 'w'))
            print(key, have, 'pieces', f'({k} tries)', flush=True)
print('library complete:', len(lib['pieces']), 'pieces')
