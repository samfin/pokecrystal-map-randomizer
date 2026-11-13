"""How many boulder moves a Strength puzzle takes (and, as a tiebreak, submoves: within a move, a
run of pushes in one direction; A up, A up, B down, B up, A right, A left = 3 moves, 1 + 2 + 2
submoves). Both are minimised together: the fewest moves, then the fewest submoves among those. A move is a run of pushes of the same boulder:
pushing A three times, then B twice, then C, then A again is 4 moves (AAABBCA); walking around
between two pushes of the same boulder does not end a move. min_moves is the fewest moves after
which the player can walk to a goal tile (0-1 search: another push of the boulder pushed last costs
nothing, any other boulder costs 1)."""


K = 10000          # cost = moves * K + submoves: fewest moves first, then fewest submoves
# weight=0: submoves only (a new boulder or a new direction costs one); detail then gives (None, submoves)
def _res(cost, weight, detail):
    if not detail: return cost // weight if weight else cost
    return (cost // weight, cost % weight) if weight else (None, cost)


def min_moves(W, floor, boulders, starts, goal, limit=40000, detail=False, weight=K):
    """floor: tiles the player walks on and boulders can be pushed onto (int cells, width W);
    boulders: {id: cell}; starts: where the player comes in; goal: tiles to reach. Returns the
    fewest moves (0 if the goal is open from the start) or None (never, or more than `limit`
    positions explored). Areas are bitmasks (one bit per tile), grown by shifts."""
    ids = sorted(boulders, key=str)
    if not floor: return None
    n = max(floor) + W + 1
    F = 0
    for c in floor: F |= 1 << c
    notL = notR = 0                       # tiles a step right / left may land on
    for c in range(n):
        if c % W != 0: notL |= 1 << c
        if c % W != W - 1: notR |= 1 << c
    G = 0
    for c in goal: G |= 1 << c
    comps = {}
    def area(free, c):
        r = 1 << c
        while True:
            m = (r | ((r << 1) & notL) | ((r >> 1) & notR) | (r << W) | (r >> W)) & free
            if m == r: return r
            r = m
    def comp(pos, c):
        """the area (bitmask) the player at c walks in with the boulders at pos."""
        cs = comps.get(pos)
        if cs is None: cs = comps[pos] = []
        b = 1 << c
        for m in cs:
            if m & b: return m
        free = F
        for x in pos: free &= ~(1 << x)
        if not free & b: return 0
        m = area(free, c); cs.append(m)
        return m
    pos0 = tuple(boulders[i] for i in ids)
    import heapq
    best = {}; heap = []; tick = 0
    for s in starts:
        m = comp(pos0, s)
        if m:
            k = (pos0, m, -1, 0)
            if k not in best: best[k] = 0; heapq.heappush(heap, (0, tick, k)); tick += 1
    while heap:
        cost, _t, k = heapq.heappop(heap)
        if best.get(k) != cost: continue
        pos, R, last, ldir = k
        if R & G: return _res(cost, weight, detail)
        occ = 0
        for x in pos: occ |= 1 << x
        for bi, c in enumerate(pos):
            for dd in (1, -1, W, -W):
                if dd in (1, -1) and ((c + dd) // W != c // W or (c - dd) // W != c // W): continue
                p, t = c - dd, c + dd
                if p < 0 or t < 0 or not (R >> p) & 1 or not (F >> t) & 1 or (occ >> t) & 1: continue
                npos = pos[:bi] + (t,) + pos[bi + 1:]
                k2 = (npos, comp(npos, c), bi, dd)
                c2 = cost + (0 if (bi, dd) == (last, ldir) else 1 if bi == last else weight + 1)
                if k2 in best and best[k2] <= c2: continue
                if len(best) >= limit: return None
                best[k2] = c2
                heapq.heappush(heap, (c2, tick, k2)); tick += 1
    return None


def drop_moves(W, floor, boulders, own, starts, boxonly=frozenset(), limit=30000, detail=False, weight=K):
    """the fewest boulder moves (runs of pushes of one boulder) that drop every boulder with a hole
    of its own (`own`: {id: hole cell}) into it; a boulder that falls in is gone. Boulders may be
    pushed onto `boxonly` tiles (stairs) but the player never stands there. Holes are not floor.
    None if impossible or more than `limit` positions. (Leaving the room, which puts the boulders
    back, is not used: the count is for one go.)"""
    drop_moves.capped = False
    ids = sorted(boulders, key=str)
    cells = set(floor) | set(boxonly) | set(own.values()) | set(boulders.values()) | set(starts)
    n = (max(cells) if cells else 0) + W + 1
    F = 0
    for c in floor: F |= 1 << c
    BX = 0
    for c in boxonly: BX |= 1 << c
    notL = notR = 0
    for c in range(n):
        if c % W != 0: notL |= 1 << c
        if c % W != W - 1: notR |= 1 << c
    holes = {}
    for i, h in own.items(): holes[h] = i
    comps = {}
    def comp(pos, c):
        cs = comps.get(pos)
        if cs is None: cs = comps[pos] = []
        b = 1 << c
        for m in cs:
            if m & b: return m
        free = F
        for x in pos:
            if x is not None: free &= ~(1 << x)
        if not free & b: return 0
        r = b
        while True:
            m = (r | ((r << 1) & notL) | ((r >> 1) & notR) | (r << W) | (r >> W)) & free
            if m == r: break
            r = m
        cs.append(r)
        return r
    need = {k for k, i in enumerate(ids) if i in own}
    # live tiles: where each boulder can still be pushed into its own hole (pulling it back out of
    # the hole: a boulder at t got there from t-d with the player at t-2d, both on floor); a
    # boulder anywhere else can never finish, so such positions are never explored
    box_ok = F | BX
    live = {}
    for k in need:
        h = own[ids[k]]
        L = {h}; st = [h]
        while st:
            t = st.pop()
            for dd in (1, -1, W, -W):
                b, pl = t - dd, t - 2 * dd
                if dd in (1, -1) and (b // W != t // W or pl // W != t // W): continue
                if b < 0 or pl < 0 or b in L: continue
                if not (box_ok >> b) & 1 or not (F >> pl) & 1: continue
                L.add(b); st.append(b)
        live[k] = L
    if any(boulders[ids[k]] not in live[k] for k in need): return None
    pos0 = tuple(boulders[i] for i in ids)
    import heapq
    def h(pos, last):
        # each boulder still out needs a move (and a submove) of its own; the one pushed last may
        # go on for free
        out = sum(1 for j in need if pos[j] is not None)
        return (out - (1 if last in need and pos[last] is not None else 0)) * (weight + 1)
    best = {}; heap = []; tick = 0
    for s in starts:
        m = comp(pos0, s)
        if m:
            k = (pos0, m, -1, 0)
            if k not in best:
                best[k] = 0; heapq.heappush(heap, (h(pos0, -1), h(pos0, -1), tick, k)); tick += 1
    while heap:
        _f, _h, _t, k = heapq.heappop(heap)
        pos, R, last, ldir = k
        cost = best[k]
        if _f - _h != cost: continue                      # an older, costlier entry
        if all(pos[j] is None for j in need): return _res(cost, weight, detail)
        occ = 0
        for x in pos:
            if x is not None: occ |= 1 << x
        for bi, c in enumerate(pos):
            if c is None: continue
            for dd in (1, -1, W, -W):
                if dd in (1, -1) and ((c + dd) // W != c // W or (c - dd) // W != c // W): continue
                p, t = c - dd, c + dd
                if p < 0 or t < 0 or not (R >> p) & 1 or (occ >> t) & 1: continue
                if not (F >> c) & 1: continue              # the player would step onto the stairs
                if t in holes:
                    if holes[t] != ids[bi]: continue        # never into another boulder's hole
                    npos = pos[:bi] + (None,) + pos[bi + 1:]
                elif (F >> t) & 1 or (BX >> t) & 1:
                    if bi in need and t not in live[bi]: continue      # a dead spot for this boulder
                    npos = pos[:bi] + (t,) + pos[bi + 1:]
                else: continue
                R2 = comp(npos, c)
                if not R2: continue
                k2 = (npos, R2, bi, dd)
                c2 = cost + (0 if (bi, dd) == (last, ldir) else 1 if bi == last else weight + 1)
                if k2 in best and best[k2] <= c2: continue
                if len(best) >= limit:
                    drop_moves.capped = True; return None
                best[k2] = c2
                h2 = h(npos, bi)
                heapq.heappush(heap, (c2 + h2, h2, tick, k2)); tick += 1
    return None


def gym_puzzle(d, m2, perms):
    """the finished map's Strength puzzle (Cianwood Gym): (fewest boulder moves to the leader or
    None, positions explored, positions that shut the player in)."""
    from maprando.model.movement import build_model, classify
    from maprando.checks.boulder_room import player_traps
    from maprando.generators.gyms.standard import GYM_LEADERS
    mm = build_model(d, m2, perms)
    W = mm.w
    people = {mm.idx(o.x, o.y) for o in m2.objects if 'BOULDER' not in o.sprite and mm.inb(o.x, o.y)}
    stones = {o.index: mm.idx(o.x, o.y) for o in m2.objects if 'BOULDER' in o.sprite and mm.inb(o.x, o.y)}
    floor = {c for c in range(W * mm.h) if classify(mm, mm.coll[c]) == 'land' and c not in mm.warp_cells
             and c not in people}
    def nb(c):
        x = c % W
        return [n for n in (c - 1 if x > 0 else -1, c + 1 if x < W - 1 else -1, c - W, c + W) if 0 <= n < W * mm.h]
    doors = {mm.idx(w.x, w.y) for w in m2.warps}
    starts = [n for c in doors for n in nb(c) if n in floor]
    lead = next(o for o in m2.objects if o.sprite in GYM_LEADERS)
    goal = [n for n in nb(mm.idx(lead.x, lead.y)) if n in floor]
    mv = min_moves(W, floor, stones, starts, goal, limit=200000, detail=True)
    sub = min_moves(W, floor, stones, starts, goal, limit=200000, detail=True, weight=0)
    if mv is not None and sub is not None: mv = (mv[0], mv[1], sub[1])   # + the fewest submoves
    tr = player_traps(W, floor, frozenset(), set(), {}, stones, starts, set(starts), limit=200000)
    return mv, (tr[0] if tr else None), (tr[1] if tr else None)


def room_submoves(d, m, m2, perms):
    """a finished boulder room's puzzle (Ice Path B1F, Blackthorn 2F) as readback counts it: the
    fewest submoves that drop every boulder into its own hole (None if not counted)"""
    from maprando.model.movement import build_model, classify
    from maprando.checks.boulder_room import stonetable
    mm = build_model(d, m2, perms); W = mm.w; pairs = stonetable(m)
    ppl = {mm.idx(o.x, o.y) for o in m2.objects if 'BOULDER' not in o.sprite and mm.inb(o.x, o.y)}
    fl = {c for c in range(W * mm.h) if classify(mm, mm.coll[c]) == 'land' and c not in mm.warp_cells and c not in ppl}
    own = {i: mm.idx(m2.warps[w].x, m2.warps[w].y) for i, w in pairs}
    bl = {i: mm.idx(o.x, o.y) for i, o in enumerate(m2.objects) if 'BOULDER' in o.sprite}
    st = [mm.idx(w.x, w.y) for k, w in enumerate(m2.warps) if k not in dict(pairs).values()]
    starts = [c for s_ in st for c in (s_ - 1, s_ + 1, s_ - W, s_ + W) if c in fl]
    r = drop_moves(W, fl, bl, own, starts, boxonly=frozenset(c for c in st if classify(mm, mm.coll[c]) == 'land'),
                   limit=200000, detail=True, weight=0)
    return r[1] if r else None
