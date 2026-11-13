"""Strength boulders move one tile when pushed; they do not vanish.

The requirement model treats a boulder as a cell you can pass with Strength. That holds only if,
from each side the boulder can be approached, some push opens the way: the player stands next to
it, the tile beyond is free floor the boulder can move onto, and with the boulder there everything
that was joined through the boulder's cell is still joined (the boulder must not land in a
one-tile corridor and block it, or block anything else). A boulder cannot be pushed onto an item
ball that is still there, so item ball tiles never count as landing spots."""
from maprando.model.movement import classify, STRENGTH

D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))

def bad_boulders(mm):
    W, H = mm.w, mm.h
    objs = mm.objects
    def land(c): return classify(mm, mm.coll[c]) == 'land' and c not in mm.warp_cells
    def walk(c): return classify(mm, mm.coll[c]) in ('land', 'water') and c not in objs
    free = {c for c in range(W * H) if walk(c)}
    balls = getattr(mm, 'itemballs', set())
    def nb(c):
        x, y = c % W, c // W
        for dx, dy in D4:
            if 0 <= x + dx < W and 0 <= y + dy < H: yield c + dx + dy * W, (dx, dy)
    def comp(cells, start):
        seen = {start}; st = [start]
        while st:
            for n, _ in nb(st.pop()):
                if n in cells and n not in seen: seen.add(n); st.append(n)
        return seen
    bad = []
    # boulders that touch work as one gate (Cianwood Gym's row of three: only the middle one is
    # ever pushed): from every side the group can be walked up to, some push of some boulder in it
    # must open the way to every area around the group, without the moved boulder blocking it
    stones = {B: o for B, (kind, bits, o) in objs.items() if kind == 'bits' and bits == STRENGTH}
    done = set()
    for B0 in stones:
        if B0 in done: continue
        group = comp(set(stones), B0); done |= group
        sides = [(n, B, d) for B in group for n, d in nb(B) if n in free]
        areas = {}
        for n, B, d in sides:
            areas.setdefault(frozenset(comp(free, n)), []).append((n, B, d))
        if len(areas) <= 1: continue                     # nothing behind it: nothing to open
        everything = set().union(*areas)
        ok = True
        for area, ns in areas.items():
            good = False
            for n, B, (dx, dy) in ns:
                if classify(mm, mm.coll[n]) != 'land': continue   # the player pushes standing on land (a ladder is fine)
                x, y = B % W, B // W
                # push away from the player, again and again in a straight line (the player
                # follows) until the boulder rests where it blocks nothing
                for k in range(1, 8):
                    tx, ty = x - dx * k, y - dy * k
                    if not (0 <= tx < W and 0 <= ty < H): break
                    t = tx + ty * W
                    if t not in free or not land(t) or t in balls: break
                    after = comp((free | {B}) - {t}, n)
                    if (everything - {t}) <= after: good = True; break
                if good: break
            if not good: ok = False; break
        if not ok: bad += [stones[B] for B in sorted(group)]
    return bad
