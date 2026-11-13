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
import copy, math, random, re
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
    def generate(self, seed, iters=300, budget=None, scratch=False):
        """a fixed number of iterations, so a seed always gives the same room on any machine
        (`budget`, in seconds, is only for experiments). scratch: start from a random room (random
        walls, holes and boulder spots) instead of vanilla's, so nothing of vanilla's is kept."""
        import time
        t0 = time.time()
        rng = random.Random(f'{seed}-{self.m.name}-boulders')
        pats, holes, boulders = (dict(x) for x in self.vdesign)
        if scratch:
            pats = {b: ('.',) * 4 if rng.random() < 0.55 else rng.choice(self.pats) for b in self.free}
            spots = list(self.free); rng.shuffle(spots)
            holes = {}
            for w in sorted(holes_v := dict(self.vdesign[1])):
                b = next((b for b in spots if b not in holes.values()
                          and all(abs(b[0] - o[0]) + abs(b[1] - o[1]) >= 2 for o in holes.values())), None)
                if b is None: break
                holes[w] = b; pats.pop(b, None)
            if len(holes) != len(holes_v): holes = holes_v
            lay = self.layout(pats, holes)
            if lay is not None:
                fl = sorted(lay[0]); rng.shuffle(fl)
                if len(fl) >= len(boulders): boulders = {i: fl[k] for k, i in enumerate(sorted(boulders))}
        vp, vh, vb = self.vdesign
        def novelty(p, h, b):
            # being unlike vanilla counts as much as a harder puzzle: holes and boulders that moved,
            # blocks drawn differently
            return (12 * sum(1 for w in h if h[w] != vh.get(w)) + 4 * sum(1 for i in b if b[i] != vb.get(i))
                    + sum(1 for k in self.free if p.get(k, vp.get(k)) != vp.get(k)))
        ok, sc = self.evaluate(pats, holes, boulders)
        self.vscore = sc
        cj = sc + novelty(pats, holes, boulders) if ok else -50
        found = []
        T0, T1 = 6.0, 0.3
        rblocks = sorted(self.room)
        for it in range(iters):
            if budget and time.time() - t0 > budget: break
            T = T0 * (T1 / T0) ** (it / iters)
            np_, nh, nb = dict(pats), dict(holes), dict(boulders)
            r = rng.random()
            if r < 0.6:
                b = rng.choice(self.free); np_[b] = rng.choice(self.pats)
            elif r < 0.8:
                lay = self.layout(np_, nh)
                if lay is None: continue
                i = rng.choice(sorted(nb))
                c0 = nb[i]
                spots = sorted(c for c in lay[0] - set(nb.values())
                               if abs(c % self.W - c0 % self.W) + abs(c // self.W - c0 // self.W) <= 4)
                if not spots: continue
                nb[i] = rng.choice(spots)
            else:
                w = rng.choice(sorted(nh))
                near = [b for b in self.free if 0 < abs(b[0] - nh[w][0]) + abs(b[1] - nh[w][1]) <= 2]
                # mostly small steps (a hole walks away a block or two at a time), sometimes a jump
                b = rng.choice(near) if near and rng.random() < 0.7 else rng.choice(self.free)
                if b in nh.values(): continue
                ob = nh[w]
                if ob in self.free: np_[ob] = rng.choice(self.pats)      # the block it leaves becomes floor/wall
                else:
                    # a vanilla hole block the design may not otherwise change (on the map's edge):
                    # it keeps its shape, the hole tile turned to floor (or wall)
                    vp_ = self.vacated_pat(w)
                    if vp_ is None: continue
                    np_[ob] = vp_
                nh[w] = b
                np_.pop(b, None)
            ok, sc = self.evaluate(np_, nh, nb)
            j = sc + novelty(np_, nh, nb) if ok else -50
            if j >= cj or rng.random() < math.exp((j - cj) / T):
                pats, holes, boulders, cj = np_, nh, nb, j
                if ok: found.append((j, dict(np_), dict(nh), dict(nb)))
        found.sort(key=lambda t: -t[0])
        tried = set()
        for sc, p, h, b in found[:60]:
            key = (tuple(sorted(p.items())), tuple(sorted(h.items())), tuple(sorted(b.items())))
            if key in tried: continue
            tried.add(key)
            m2 = self.draw(p, h, b, rng)
            if m2 is None: continue
            mat = requirement_matrix(build_model(self.d, m2, self.perms))
            if mat == self.vmatrix:
                # the finished room's own check: wrong holes over the stairs, solvable, no traps
                if bad_boulder_room(self.d, self.m, m2, self.perms, self.cat): continue
                self.score = sc; self.chosen = (p, h, b)
                return m2
            if getattr(self, 'debug', False):
                vm = self.vmatrix
                print('  matrix', [(e, a, vm.get(e, {}).get(a), mat.get(e, {}).get(a)) for e in set(vm) | set(mat)
                                   for a in set(vm.get(e, {})) | set(mat.get(e, {})) if vm.get(e, {}).get(a) != mat.get(e, {}).get(a)][:4])
        raise BoulderRoomError(f'no boulder room design kept the requirements ({len(found)} solvable)')

    def vacated_pat(self, w):
        base = tuple('#' if k != '.' else '.' for k in self.hole_pat[w])
        q = self.hole_q[w]
        for k in ('.', '#'):
            p = base[:q] + (k,) + base[q + 1:]
            if p in self.pats: return p
        return None

    def _plain(self, b, pat):
        """block b draws pattern pat with plain floor (no ice, ledges or warps) and solid walls."""
        coll = self.cat.coll[b]
        for q in range(4):
            c = coll[q]
            if pat[q] == '.' and c != 0x00: return False
            if pat[q] == '#' and (c not in (0x07, 0x15, 0x14) or (c & 0xF0) in (0x60, 0x70)): return False
        return True

    def draw(self, pats, holes, boulders, rng):
        """the map with the design filled in: free blocks from the catalog, hole blocks copied."""
        m2 = copy.deepcopy(self.base)
        out = bytearray(m2.blocks)
        for w, (bx, by) in holes.items():
            out[by * self.Wb + bx] = self.hole_blk[w]
            q = self.hole_q[w]
            m2.warps[w].x, m2.warps[w].y = bx * 2 + (q & 1), by * 2 + (q >> 1)
        hb = set(holes.values())
        # free blocks, and vanilla hole blocks on the edge that a hole has left
        for (bx, by) in sorted(set(self.free) | set(pats), key=lambda b: (b[1], b[0])):
            if (bx, by) in hb: continue
            i = by * self.Wb + bx
            left = out[i - 1] if bx > 0 else None
            up = out[i - self.Wb] if by > 0 else None
            p = pats.get((bx, by)) or self.vdesign[0].get((bx, by))
            blk = self.cat.choose(p, left, up, rng, ok=lambda b, p=p: self._plain(b, p))
            if blk is None: return None
            out[i] = blk
        # vanilla hole spots that no hole uses any more become plain floor or wall as designed
        m2.blocks = bytes(out)
        for i, c in boulders.items():
            m2.objects[i].x, m2.objects[i].y = c % self.W, c // self.W
        for i, c in getattr(self, '_extra_pos', {}).items():
            m2.objects[i].x, m2.objects[i].y = c % self.W, c // self.W
        return m2

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


class ScratchRoom(BoulderRoom):
    """The boulder room built from scratch (solid rock first), backwards from the solution.

    The vanilla requirements say which holes and boulders need Strength to reach (in vanilla: the
    holes of boulders 3 and 4, and boulder 4). Those go in a sealed part of the room whose only
    doorway holds a 'gate' boulder: one reachable without Strength whose own hole is in the sealed
    part. Every boulder's route is planned backwards from its hole (pulling it away, with turns),
    carving the boulder's path and the cells the player pushes from; the gate boulder's route
    starts in the doorway. Then every carved piece is joined to the ways in, never joining the
    two parts except through the doorway. Many attempts are built; the checks of BoulderRoom
    (no wrong hole over all floor, solvable, the drawn map's requirement matrix) pick the best."""

    def generate(self, seed, attempts=6000, keep=40):
        rng = random.Random(f'{seed}-{self.m.name}-scratch')
        vm = self.vmatrix
        gated_h, gated_b = set(), set()
        for e, row in vm.items():
            for a, masks in row.items():
                if masks and all(mk != 0 for mk in masks):
                    if a.startswith('warp:') and int(a[5:]) in {w for _, w in self.pairs}: gated_h.add(int(a[5:]))
                    if a.startswith('obj:'):
                        i = next((i for i, o in enumerate(self.m.objects) if f'obj:{o.const}' == a), None)
                        if i in {i for i, _ in self.pairs}: gated_b.add(i)
        self.gated = (gated_h, gated_b)
        cands = []
        for t in range(attempts):
            d = self._build(rng, gated_h, gated_b)
            if d is None: continue
            pats, holes, boulders, extra = d
            ok, sc = self.evaluate_scratch(pats, holes, boulders, extra)
            if ok: cands.append((sc + rng.random() * 0.1, pats, holes, boulders, extra))
        cands.sort(key=lambda c: -c[0])
        self.attempts_ok = len(cands)
        for sc, p, h, b, x in cands[:keep]:
            self._extra_pos = x
            m2 = self.draw(p, h, b, rng)
            if m2 is None:
                if getattr(self, 'debug', False): print('  draw failed')
                continue
            mat = requirement_matrix(build_model(self.d, m2, self.perms))
            if mat == vm:
                self.score = round(sc); return m2
            if getattr(self, 'debug', False):
                self.debug_maps = getattr(self, 'debug_maps', []) + [m2]
                print('  matrix', [(e, a, vm.get(e, {}).get(a), mat.get(e, {}).get(a)) for e in set(vm) | set(mat)
                                   for a in set(vm.get(e, {})) | set(mat.get(e, {})) if vm.get(e, {}).get(a) != mat.get(e, {}).get(a)][:5])
        raise BoulderRoomError(f'no from-scratch room passed ({len(cands)} solvable designs)')

    def evaluate_scratch(self, pats, holes, boulders, extra=None):
        """like evaluate(), without vanilla's grouping of areas (the requirement matrix decides)."""
        lay = self.layout(pats, holes)
        if lay is None: return False, -1
        floor, hcell = lay
        if any(c not in floor for c in boulders.values()) or len(set(boulders.values())) < len(boulders): return False, -1
        sv = self.vsig
        self.vsig = self.signature(pats, holes, boulders)   # accept any grouping here
        try: return self.evaluate(pats, holes, boulders, extra)
        finally: self.vsig = sv

    # ------------------------------------------------------------------ construction
    def _build(self, rng, gated_h, gated_b):
        W = self.W
        free = set(self.free)
        own = dict(self.pairs)                       # boulder -> hole warp
        gate = [i for i, w in self.pairs if w in gated_h and i not in gated_b]
        # no boulder with a hole can be the gate (Blackthorn: boulder and hole are both behind
        # Strength): a boulder with no hole guards the doorway instead
        vm = self.vmatrix
        def reach0(i):
            a = f'obj:{self.m.objects[i].const}'
            return any(0 in row.get(a, ()) for row in vm.values())
        xgate = [i for i in self.extras if reach0(i)] if gated_h and not gate else []
        if gated_h and not gate and not xgate: return None
        # split the free blocks: the sealed part grows from a random block, the rest is open
        sealed = set()
        if gated_h:
            seed_b = rng.choice(sorted(free))
            want = max(4, len(free) * rng.uniform(0.3, 0.45))
            sealed, q = {seed_b}, [seed_b]
            while q and len(sealed) < want:
                b = q.pop(rng.randrange(len(q)))
                for dx, dy in D4:
                    n = (b[0] + dx, b[1] + dy)
                    if n in free and n not in sealed: sealed.add(n); q.append(n)
        opened = free - sealed
        # holes: in their part, apart from each other
        holes = {}
        for i, w in self.pairs:
            part = sorted(sealed if w in gated_h else opened)
            part = [b for b in part if b not in holes.values()
                    and all(abs(b[0] - o[0]) + abs(b[1] - o[1]) >= 2 for o in holes.values())]
            if not part: return None
            holes[w] = rng.choice(part)
        hcell = {w: (b[0] * 2 + (self.hole_q[w] & 1)) + (b[1] * 2 + (self.hole_q[w] >> 1)) * W for w, b in holes.items()}
        holecells = set(hcell.values())
        hblock = set(holes.values())
        self.tiles_all = {bx*2 + (q & 1) + (by*2 + (q >> 1)) * W for (bx, by) in free if (bx, by) not in hblock for q in range(4)}
        tiles = {}                                   # free tile -> 'A' / 'B' (open / sealed part)
        for (bx, by) in free:
            if (bx, by) in hblock: continue
            for q in range(4): tiles[bx*2 + (q & 1) + (by*2 + (q >> 1)) * W] = 'B' if (bx, by) in sealed else 'A'
        floor = set()
        # hole blocks' own floor cells belong to the hole's part
        for w, (bx, by) in holes.items():
            for q in range(4):
                c = bx*2 + (q & 1) + (by*2 + (q >> 1)) * W
                if c != hcell[w] and self.hole_pat[w][q] == '.': floor.add(c); tiles[c] = 'B' if w in gated_h else 'A'
        # floor that is not part of the design (people's spots, the ways in) is the open part's:
        # the sealed part never touches it
        ffix = self._fixed_floor(holes) if self.extras else set()
        def can_carve(c, part):
            if c not in tiles or tiles[c] != part or c in holecells: return False
            if part == 'B' and any(n in ffix for n in self._nbrs(c)): return False
            # never next to floor of the other part (the parts touch only at the doorway)
            for n in self._nbrs(c):
                if n in floor and tiles.get(n) not in (None, part): return False
            return True
        boulders = {}
        doorway = None
        used_routes = set()
        extra = {}
        nook_guard = set()
        if xgate:
            # doorway c (the boulder), pushed twice inward along d into a dead-end nook at c+2d;
            # the sealed part opens beside c+d. The player pushes from c-d in the open part.
            opts = []
            for c, t in tiles.items():
                if t != 'B' or c in holecells: continue
                for d in D4:
                    step = d[0] + d[1] * W
                    if not (self._ok_step(c, d, -1) and self._ok_step(c, d, 2)): continue
                    a, b1, b2 = c - step, c + step, c + 2 * step
                    if tiles.get(a) != 'A' or tiles.get(b1) != 'B' or tiles.get(b2) != 'B': continue
                    if a in holecells or b1 in holecells or b2 in holecells: continue
                    opts.append((rng.random(), c, step, a, b1, b2))
            if not opts: return None
            _, c, step, a, b1, b2 = min(opts)
            gi = rng.choice(xgate)
            tiles[c] = 'D'; doorway = c
            for x in (a, c, b1, b2): floor.add(x)
            used_routes |= {a, c, b1, b2}
            extra[gi] = c
            # the nook stays a dead end: nothing is carved beside it
            nook_guard = {n for n in self._nbrs(b2) if n != b1}
        order = sorted(self.pairs, key=lambda p: (p[0] not in gate, rng.random()))
        def used_routes_starts(): return set()
        for i, w in order:
            part = 'B' if w in gated_h else 'A'
            box = hcell[w]
            dirs = [d for d in D4 if self._ok_step(box, d, -1)]
            rng.shuffle(dirs)
            route = None
            for d0 in dirs:
                r = self._pull(rng, box, d0, part, can_carve, floor, set(boulders.values()) | holecells | used_routes_starts(),
                               gate=(i in gate), want=rng.randint(8, 16))
                if r: route = r; break
            if route is None: return None
            cells, start, door = route
            # a boulder must not start on another boulder's route (or block its pushes)
            if start in used_routes: return None
            used_routes |= set(cells) - {start}
            for c in cells: floor.add(c)
            if i in gate:
                doorway = start; tiles[start] = 'D'
            boulders[i] = start
        # join every piece of floor to the ways in (open part), and the sealed part to the doorway
        floor_all = floor | self._fixed_floor(holes)
        if any(n in floor for n in nook_guard): return None
        if not self._join(rng, floor, floor_all, tiles, holecells, set(boulders.values()) | nook_guard, doorway): return None
        # the other boulders with no hole: each in a one-tile nook off the floor of its part,
        # where nothing is behind it (it can never be pushed, and never needs to be)
        for i in self.extras:
            if i in extra: continue
            part = 'A' if reach0(i) else 'B'
            opts = []
            for f in sorted(floor):
                if tiles.get(f) != part: continue
                for n in self._nbrs(f):
                    if n in floor or n in holecells or tiles.get(n) != part or n in nook_guard or n in extra.values(): continue
                    if any(x in floor or x in floor_all for x in self._nbrs(n) if x != f): continue
                    opts.append((rng.random(), n))
            if not opts: return None
            n = min(opts)[1]; floor.add(n); extra[i] = n
            nook_guard |= set(self._nbrs(n)) - floor
        pats = {}
        for (bx, by) in free:
            if (bx, by) in hblock: continue
            p = tuple('.' if bx*2 + (q & 1) + (by*2 + (q >> 1)) * W in floor else '#' for q in range(4))
            if p not in self.pats:
                # a pattern the tileset cannot draw (diagonal): open one more cell if it stays in its part
                for q in range(4):
                    c = bx*2 + (q & 1) + (by*2 + (q >> 1)) * W
                    if p[q] == '#' and can_carve(c, tiles.get(c)):
                        p2 = p[:q] + ('.',) + p[q + 1:]
                        if p2 in self.pats: p = p2; floor.add(c); break
                if p not in self.pats: return None
            pats[(bx, by)] = p
        return pats, holes, boulders, extra

    def _ok_step(self, c, d, k):
        x, y = c % self.W, c // self.W
        return 0 <= x + d[0] * k < self.W and 0 <= y + d[1] * k < self.H

    def _pull(self, rng, hole, d, part, can_carve, floor, avoid, gate=False, want=8):
        """plan a boulder's route backwards from its hole: the last push enters the hole moving
        along d. Returns (cells to carve, boulder start, doorway) or None."""
        W = self.W
        cells = []
        box = hole - d[0] - d[1] * W                  # where the boulder is before its last push
        def tile_ok(c, p=part): return c in floor or can_carve(c, p)
        if not tile_ok(box) or box in avoid: return None
        player = box - d[0] - d[1] * W
        if not tile_ok(player) or player in avoid: return None
        cells += [box, player]
        pushes = 1; cur = d; steps_in_dir = 1
        while pushes < want:
            turn = steps_in_dir >= 2 and rng.random() < 0.45
            nd = rng.choice([(cur[1], cur[0]), (-cur[1], -cur[0])]) if turn else cur
            nbox = box - nd[0] - nd[1] * W
            nplayer = nbox - nd[0] - nd[1] * W
            extra = []
            if nd != cur:
                # after pushing along nd the player stands at nbox and must walk round the boulder
                # to box - cur for the next push: the corner between them must be open
                corner = box - nd[0] - nd[1] * W - cur[0] - cur[1] * W
                extra = [corner]
            if not (self._ok_step(box, nd, -2) and tile_ok(nbox) and tile_ok(nplayer) and all(tile_ok(c) for c in extra)) \
                    or nbox in avoid or nplayer in avoid or any(c in avoid for c in extra):
                if turn: continue
                break
            cells += [nbox, nplayer] + extra; box = nbox
            steps_in_dir = 1 if nd != cur else steps_in_dir + 1
            cur = nd; pushes += 1
        if pushes < 3: return None
        if gate:
            # the start is the doorway: one step further back, in the open part
            nbox = box - cur[0] - cur[1] * W; nplayer = nbox - cur[0] - cur[1] * W
            if not (self._ok_step(box, cur, -2) and nbox in self.tiles_all and nbox not in avoid and can_carve(nplayer, 'A')):
                return None
            cells += [nbox, nplayer]
            return cells, nbox, nbox
        return cells, box, None


    def _fixed_floor(self, holes):
        """floor that is not part of the design: fixed room blocks and the floor around the room."""
        lay = self.layout({b: ('#',) * 4 for b in self.free if b not in holes.values()}, holes)
        return lay[0] if lay else set()

    def _join(self, rng, floor, floor_all, tiles, holecells, boulders, doorway):
        """carve the cheapest tunnels joining every piece of floor to the ways in (or, in the
        sealed part, to the doorway), keeping the parts apart."""
        W = self.W
        ent = self.entry_cells(floor_all | floor)
        if not ent: return False
        for _ in range(12):
            allf = floor | floor_all
            lab = self._label(allf - holecells, set())
            reach = {lab[c] for c in ent if c in lab}
            pieces = {}
            for c in floor:
                if c in lab and lab[c] not in reach and c != doorway:
                    pieces.setdefault(lab[c], []).append(c)
            if not pieces:
                # the ways in must be joined to each other too (as in vanilla, without Strength)
                labb = self._label(allf - holecells, set(boulders))
                groups = {}
                for c in ent:
                    if c in labb: groups.setdefault(labb[c], set()).add(c)
                if len(groups) <= 1: return True
                gs = sorted(groups.values(), key=len)
                comp = {c for c, k in labb.items() if k == labb[next(iter(gs[0]))]}
                others = {c for c, k in labb.items() if k != labb[next(iter(gs[0]))] and any(k == labb[x] for g in gs[1:] for x in g)}
                path = self._tunnel(comp, others, tiles, 'A', floor, holecells, boulders)
                if path is None: return False
                floor |= path
                continue
            k, cs = sorted(pieces.items(), key=lambda kv: len(kv[1]))[0]
            part = tiles.get(cs[0])
            if part == 'B':
                # the sealed part joins only to the doorway's inner side
                targets = {n for n in self._nbrs(doorway) if tiles.get(n) == 'B'} if doorway is not None else set()
            else:
                targets = {c for c in allf if lab.get(c) in reach}
            path = self._tunnel(set(cs), targets, tiles, part, floor, holecells, boulders)
            if path is None: return False
            floor |= path
        return False

    def _tunnel(self, srcs, targets, tiles, part, floor, holecells, boulders):
        """0-1 BFS from srcs to targets through the part's tiles; carving a wall costs 1."""
        W = self.W
        dist = {c: 0 for c in srcs}; prev = {}
        dq = deque(srcs)
        while dq:
            c = dq.popleft()
            if c in targets:
                path = set()
                while c in prev: path.add(c); c = prev[c]
                return {p for p in path if p not in floor}
            for n in self._nbrs(c):
                if n in holecells or n in boulders: continue
                if n in floor or n in targets: w = 0
                elif tiles.get(n) == part and not any(tiles.get(x) not in (None, part, 'D') and x in floor for x in self._nbrs(n)): w = 1
                else: continue
                nd = dist[c] + w
                if n not in dist or nd < dist[n]:
                    dist[n] = nd; prev[n] = c
                    (dq.appendleft if w == 0 else dq.append)(n)
        return None
