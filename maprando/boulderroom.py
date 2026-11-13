"""Ice Path B1F's Strength-boulder room, designed on an abstract block grid, then drawn.

Each boulder falls only through its own hole (the map's stonetable pairs a hole warp with a
boulder object); a boulder pushed onto another hole just sits there. Where a B1F hole is does not
matter to B2F (the hole is a warp to a fixed B2F spot and the fallen boulder appears at a fixed B2F
spot), so holes move too: each hole's vanilla block moves as a whole.

The design is a grid over the room's blocks: each free block is floor or wall per quarter (only
patterns the tileset can draw), hole blocks sit somewhere in the room, boulders stand on floor
tiles. A design is accepted only if
  - no boulder can ever be pushed onto another boulder's hole (checked with the other boulders
    removed: more boulders only block pushes, never enable them, so this is a safe over-estimate);
  - in some order every boulder can be pushed into its own hole, with the boulders still waiting
    at their starting spots (unfallen boulders reset when the player re-enters the map, and
    removing a boulder never hurts, so solving any solvable boulder first always works);
  - the same holes and boulders are reachable from the entrances without Strength as in vanilla,
    the rest only past a boulder, and everything with Strength;
  - the drawn map has vanilla's requirement matrix.
Among accepted designs the search prefers more pushes and more changes of push direction."""
import re
from collections import deque
from .model import build_model, classify, COLL_ICE
from .reach import requirement_matrix, Searcher, entry_starts

D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))

class BoulderRoomError(Exception): pass

def stonetable(m):
    src = open(m.asm_path, encoding='utf-8', errors='replace').read()
    consts = [o.const for o in m.objects]
    return [(consts.index(oc), int(w) - 1) for w, oc in re.findall(r'stonetable\s+(\d+),\s*(\w+)', src)]

