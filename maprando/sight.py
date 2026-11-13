"""Trainers the player cannot get past without being seen (a forced battle).

A trainer sees along its facing direction up to its sight range; walls and other objects block the
view. The Item Randomizer can flip facings and spinners turn, so every facing facings() allows
counts. A trainer is forced when something the player can reach in the model (a warp, a person,
an item, a sign) cannot be reached without stepping into some trainer's view."""
from .model import classify
from .trainers import trainers, facings
from .reach import Searcher, entry_starts, anchor_masks

def sight_cells(mm):
    """{cell: [trainer objects that see it]}."""
    walls = {c for c, (kind, _, _) in mm.objects.items() if kind in ('wall', 'bits')}
    seen = {}
    for c, o in trainers(mm):
        x, y = c % mm.w, c // mm.w
        for dx, dy in facings(o):
            for k in range(1, int(o.sight) + 1):
                sx, sy = x + dx * k, y + dy * k
                if not mm.inb(sx, sy): break
                s = mm.idx(sx, sy)
                if s in walls or classify(mm, mm.coll[s]) is None: break
                seen.setdefault(s, []).append(o)
    return seen

def forced(mm):
    """{anchor id: [trainer consts in the way]} for anchors reachable only through a trainer's view."""
    seen = sight_cells(mm)
    full = (1 << 40) - 1
    def reach(avoid):
        s = Searcher(mm)
        if avoid:
            n0 = s._neighbors
            s._neighbors = lambda c, m: [(d, r) for d, r in n0(c, m) if d not in seen]
        sets = {}
        for e in mm.anchors:
            if e.is_entry:
                st = [(c, m) for c, m in [(c, full) for c, _ in entry_starts(mm, e)] if not (avoid and c in seen)]
                for c, ms in s.search(st).items(): sets.setdefault(c, []).extend(ms)
        return sets
    a, b = reach(False), reach(True)
    out = {}
    tr = {f'obj:{o.const or o.index}' for _, (kind, _b, o) in mm.objects.items() if o.obj_type == 'OBJECTTYPE_TRAINER'}
    for an in mm.anchors:
        if an.is_entry and an.kind != 'warp': continue
        if an.id in tr: continue                       # walking up to a trainer is choosing to fight
        if anchor_masks(mm, a, an) and not anchor_masks(mm, b, an):
            near = sorted({o.const for c in an.cells for o in seen.get(c, [])}) or ['(path crosses a view)']
            out[an.id] = near
    return out
