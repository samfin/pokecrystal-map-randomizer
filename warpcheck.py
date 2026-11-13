"""Objects / hidden items must never sit on a warp tile or on a cell next to one."""
import sys; sys.path.insert(0, '.')
from maprando.io.disasm import Disasm
from maprando.model.movement import load_permissions
from maprando.model.catalog import Catalog
from maprando.generators.caves import CaveGenerator, GenError
from generate import CAVES
d = Disasm('../pokecrystal-speedchoice2'); perms = load_permissions(d); cats = {}
bad = 0; total = 0
for sd in (sys.argv[1:] or ['1', '2', '3']):
    for n in CAVES:
        m = d.maps[n]; cats.setdefault(m.tileset, Catalog(d, m.tileset, perms, list(d.maps.values())))
        try: g = CaveGenerator(d, m, perms, cats[m.tileset]); m2, _ = g.generate(sd, 400)
        except GenError: continue
        near = set()
        for w in m2.warps:
            for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)): near.add((w.x + dx, w.y + dy))
        vanilla = {(o.x, o.y) for o in m.objects} | {(b.x, b.y) for b in m.bg_events}
        for kind, e in [('obj', o) for o in m2.objects] + [('bg', b) for b in m2.bg_events]:
            total += 1
            if (e.x, e.y) in near and (e.x, e.y) not in vanilla:
                bad += 1
                print(f'  seed {sd} {n}: {kind} {getattr(e, "const", "") or getattr(e, "script", "")} at {(e.x, e.y)} next to/on a warp')
print(f'{bad} misplaced of {total} placed objects/hidden items')
