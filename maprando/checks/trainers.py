"""Trainers that walk up to the player must never trap them.

A trainer that sees you walks along its line of sight and stops next to you, and stays there.
For every trainer, every direction it may face (the Item Randomizer can flip facings, spinners
face anywhere) and every cell it may stop on, check that
  - the stop cell does not split the walkable area it stands in, and
  - the cell it leaves does not join walkable areas that were apart (a trainer used as a wall)."""
from maprando.model.movement import classify

D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
FACING = {'SPRITEMOVEDATA_STANDING_DOWN': (0, 1), 'SPRITEMOVEDATA_STANDING_UP': (0, -1),
          'SPRITEMOVEDATA_STANDING_LEFT': (-1, 0), 'SPRITEMOVEDATA_STANDING_RIGHT': (1, 0)}

def facings(o):
    f = FACING.get(o.movement)
    if f is None: return list(D4)            # spinners and anything unusual: all four
    return [f, (-f[0], -f[1])]               # its own facing and the flipped one

def trainers(mm):
    out = []
    for c, (kind, bits, o) in mm.objects.items():
        if kind == 'wall' and o.obj_type == 'OBJECTTYPE_TRAINER' and int(o.sight or 0) > 0:
            out.append((c, o))
    return out

def unsafe_trainers(mm, cells=None):
    """trainers (object events) that could trap the player; cells limits which trainer cells count."""
    W, H = mm.w, mm.h
    walls = {c for c, (kind, _, _) in mm.objects.items() if kind == 'wall'}
    def open_(c): return classify(mm, mm.coll[c]) in ('land', 'water') and c not in walls
    free = {c for c in range(W * H) if open_(c)}
    def label(cells):
        lab, k = {}, 0
        for c0 in cells:
            if c0 in lab: continue
            k += 1; lab[c0] = k; st = [c0]
            while st:
                c = st.pop(); x, y = c % W, c // W
                for dx, dy in D4:
                    if 0 <= x + dx < W and 0 <= y + dy < H:
                        n = c + dx + dy * W
                        if n in cells and n not in lab: lab[n] = k; st.append(n)
        return lab, k
    base, kb = label(free)
    bad = []
    for c, o in trainers(mm):
        if cells is not None and c not in cells: continue
        x, y = c % W, c // W
        # leaving its cell must not join two separate areas
        around = {base[c + dx + dy * W] for dx, dy in D4
                  if 0 <= x + dx < W and 0 <= y + dy < H and (c + dx + dy * W) in free}
        sight = int(o.sight)
        stops = []
        for dx, dy in facings(o):
            for k in range(1, sight):           # player stands at k+1 <= sight
                sx, sy = x + dx * k, y + dy * k
                if not (0 <= sx < W and 0 <= sy < H): break
                s = sx + sy * W
                if s not in free: break
                nx, ny = sx + dx, sy + dy        # the player's cell must be open too
                if not (0 <= nx < W and 0 <= ny < H) or (nx + ny * W) not in free: break
                stops.append(s)
        if stops and len(around) > 1: bad.append(o); continue
        for s in stops:
            cells2 = (free | {c}) - {s}
            lab, k2 = label(cells2)
            # no area may split: the cells of each old area keep one label
            groups = {}
            split = False
            for q in cells2:
                if q == c: continue
                b = base.get(q)
                if groups.setdefault(b, lab[q]) != lab[q]: split = True; break
            if split: bad.append(o); break
    return bad
