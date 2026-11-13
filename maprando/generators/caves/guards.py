"""Scripted guards and triggers: who blocks which path, and who must not stand in a wall."""
from maprando.model.movement import classify, open_tile
from maprando.model.reach import requirement_matrix

def trigger_matrix(mm, m, src):
    """the requirement matrix with every gate trigger (a coord event whose scene moves the player)
    as a wall: what you reach without stepping on one. None if the map has none."""
    from maprando.checks.scenes import gate_triggers
    from maprando.model.reach import requirement_matrix
    trig = [e for e in gate_triggers(m, src) if mm.inb(e.x, e.y)]
    if not trig: return None
    saved = dict(mm.objects)
    try:
        for e in trig: mm.objects[mm.idx(e.x, e.y)] = ('wall', 0, None)
        # fixed places only (warps, edges, Fly spots, coord events): items and people are placed
        # anew on purpose, and the usual matrix already covers what each needs
        fixed = lambda a: not (a.startswith('bg:') or a.startswith('obj:'))
        return {e: {a: v for a, v in row.items() if fixed(a)}
                for e, row in requirement_matrix(mm, boulders=False).items() if fixed(e)}
    finally:
        mm.objects.clear(); mm.objects.update(saved)

def guarded_actors(m):
    """cutscene actors with no talk script (a trainer-type object whose script is ObjectEvent:
    Burned Tower 1F's rival): talked to, the game crashes, so only a coord event may reach them"""
    return [o for o in m.objects if o.obj_type == 'OBJECTTYPE_TRAINER' and o.script == 'ObjectEvent']

def guard_leaks(mm2, m2, m=None):
    """guarded actors someone can walk up to from the map's entrances without stepping on a coord
    event (Cut and Rock Smash allowed, other people out of the way; warps that lead to a floor below
    do not count as ways in: you come up them only after the scene)"""
    from maprando.model.movement import open_tile
    m = m or m2
    acts = guarded_actors(m)
    if not acts or not m2.coord_events: return []
    W = mm2.w
    trig = {mm2.idx(e.x, e.y) for e in m2.coord_events}
    # warps fire only on warp tiles (a hole, a door): leftover warps on plain floor are just floor
    from maprando.model.movement import WARP_TILES
    warps = {mm2.idx(w.x, w.y) for w in m2.warps if mm2.inb(w.x, w.y) and mm2.coll[mm2.idx(w.x, w.y)] in WARP_TILES}
    pos = {o.index: mm2.idx(o.x, o.y) for o in m2.objects}
    act_cells = {pos[o.index] for o in acts}
    def ok(c): return 0 <= c < W * mm2.h and open_tile(mm2, c) and c not in trig and c not in warps and c not in act_cells
    def nb(c):
        x = c % W
        return [n for n in (c - 1 if x > 0 else -1, c + 1 if x < W - 1 else -1, c - W, c + W) if 0 <= n < W * mm2.h]
    starts = [n for w in m2.warps if w.dest_map != m2.const and not ('B1F' in w.dest_map or 'BASEMENT' in w.dest_map)
              and mm2.inb(w.x, w.y) and mm2.coll[mm2.idx(w.x, w.y)] in WARP_TILES
              for n in nb(mm2.idx(w.x, w.y)) if ok(n)]
    seen, st = set(starts), list(starts)
    while st:
        c = st.pop()
        for n in nb(c):
            if n not in seen and ok(n): seen.add(n); st.append(n)
    return [o.const for o in acts if any(n in seen for n in nb(pos[o.index]))]

def guard_pocket(mm, m):
    """the vanilla pocket round each guarded actor: the tiles reached from beside it without a coord
    event or a firing warp, and every tile touching them (its walls)"""
    from maprando.model.movement import open_tile, WARP_TILES
    W = mm.w
    trig = {mm.idx(e.x, e.y) for e in m.coord_events}
    warps = {mm.idx(w.x, w.y) for w in m.warps if mm.inb(w.x, w.y) and mm.coll[mm.idx(w.x, w.y)] in WARP_TILES}
    def nb(c):
        x = c % W
        return [n for n in (c - 1 if x > 0 else -1, c + 1 if x < W - 1 else -1, c - W, c + W) if 0 <= n < W * mm.h]
    out = set()
    for o in guarded_actors(m):
        a = mm.idx(o.x, o.y); seen = {a}; st = [a]
        while st:
            c = st.pop()
            for n in nb(c):
                if n in seen: continue
                seen.add(n)
                if open_tile(mm, n) and n not in trig and n not in warps: st.append(n)
        out |= seen            # the pocket and what bounds it (walls, the trigger, the hole)
    return out

def standing_in_walls(mm, m, mm2, m2, anchor_zones=None):
    """objects on floor in vanilla (and reached there) that start on a tile nobody can stand on"""
    out = []
    for o0, o in zip(m.objects, m2.objects):
        if not mm.inb(o0.x, o0.y) or not mm2.inb(o.x, o.y): continue
        if classify(mm, mm.coll[mm.idx(o0.x, o0.y)]) is None: continue           # in a wall in vanilla too
        if anchor_zones is not None and not anchor_zones.get(f'obj:{o0.const or o0.index}'): continue  # parked
        if anchor_zones is None and not any(mm2.inb(o.x + dx, o.y + dy) and classify(mm2, mm2.coll[mm2.idx(o.x + dx, o.y + dy)])
                                            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
            continue          # buried out of reach (no open tile beside it): parked on purpose
        if classify(mm2, mm2.coll[mm2.idx(o.x, o.y)]) is None: out.append(o0.const or f'object {o0.index}')
    return out