class BoulderRoom:
    def __init__(self, d, m, perms, cat, base=None):
        self.d, self.m, self.perms, self.cat = d, m, perms, cat
        self.base = base or m
        self.mm = mm = build_model(d, self.base, perms)
        self.W, self.H, self.Wb, self.Hb = mm.w, mm.h, m.width, m.height
        self.pairs = stonetable(m)                     # (boulder object index, hole warp index)
        if not self.pairs: raise BoulderRoomError('no stonetable')
        hw = {w for _, w in self.pairs}
        # each hole block moves as a unit: its block id and where the hole sits in it
        # hole blocks as in vanilla (the base map may already have moved them)
        vmm = mm if self.base is m else build_model(d, m, perms)
        self.hole_blk = {w: m.blocks[(m.warps[w].y // 2) * self.Wb + m.warps[w].x // 2] for w in hw}
        self.hole_q = {w: (m.warps[w].x & 1) + 2 * (m.warps[w].y & 1) for w in hw}
        self.hole_pat = {w: tuple(self.cell_kind(vmm.idx((m.warps[w].x // 2) * 2 + (q & 1), (m.warps[w].y // 2) * 2 + (q >> 1)), vmm)
                                  for q in range(4)) for w in hw}
        def cells(bx, by): return [mm.idx(bx*2 + (q & 1), by*2 + (q >> 1)) for q in range(4)]
        icy = lambda bx, by: any(mm.coll[c] == COLL_ICE for c in cells(bx, by))
        hb = {(m.warps[w].x // 2, m.warps[w].y // 2) for w in hw}
        room, q = set(hb), deque(hb)
        while q:
            bx, by = q.popleft()
            for dx, dy in D4:
                n = (bx + dx, by + dy)
                if 0 <= n[0] < self.Wb and 0 <= n[1] < self.Hb and n not in room and not icy(*n):
                    room.add(n); q.append(n)
        pts = list(hb) + [(m.objects[i].x // 2, m.objects[i].y // 2) for i, _ in self.pairs]
        x0, x1 = min(p[0] for p in pts) - 1, max(p[0] for p in pts) + 1
        y0, y1 = min(p[1] for p in pts) - 1, max(p[1] for p in pts) + 1
        room = {b for b in room if x0 <= b[0] <= x1 and y0 <= b[1] <= y1}
        fixed = set()
        boulder_idx = {i for i, _ in self.pairs}
        # boulders with no hole of their own (Blackthorn Gym 2F has three): ScratchRoom moves them
        # too, so their blocks are not fixed there
        self.extras = [i for i, o in enumerate(m.objects) if 'BOULDER' in o.sprite and i not in boulder_idx
                       and (o.x // 2, o.y // 2) in room]
        boulder_idx |= set(self.extras)
        events = [(w.x, w.y) for k, w in enumerate(m.warps) if k not in hw] + [(b.x, b.y) for b in m.bg_events] + \
                 [(o.x, o.y) for i, o in enumerate(m.objects) if i not in boulder_idx] + [(e.x, e.y) for e in m.coord_events]
        for (x, y) in events: fixed.add((x // 2, y // 2))
        for (bx, by) in room:
            # the map's edge stays as it is where it connects to another map; an indoor room (no
            # connections) keeps only its top row (the wall drawn along the top)
            if m.connections and (bx in (0, self.Wb - 1) or by in (0, self.Hb - 1)): fixed.add((bx, by))
            elif not m.connections and by == 0: fixed.add((bx, by))
            if (bx, by) in hb: continue
            if any(self.cell_kind(c) not in ('.', '#') for c in cells(bx, by)): fixed.add((bx, by))
        self.room = room
        self.free = sorted(b for b in room if b not in fixed)      # blocks the design may change
        self.pats = [p for p in (tuple('#' if k >> q & 1 else '.' for q in range(4)) for k in range(16))
                     if any(self._plain(b, p) for b in cat.cands.get(p, []))]
        if len(self.free) < 6 or not self.pats: raise BoulderRoomError('no room to rebuild')
        self.vmatrix = requirement_matrix(mm)
        # vanilla design and its reachability signature
        def plainpat(b):
            p = tuple(self.cell_kind(c) for c in cells(*b))
            return p if set(p) <= {'.', '#'} else tuple('.' if k in ('.', 'W') else '#' for k in p)
        self.vdesign = ({b: plainpat(b) for b in self.free},
                        {w: (self.base.warps[w].x // 2, self.base.warps[w].y // 2) for w in hw},
                        {i: mm.idx(self.base.objects[i].x, self.base.objects[i].y) for i in [i for i, _ in self.pairs] + self.extras})
        self.vsig = self.signature(*self.vdesign)

    def cell_kind(self, c, mm=None):
        mm = mm or self.mm
        if c in mm.warp_cells: return 'W'
        k = classify(mm, mm.coll[c])
        if k == 'land': return '.' if mm.coll[c] == 0 else 'x'
        return '#' if k is None and mm.coll[c] in (0x07, 0x15, 0x14) else 'x'

    # ------------------------------------------------------------------ the design as tiles
    def layout(self, pats, holes):
        """(floor cells, {hole warp: cell}) for a design; None if hole blocks collide."""
        mm = self.mm
        hblocks = {}
        for w, b in holes.items():
            if b in hblocks.values() or b not in self.room: return None
            hblocks[w] = b
        floor = set(); hcell = {}
        for (bx, by) in self.room:
            owner = next((w for w, b in hblocks.items() if b == (bx, by)), None)
            for q in range(4):
                x, y = bx*2 + (q & 1), by*2 + (q >> 1); c = mm.idx(x, y)
                if owner is not None:
                    if q == self.hole_q[owner]: hcell[owner] = c; continue
                    kind = self.hole_pat[owner][q]
                elif (bx, by) in pats: kind = pats[(bx, by)][q]
                else: kind = self.cell_kind(c)
                if kind == '.': floor.add(c)
        # plain floor outside the room is walkable too (fixed): the way around may lead out and back
        if not hasattr(self, '_outfloor'):
            roomcells = {mm.idx(bx*2 + (q & 1), by*2 + (q >> 1)) for (bx, by) in self.room for q in range(4)}
            xs = [b[0] for b in self.room]; ys = [b[1] for b in self.room]
            near = lambda c: min(xs) - 2 <= (c % mm.w) // 2 <= max(xs) + 2 and min(ys) - 2 <= (c // mm.w) // 2 <= max(ys) + 2
            self._outfloor = {c for c in range(mm.w * mm.h)
                              if c not in roomcells and near(c) and self.cell_kind(c) == '.'}
        floor |= self._outfloor
        return floor - self.people(), hcell

    def people(self):
        """tiles of people standing in the room (trainers): walls for boulders and the player."""
        if not hasattr(self, '_people'):
            from .blocking import people_cells
            self._people = {self.mm.idx(x, y) for x, y in people_cells(self.base) if self.mm.inb(x, y)}
        return self._people

    def all_floor(self, floor):
        """floor plus every plain floor tile of the whole map: where a boulder can really go."""
        mm = self.mm
        if not hasattr(self, '_everyfloor'):
            roomcells = {mm.idx(bx*2 + (q & 1), by*2 + (q >> 1)) for (bx, by) in self.room for q in range(4)}
            self._everyfloor = {c for c in range(mm.w * mm.h) if c not in roomcells and self.cell_kind(c) == '.'}
        return (floor | self._everyfloor) - self.people()

    def _nbrs(self, c):
        x, y = c % self.W, c // self.W
        if x > 0: yield c - 1
        if x < self.W - 1: yield c + 1
        if y > 0: yield c - self.W
        if y < self.H - 1: yield c + self.W

    def _label(self, floor, blocked):
        lab = {}; k = 0
        for c0 in floor:
            if c0 in lab or c0 in blocked: continue
            k += 1; lab[c0] = k; st = [c0]
            while st:
                c = st.pop()
                for n in self._nbrs(c):
                    if n in floor and n not in blocked and n not in lab: lab[n] = k; st.append(n)
        return lab

    def entry_cells(self, floor):
        """room floor cells that touch the rest of the map (the way in), from outside the room."""
        mm = self.mm
        if not hasattr(self, '_outside'):
            roomcells = {mm.idx(bx*2 + (q & 1), by*2 + (q >> 1)) for (bx, by) in self.room for q in range(4)}
            s = Searcher(mm); out = set()
            for e in mm.anchors:
                if e.is_entry and e.cells[0] not in roomcells:
                    out |= set(s.search(entry_starts(mm, e)))
            self._outside = out - roomcells
            hw = {self.mm.idx(self.m.warps[w].x, self.m.warps[w].y) for _, w in self.pairs}
            self._doors = {c for a in mm.anchors if a.is_entry for c in a.cells if c in roomcells and c not in hw}
        return {c for c in floor if c in self._outside or any(n in self._outside or n in self._doors for n in self._nbrs(c))}

    def signature(self, pats, holes, boulders):
        """how holes, boulders and ways in are grouped into areas: once with boulders as walls
        (what is reachable without pushing anything), once with boulders moved out of the way."""
        lay = self.layout(pats, holes)
        if lay is None: return None
        floor, hcell = lay
        bc = set(boulders.values())
        ent = self.entry_cells(floor)
        def groups(blocked):
            lab = self._label(floor, blocked)
            g = {}
            for w, c in hcell.items():
                for n in self._nbrs(c):
                    if n in lab: g.setdefault(lab[n], set()).add(('hole', w))
            for i, c in boulders.items():
                for n in self._nbrs(c):
                    if n in lab: g.setdefault(lab[n], set()).add(('boulder', i))
            for c in ent:
                if c in lab: g.setdefault(lab[c], set()).add(('in', c))
            # people (trainers) are reached from the floor beside them
            for c in self.people():
                for n in self._nbrs(c):
                    if n in lab: g.setdefault(lab[n], set()).add(('person', c))
            return frozenset(frozenset(v) for v in g.values())
        return groups(bc), groups(set())

    # ------------------------------------------------------------------ pushing
    def _push(self, floor, box, starts, obstacles, own, holes, boxonly=frozenset()):
        """BFS over pushes of one boulder. Returns (pushes to its own hole or None, other holes it
        can reach, turns on the way). boxonly: tiles a boulder may be pushed onto but the player
        never stands on (staircases: land for a boulder, a warp for the player)."""
        labs = {}
        def lab(b):
            if b not in labs: labs[b] = self._label(floor, obstacles | {b})
            return labs[b]
        l0 = lab(box)
        init = {(box, l0[s]) for s in starts if s in l0}
        seen = set(init); q = deque((s, 0, None, 0) for s in init)
        wrong = set()
        while q:
            (b, pl), n, lastd, turns = q.popleft()
            L = lab(b)
            for dx, dy in D4:
                p = b - dx - dy * self.W
                if abs((p % self.W) - (b % self.W)) > 1 or L.get(p) != pl: continue
                t = b + dx + dy * self.W
                if abs((t % self.W) - (b % self.W)) > 1 or t in obstacles: continue
                nt = turns + (1 if lastd is not None and lastd != (dx, dy) else 0)
                if t in holes:
                    if t == own: return n + 1, wrong, nt
                    wrong.add(t); continue
                if t not in floor and t not in boxonly: continue
                st = (t, lab(t).get(b))
                if st[1] is None or st in seen: continue
                seen.add(st); q.append((st, n + 1, (dx, dy), nt))
        return None, wrong, 0

    def holes_reached(self, big, box, starts, hs):
        """every hole one boulder can be pushed into (the whole search: it does not stop at its own),
        over staircases too (a boulder can be pushed onto and across one)."""
        return self._push(big, box, starts | {box}, set(), -1, hs, boxonly=self.stair_cells(hs))[1]

    def stair_cells(self, hs):
        """warp tiles a boulder can stand on: every land warp that is not a hole (stairs, ladders)."""
        mm = self.mm
        return frozenset(c for c in mm.warp_cells if c not in hs and classify(mm, mm.coll[c]) == 'land')


    def evaluate(self, pats, holes, boulders, extra=None):
        """(ok, score). boulders: the boulders with a hole, and (BoulderRoom) those with none, which
        move with the design too; extra: ScratchRoom's spots for the ones with none."""
        sig = self.signature(pats, holes, boulders)
        if sig is None or sig != self.vsig: return False, -1
        floor, hcell = self.layout(pats, holes)
        if any(c not in floor for c in boulders.values()) or len(set(boulders.values())) < len(boulders): return False, -1
        hs = set(hcell.values())
        own_i = {i for i, _ in self.pairs}
        pb = {i: c for i, c in boulders.items() if i in own_i}
        ex = {i: c for i, c in boulders.items() if i not in own_i}
        if extra is not None: ex.update(extra)
        else:
            for i, o in enumerate(self.base.objects):
                if 'BOULDER' in o.sprite and i not in own_i and i not in ex and self.mm.inb(o.x, o.y):
                    ex[i] = self.mm.idx(o.x, o.y)
        ent = self.entry_cells(floor)
        # wrong holes: a boulder can be pushed anywhere there is plain floor, so check over all of it
        big = self.all_floor(floor)
        lab = self._label(big, set()); ks = {lab[c] for c in ent if c in lab}
        everywhere = {c for c, k in lab.items() if k in ks}
        for i, w in self.pairs:
            if self.holes_reached(big, pb[i], everywhere, hs) - {hcell[w]}: return False, -1
        # a boulder with no hole must not reach any: nothing happens when it sits on one, and it
        # blocks that hole until the player leaves the room
        for c in ex.values():
            if self.holes_reached(big, c, everywhere, hs): return False, -1
        # solving: the boulders with no hole are in the way where they stand; the joint search may
        # push them aside (they reach no hole, checked above)
        exc = set(ex.values())
        def starts(blocked):
            lab = self._label(floor, blocked); ks = {lab[c] for c in ent if c in lab}
            return {c for c, k in lab.items() if k in ks}
        stc = self.stair_cells(hs)        # boulders may be pushed over the stairs
        left = dict(pb); score = 0
        own = {i: hcell[w] for i, w in self.pairs}
        while left:
            # quick case: some boulder goes in with the others where they stand
            for i in sorted(left):
                obst = {c for j, c in left.items() if j != i} | exc
                n, _, turns = self._push(floor, left[i], starts(obst | {left[i]}), obst, own[i], hs, boxonly=stc)
                if n is not None:
                    score += n + 2 * turns; del left[i]; break
            else:
                # otherwise move several boulders together until one falls in
                r = self._joint(floor - exc, left, own, hs, ent, boxonly=stc)
                if r is None and ex: r = self._joint(floor, {**left, **ex}, own, hs, ent, limit=4000, boxonly=stc)
                if r is None: return False, -1
                i, pos, n, turns = r
                score += n + 2 * turns + 3          # moving one aside first: a harder puzzle
                # the rest go back to their starting spots when the player leaves and returns
                left = {j: pb[j] for j in left if j != i}
        return True, score

    def _joint(self, floor, left, own, hs, ent, limit=15000, boxonly=frozenset()):
        """BFS over pushes of all remaining boulders together until one falls into its own hole.
        Returns (that boulder, positions of the others when it falls, pushes, turns) or None."""
        ids = sorted(left)
        labs = {}
        adj = {c: [n for n in self._nbrs(c) if n in floor] for c in floor}
        def lab(pos):
            r = labs.get(pos)
            if r is None:
                blocked = set(pos); r = {}; k = 0
                for c0 in adj:
                    if c0 in r or c0 in blocked: continue
                    k += 1; r[c0] = k; st = [c0]
                    while st:
                        c = st.pop()
                        for n in adj[c]:
                            if n not in r and n not in blocked: r[n] = k; st.append(n)
                labs[pos] = r
            return r
        p0 = tuple(left[i] for i in ids)
        L0 = lab(p0); ks = {L0[c] for c in ent if c in L0}
        init = [(p0, k) for k in ks]
        seen = set(init); q = deque((s, 0, None, 0, 0) for s in init)
        while q and len(seen) < limit:
            (pos, pl), n, lastd, turns, mv = q.popleft()
            L = lab(pos); occ = set(pos)
            for bi, b in enumerate(pos):
                for dx, dy in D4:
                    p = b - dx - dy * self.W
                    if abs((p % self.W) - (b % self.W)) > 1 or L.get(p) != pl: continue
                    t = b + dx + dy * self.W
                    if abs((t % self.W) - (b % self.W)) > 1 or t in occ: continue
                    nt = turns + (1 if lastd is not None and lastd != (bi, dx, dy) else 0)
                    nm = mv + (1 if lastd is None or lastd[0] != bi else 0)   # a move: a run of one boulder's pushes
                    if t in hs:
                        if t == own.get(ids[bi]):
                            self.joint_moves = nm
                            return ids[bi], {ids[k]: c for k, c in enumerate(pos)}, n + 1, nt
                        continue
                    if t not in floor and t not in boxonly: continue
                    npos = pos[:bi] + (t,) + pos[bi + 1:]
                    st = (npos, lab(npos).get(b))
                    if st[1] is None or st in seen: continue
                    seen.add(st); q.append((st, n + 1, (bi, dx, dy), nt, nm))
        return None

    # ------------------------------------------------------------------ search

    def _plain(self, b, pat):
        """block b draws pattern pat with plain floor (no ice, ledges or warps) and solid walls."""
        coll = self.cat.coll[b]
        for q in range(4):
            c = coll[q]
            if pat[q] == '.' and c != 0x00: return False
            if pat[q] == '#' and (c not in (0x07, 0x15, 0x14) or (c & 0xF0) in (0x60, 0x70)): return False
        return True


def player_traps(W, floor, boxonly, holes, own, boulders, starts, exits, limit=200000):
    """every position the player can push the boulders into, from the ways in: (states, trapped,
    an example) where trapped counts positions from which no sequence of pushes leads back to a
    way out (a staircase or hole not covered by a boulder, or `exits`, floor that leads on). None
    if there are more than `limit` positions. floor: tiles the player walks on; boxonly: tiles only
    a boulder may stand on (stairs); own: {boulder: its hole} (it falls in and is gone). Areas are
    bitmasks (one bit per tile), grown by shifts."""
    ids = sorted(boulders, key=str)
    cells = set(floor) | set(boxonly) | set(holes) | set(exits) | set(boulders.values()) | set(starts)
    n = (max(cells) if cells else 0) + W + 1
    def mask(cs):
        m = 0
        for c in cs: m |= 1 << c
        return m
    F, BX, HO, EX = mask(floor), mask(boxonly), mask(holes), mask(exits)
    WAYS = BX | HO
    notL = notR = 0
    for c in range(n):
        if c % W != 0: notL |= 1 << c
        if c % W != W - 1: notR |= 1 << c
    def grow(r): return r | ((r << 1) & notL) | ((r >> 1) & notR) | (r << W) | (r >> W)
    def area(free, c):
        r = 1 << c
        while True:
            m = grow(r) & free
            if m == r: return r
            r = m
    def out(R, occ):
        return bool(R & EX) or bool(grow(R) & WAYS & ~occ)
    pos0 = tuple(boulders[i] for i in ids)
    occ0 = mask(pos0)
    keys, todo = {}, deque()
    for sc in starts:
        if (F >> sc) & 1 and not (occ0 >> sc) & 1:
            k = (pos0, area(F & ~occ0, sc))
            if k not in keys: keys[k] = []; todo.append(k)
    good = set()
    while todo:
        k = todo.popleft()
        pos, R = k
        occ = 0
        for c in pos:
            if c is not None: occ |= 1 << c
        if out(R, occ): good.add(k)
        for bi, c in enumerate(pos):
            if c is None: continue
            for dd in (1, -1, W, -W):
                if dd in (1, -1) and ((c + dd) // W != c // W or (c - dd) // W != c // W): continue
                p_, t = c - dd, c + dd
                if p_ < 0 or not (R >> p_) & 1: continue
                if not (F >> c) & 1:
                    # a boulder on the stairs: pushing it off steps the player onto them (a way out)
                    good.add(k); continue
                if t < 0 or (occ >> t) & 1: continue
                if (HO >> t) & 1:
                    if own.get(ids[bi]) != t: continue
                    npos = pos[:bi] + (None,) + pos[bi + 1:]
                elif (F >> t) & 1 or (BX >> t) & 1:
                    npos = pos[:bi] + (t,) + pos[bi + 1:]
                else: continue
                nocc = (occ & ~(1 << c)) | ((1 << t) if npos[bi] is not None else 0)
                k2 = (npos, area(F & ~nocc, c))
                if k2 not in keys:
                    if len(keys) >= limit: return None
                    keys[k2] = []; todo.append(k2)
                keys[k2].append(k)          # reverse edge
    ok = set(good); st = list(good)
    while st:
        k = st.pop()
        for k0 in keys[k]:
            if k0 not in ok: ok.add(k0); st.append(k0)
    bad = [k for k in keys if k not in ok]
    ex = None
    if bad:
        pos, R = bad[0]
        ex = (pos, (R & -R).bit_length() - 1)
    return len(keys), len(bad), ex


def bad_boulder_room(d, m, m2, perms, cat):
    """problems in a finished map's boulder room (empty if fine)."""
    br = BoulderRoom(d, m, perms, cat, base=m2)
    floor, hcell = br.layout({}, {w: (m2.warps[w].x // 2, m2.warps[w].y // 2) for _, w in br.pairs})
    hs = set(hcell.values())
    boulders = {i: br.mm.idx(m2.objects[i].x, m2.objects[i].y) for i, _ in br.pairs}
    probs = []
    ent = br.entry_cells(floor)
    big = br.all_floor(floor)
    lab = br._label(big, set()); ks = {lab[c] for c in ent if c in lab}
    every = {c for c, k in lab.items() if k in ks}
    for i, w in br.pairs:
        if br.holes_reached(big, boulders[i], every, hs) - {hcell[w]}:
            probs.append(f'{m.objects[i].const} can be pushed onto another hole')
    own = {i for i, _ in br.pairs}
    for i, o in enumerate(m2.objects):
        if 'BOULDER' in o.sprite and i not in own and br.mm.inb(o.x, o.y):
            if br.holes_reached(big, br.mm.idx(o.x, o.y), every, hs):
                probs.append(f'{m.objects[i].const} (no hole of its own) can be pushed onto a hole')
    ok, _ = br.evaluate({}, {w: (m2.warps[w].x // 2, m2.warps[w].y // 2) for _, w in br.pairs}, dict(br.vdesign[2]))
    if not ok and not probs: probs.append('not every boulder can be pushed into its own hole')
    # no push may shut the player in (no way out of a gym: no Fly, Dig or Escape Rope)
    allb = dict(br.vdesign[2])
    own_h = {i: hcell[w] for i, w in br.pairs}
    # ways on out of the room: its floor that touches the rest of the map (Ice Path B1F)
    outer = {c for c in ent if c in br._outside or any(n in br._outside for n in br._nbrs(c))}
    stc = br.stair_cells(hs)
    # all boulders together where the floor is small (Blackthorn 2F: about 80,000 positions)
    tr = player_traps(br.W, big, stc, hs, own_h, allb, ent, outer, limit=150000) if len(big) <= 200 else None
    if tr is None:
        # too many positions for all boulders together (Ice Path B1F: they roam the whole map's
        # floor): every boulder alone and every two together, the others gone
        import itertools
        for sub in [(i,) for i in allb] + list(itertools.combinations(sorted(allb), 2)):
            tr = player_traps(br.W, big, stc, hs, {i: own_h[i] for i in sub if i in own_h},
                              {i: allb[i] for i in sub}, ent, outer)
            if tr is None: probs.append(f'too many boulder positions to check for traps ({sub})'); break
            if tr[1]: break
    if tr is not None and tr[1]:
        pos, rep = tr[2]
        probs.append(f'pushing boulders can shut the player in ({tr[1]} of {tr[0]} positions; e.g. player at '
                     f'{(rep % br.W, rep // br.W)}, boulders at {[(c % br.W, c // br.W) if c is not None else None for c in pos]})')
    return probs


