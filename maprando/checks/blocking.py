"""Paths blocked by people: count how the walkable ground falls apart when every person (fixed or
walking, trainer or not; item balls excluded) stands on its tile as a wall. The requirement model
treats walking NPCs as passable, so two people side by side in a corridor could slip past it."""
from maprando.model.movement import classify

D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))

def people_cells(m):
    """tiles where people stand (not item balls, boulders or rocks)."""
    return {(o.x, o.y) for o in m.objects if o.obj_type != 'OBJECTTYPE_ITEMBALL'
            and 'BOULDER' not in o.sprite and 'ROCK' not in o.sprite}

def split_pairs(mm, m, reach):
    """pairs of reachable cells (as a frozenset of two component labels' representatives) that are
    joined when people are ignored but separated when everyone stands still."""
    W, H = mm.w, mm.h
    people = {mm.idx(x, y) for x, y in people_cells(m) if mm.inb(x, y)}
    walk = {c for c in reach if classify(mm, mm.coll[c]) in ('land', 'water')}
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
        return lab
    a = label(walk); b = label(walk - people)
    groups = {}
    for c, la in a.items():
        if c in people: continue
        groups.setdefault(la, set()).add(b[c])
    return sum(len(v) - 1 for v in groups.values())

def trainer_people_traps(mm, m, reach):
    """trainers that, after walking up to the player, close a path together with anyone else
    (walking NPCs included). Returns [(trainer const, stop cell)]."""
    from maprando.checks.trainers import facings
    W, H = mm.w, mm.h
    walk = {c for c in reach if classify(mm, mm.coll[c]) in ('land', 'water')}
    people = {mm.idx(x, y) for x, y in people_cells(m) if mm.inb(x, y)}
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
        return lab
    base = label(walk - people)
    out = []
    for o in m.objects:
        if o.obj_type != 'OBJECTTYPE_TRAINER' or int(o.sight or 0) <= 0 or not mm.inb(o.x, o.y): continue
        org = mm.idx(o.x, o.y)
        for dx, dy in facings(o):
            for k in range(1, int(o.sight)):
                sx, sy = o.x + dx * k, o.y + dy * k
                if not mm.inb(sx, sy): break
                s = mm.idx(sx, sy)
                if s not in walk or s in people: break
                if not mm.inb(sx + dx, sy + dy) or mm.idx(sx + dx, sy + dy) not in walk: break
                cells = (walk - people - {s}) | {org}
                lab = label(cells); seen = {}
                bad = False
                for c in cells:
                    if c == org: continue
                    if seen.setdefault(base[c], lab[c]) != lab[c]: bad = True; break
                if bad: out.append((o.const, (sx, sy))); break
    return out
