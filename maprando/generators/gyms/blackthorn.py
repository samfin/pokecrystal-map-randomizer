"""Blackthorn Gym 2F built from scratch: every block below the top wall row is designed anew, and
everything in the room moves: the stairs and the holes (as their own blocks), the six boulders and
the two trainers (people are walls: they stand still and only see the tile in front of them).

The design is annealed over block shapes (any shape the tileset can draw with plain floor and
wall) and positions. Its energy counts what is still wrong, so the search is drawn towards valid
rooms step by step:
  - what each staircase reaches without pushing anything (the other stairs, the trainers, the
    holes and boulders vanilla reaches without Strength) and what it must not (the hole and boulder
    vanilla keeps behind a Strength boulder, which must be reachable once boulders move);
  - each boulder with a hole reaches no other hole, over every push on all the floor; boulders with
    no hole reach none (one sitting on a hole does nothing and blocks it);
  - every boulder can be pushed into its hole (others in the way; the joint search may move them).
Valid designs are scored by the length of the solution (pushes, turns) and how much of the room is
walled into corridors; the best are drawn and kept only if the drawn map's requirement matrix is
vanilla's and the boulder-room check passes."""
import copy, math, random, time
from maprando.checks.pushes import K
from collections import deque
from maprando.model.movement import build_model
from maprando.model.reach import requirement_matrix
from maprando.model.catalog import Catalog
from maprando.checks.boulder_room import stonetable, BoulderRoom, bad_boulder_room

D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
PLAIN_WALL = (0x07, 0x15, 0x14)


class GymBoulderError(Exception):
    """no boulder room design passed the checks."""


class BoulderGymScratch:
    # classic: the search's scoring (solution length and walls); energy() counts exact submoves
    # only when moves_of() asks for them. On HARD_MAPS the valid designs are then ranked by
    # submoves and their boulders moved for more (harder)
    """Boulder rooms designed from scratch (Blackthorn Gym 2F, and Ice Path B1F's room with free/room
    given): wall patterns, hole blocks and boulder spots annealed together; the chamber builder
    (generators/chambers.py) reuses its drawing and checks."""
    classic = True
    HARD_MAPS = {'BlackthornGym2F'}     # (not Ice Path B1F's fallback)
    drop_k = K           # the exact count's weighting (pushes.K: moves first; 0: submoves only)
    hard_designs = 3     # distinct earlier designs whose boulders are moved
    hard_steps = 300     # boulder steps per design
    hard_time = 8.0      # seconds for all of it
    W_MOVES = 15.0
    W_SUB = 1.0
    # the push searches only need W, _nbrs and _label
    _push = BoulderRoom._push
    _joint = BoulderRoom._joint
    holes_reached = BoulderRoom.holes_reached

    def __init__(self, d, m, perms, free=None, room=None, move_stairs=True, base=None):
        """free: the blocks the design may change (default: all below the top wall row); room: the
        blocks of the boulder room (stairs and people outside it are left alone); move_stairs:
        whether the room's stairs move (Blackthorn) or keep their blocks (Ice Path's ladders)."""
        self.d, self.m, self.perms = d, m, perms
        self.mm = mm = build_model(d, m, perms)
        # base: the map as earlier steps left it (Ice Path B1F: its ice generated first); what the
        # design does not change is taken from it, the requirements from vanilla
        self.base = base or m
        mb = build_model(d, self.base, perms) if base is not None else mm
        self.W, self.H, self.Wb, self.Hb = mm.w, mm.h, m.width, m.height
        same = [x for x in d.maps.values() if x.tileset == m.tileset]
        self.own_cat = Catalog(d, m.tileset, perms, [m])
        self.cat = Catalog(d, m.tileset, perms, same)
        # Strength boulders are not compared (nothing is there; every way past one is an anchor)
        self.vmatrix = requirement_matrix(mm, boulders=False)
        self.pairs = stonetable(m)                               # (boulder object, hole warp)
        self.hole_of = dict(self.pairs)
        self.holes = [w for _, w in self.pairs]
        room = set(room) if room is not None else {(bx, by) for by in range(m.height) for bx in range(m.width)}
        self.room = room
        inroom = lambda x, y: (x // 2, y // 2) in room
        self.stairs = [i for i, w in enumerate(m.warps) if i not in self.holes and inroom(w.x, w.y)]
        self.moving = list(self.holes) + (list(self.stairs) if move_stairs else [])
        self.boulders = [i for i, o in enumerate(m.objects) if 'BOULDER' in o.sprite]
        self.extras = [i for i in self.boulders if i not in self.hole_of]
        self.people = [i for i, o in enumerate(m.objects) if i not in self.boulders and inroom(o.x, o.y)]
        # stairs and holes move as their own vanilla block (warp tile and the cells around it)
        self.unit_blk, self.unit_q, self.unit_pat = {}, {}, {}
        for w, wp in enumerate(m.warps):
            bx, by = wp.x // 2, wp.y // 2
            self.unit_blk[w] = m.blocks[by * self.Wb + bx]
            self.unit_q[w] = (wp.x & 1) + 2 * (wp.y & 1)
            pat = []
            for q in range(4):
                c = mm.idx(bx * 2 + (q & 1), by * 2 + (q >> 1))
                pat.append('W' if q == self.unit_q[w] else self._kind(c))
            self.unit_pat[w] = tuple(pat)
        # every plain floor/wall shape the tileset can draw
        def plain(cat, b, pat):
            coll = cat.coll[b]
            for q in range(4):
                if pat[q] == '.' and coll[q] != 0x00: return False
                if pat[q] == '#' and (coll[q] not in PLAIN_WALL or (coll[q] & 0xF0) in (0x60, 0x70)): return False
            return True
        self.plain = plain
        # only blocks that look like the room: drawn with the tiles its own blocks use (other maps
        # with this tileset bring plants, carpets and lava)
        meta = d.tileset_meta[m.tileset]
        tl = lambda b: set(meta[b * 16:b * 16 + 16])
        own_tiles = set().union(*[tl(b) for b in set(m.blocks)])
        room_blocks = {m.blocks[by * m.width + bx] for (bx, by) in room}
        self.allowed = {b for b in range(len(meta) // 16) if tl(b) <= own_tiles and b in room_blocks}
        self.pats = [p for p in (tuple('#' if k >> q & 1 else '.' for q in range(4)) for k in range(16))
                     if any(plain(self.cat, b, p) and b in self.allowed for b in self.cat.cands.get(p, []))]
        if ('.',) * 4 not in self.pats:
            raise GymBoulderError('the room has no plain floor block')
        # the top block row is the wall drawn along the top: kept (by default)
        self.free = sorted(free) if free is not None else [(bx, by) for by in range(1, self.Hb) for bx in range(self.Wb)]
        fs = set(self.free)
        # everything else keeps its vanilla tiles; stairs that stay keep their spot
        self.top = {c: self._kind(c, mb) for c in range(mm.w * mm.h) if ((c % mm.w) // 2, (c // mm.w) // 2) not in fs}
        bw_ = self.base.warps
        self.fixed_wcell = {w: mm.idx(bw_[w].x, bw_[w].y) for w in self.stairs if w not in self.moving}
        if any(((bw_[w].x // 2, bw_[w].y // 2) in fs) for w in self.fixed_wcell):
            raise GymBoulderError('a staircase that stays is on a block the design changes')
        # boulders and people go only on the room's free blocks
        self.obj_area = {mm.idx(bx * 2 + (q & 1), by * 2 + (q >> 1)) for (bx, by) in self.free for q in range(4)}
        # what vanilla asks: from each staircase, anchors reached with nothing (need0) and anchors
        # reached only with Strength (needS: not without pushing, but once boulders move)
        self.need0, self.needS = {}, {}
        for e in self.stairs:
            row = self.vmatrix.get(f'warp:{e}', {})
            n0, ns = [], []
            for a, masks in row.items():
                if not masks: continue
                (n0 if 0 in masks else ns).append(a)
            self.need0[e], self.needS[e] = n0, ns
        consts = {f'obj:{o.const}': i for i, o in enumerate(m.objects)}
        self.anchor = lambda a: ('warp', int(a[5:])) if a.startswith('warp:') else ('obj', consts[a])

    def _kind(self, c, mm=None):
        mm = mm or self.mm
        if c in mm.warp_cells: return 'W'
        k = mm.coll[c]
        if k == 0x00: return '.'
        return '#'

    # ------------------------------------------------------------------ geometry
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

    def grid(self, s):
        """{cell: '.', '#', 'W'} and {warp: cell} for a state."""
        W = self.W
        kind = dict(self.top)
        unit_at = {b: w for w, b in s['units'].items()}
        wcell = dict(self.fixed_wcell)
        for (bx, by) in self.free:
            w = unit_at.get((bx, by))
            pat = self.unit_pat[w] if w is not None else s['pats'][(bx, by)]
            for q in range(4):
                c = (by * 2 + (q >> 1)) * W + bx * 2 + (q & 1)
                kind[c] = pat[q]
                if w is not None and q == self.unit_q[w]: wcell[w] = c
        return kind, wcell

    def flood(self, starts, walk):
        seen = set(c for c in starts if c in walk); st = list(seen)
        while st:
            c = st.pop()
            for n in self._nbrs(c):
                if n in walk and n not in seen: seen.add(n); st.append(n)
        return seen

    def touched(self, cell, S):
        return any(n in S for n in self._nbrs(cell))

    def _fastlab(self, adj, blocked):
        lab = {}; k = 0
        for c0 in adj:
            if c0 in lab or c0 in blocked: continue
            k += 1; lab[c0] = k; st = [c0]
            while st:
                c = st.pop()
                for n in adj[c]:
                    if n not in lab and n not in blocked: lab[n] = k; st.append(n)
        return lab

    def push_search(self, walk, box, starts, obstacles, own, holes, limit=4000, adj=None, cache=None, boxonly=frozenset()):
        """pushes of one boulder: (holes it can fall into, closest it gets to `own` (0: in),
        pushes to fall into own or None). Areas are bitmasks (one bit per tile)."""
        W = self.W
        if cache is None: cache = {}
        bits = cache.get('_bits')
        if bits is None:
            n = self.W * self.H + W + 1
            notL = notR = 0
            for c in range(n):
                if c % W != 0: notL |= 1 << c
                if c % W != W - 1: notR |= 1 << c
            F = 0
            for c in walk: F |= 1 << c
            bits = cache['_bits'] = (F, notL, notR)
        F, notL, notR = bits
        okey = frozenset(obstacles)
        ent = cache.get(okey)
        if ent is None:
            free0 = F
            for c in obstacles: free0 &= ~(1 << c)
            ent = cache[okey] = (free0, {})
        free0, comps = ent
        def comp(b, c):
            cs = comps.get(b)
            if cs is None: cs = comps[b] = []
            bit = 1 << c
            for m in cs:
                if m & bit: return m
            free = free0 & ~(1 << b)
            if not free & bit: return 0
            r = bit
            while True:
                m = (r | ((r << 1) & notL) | ((r >> 1) & notR) | (r << W) | (r >> W)) & free
                if m == r: break
                r = m
            cs.append(r)
            return r
        init = set()
        for c in starts:
            if c != box and (free0 >> c) & 1:
                R = comp(box, c)
                if R: init.add((box, R))
        seen = set(init); q = deque((st, 0) for st in init)
        got = set(); ox, oy = (own % W, own // W) if own is not None else (0, 0)
        best = abs(box % W - ox) + abs(box // W - oy) if own is not None else 0
        first = None
        while q and len(seen) < limit:
            (b, R), n = q.popleft()
            for dd in (1, -1, W, -W):
                if dd in (1, -1) and ((b + dd) // W != b // W or (b - dd) // W != b // W): continue
                p, t = b - dd, b + dd
                if p < 0 or not (R >> p) & 1: continue
                if t < 0 or t in obstacles: continue
                if t in holes:
                    got.add(t)
                    if t == own and first is None: first = n + 1
                    continue
                if not (free0 >> t) & 1 and t not in boxonly: continue   # a boulder may cross the stairs
                if own is not None: best = min(best, abs(t % W - ox) + abs(t // W - oy))
                if not (free0 >> b) & 1: continue        # the player would step onto the stairs
                st = (t, comp(t, b))
                if not st[1] or st in seen: continue
                seen.add(st); q.append((st, n + 1))
        return got, (0 if first is not None else best), first

    # ------------------------------------------------------------------ energy
    def energy(self, s, solve=True):
        """(what is still wrong, puzzle score). 0 wrong: a valid design."""
        kind, wcell = self.grid(s)
        floor = {c for c, k in kind.items() if k == '.'}
        objs = s['objs']
        occ = list(objs.values())
        e = 0
        # objects stand on floor, apart, not on or beside the stairs
        stair_near = {wcell[w] for w in self.stairs} | {n for w in self.stairs for n in self._nbrs(wcell[w])}
        e += 20 * sum(1 for c in occ if c not in floor or c in stair_near)
        e += 20 * (len(occ) - len(set(occ)))
        people = {objs[i] for i in self.people}
        bcells = {objs[i] for i in self.boulders}
        walk0 = floor - people - bcells
        walkS = floor - people
        hs = {wcell[w] for w in self.holes}
        for st in self.stairs:
            start = [n for n in self._nbrs(wcell[st]) if n in walk0]
            S0 = self.flood(start, walk0)
            SS = self.flood([n for n in self._nbrs(wcell[st]) if n in walkS], walkS)
            for a in self.need0[st]:
                t, v = self.anchor(a)
                c = wcell[v] if t == 'warp' else objs[v]
                if not self.touched(c, S0): e += 10
            for a in self.needS[st]:
                t, v = self.anchor(a)
                c = wcell[v] if t == 'warp' else objs[v]
                if self.touched(c, S0): e += 10
                if not self.touched(c, SS): e += 10
        if not solve: return e, None
        e *= 3          # reachability first; the puzzle's own counts below are always added
        # wrong holes, over every push on all the floor the player can walk to
        ent = {n for st in self.stairs for n in self._nbrs(wcell[st]) if n in walkS}
        every = self.flood(ent, walkS)
        own = {i: wcell[w] for i, w in self.pairs}
        exc = {objs[i] for i in self.extras}
        if any(c not in walkS for c in occ if c not in people): return e + 60, None
        adj = {c: tuple(n for n in self._nbrs(c) if n in walkS) for c in walkS}
        cache = {}
        stc = frozenset(wcell[w] for w in self.stairs)     # boulders may be pushed over the stairs
        for i in self.boulders:
            got, _, _ = self.push_search(walkS, objs[i], every | {objs[i]}, set(), None, hs, adj=adj, cache=cache, boxonly=stc)
            e += 5 * len(got - {own.get(i)})
        # each boulder with a hole, alone (the other holed boulders where they stand; those with
        # no hole may be pushed aside, the gate in front of a Strength part is one): how close it gets
        for i, c in own.items():
            obst = {objs[j] for j in own if j != i}
            lab = self._fastlab(adj, obst | {objs[i]}); ks = {lab[x] for x in ent if x in lab}
            st_ = {x for x, k in lab.items() if k in ks}
            _, dist, _ = self.push_search(walkS, objs[i], st_, obst, c, hs, adj=adj, cache=cache, boxonly=stc)
            if dist: e += 1 + min(dist, 8)
        if e: return e, None
        # every boulder into its own hole
        pb = {i: objs[i] for i in own}
        def starts(blocked):
            lab = self._label(walkS, blocked); ks = {lab[c] for c in ent if c in lab}
            return {c for c, k in lab.items() if k in ks}
        left = dict(pb); score = 0; moves = 0
        while left:
            for i in sorted(left):
                obst = {c for j, c in left.items() if j != i} | exc
                n, _, turns = self._push(walkS, left[i], starts(obst | {left[i]}), obst, own[i], hs, boxonly=stc)
                if n is not None:
                    score += n + 2 * turns; moves += 1; del left[i]; break
            else:
                r = self._joint(walkS - exc, left, own, hs, ent, limit=1200, boxonly=stc)
                if r is None: r = self._joint(walkS, {**left, **{i: objs[i] for i in self.extras}}, own, hs, ent, limit=800, boxonly=stc)
                if r is None: return 5 * len(left), None
                i, pos, n, turns = r
                score += n + 2 * turns + 3; moves += self.joint_moves
                left = {j: pb[j] for j in left if j != i}
        # rooms of corridors, not open halls: walls inside the room count a little
        walls = sum(1 for (bx, by) in self.free for q in range(4)
                    if s['pats'].get((bx, by), ('.',) * 4)[q] == '#' and (bx, by) not in s['units'].values())
        if self.classic: return 0, score + 0.15 * walls
        # boulder moves (runs of one boulder's pushes) count most: the fewest that drop every
        # boulder in one go (exact search), else the solver's own count
        from maprando.checks.pushes import drop_moves, K
        ex_moves = drop_moves(self.W, walkS, {i: objs[i] for i in self.boulders}, own, sorted(ent),
                              boxonly=stc, limit=20000, detail=True, weight=self.drop_k)
        subs = 0
        if ex_moves is not None:
            moves, subs = ex_moves
            if moves is None: moves = 0          # (submoves only, drop_k=0: moves are not counted)
        self.last_moves, self.last_subs = moves, subs
        # submoves (runs of pushes in one direction within a move) break ties
        return 0, self.W_MOVES * moves + self.W_SUB * subs + score + 0.15 * walls

    # ------------------------------------------------------------------ search
    def random_state(self, rng):
        pats = {b: (('.',) * 4 if rng.random() < 0.45 else rng.choice(self.pats)) for b in self.free}
        blocks = list(self.free); rng.shuffle(blocks)
        units = {w: blocks[k] for k, w in enumerate(sorted(self.moving))}
        s = {'pats': pats, 'units': units, 'objs': {}}
        kind, _ = self.grid(s)
        fl = sorted(c for c, k in kind.items() if k == '.' and c in self.obj_area); rng.shuffle(fl)
        s['objs'] = {i: fl[k] for k, i in enumerate(self.boulders + self.people)}
        return s

    def mutate(self, s, rng):
        """a neighbouring design of s (one block's pattern, a hole block or a boulder moved), or None
        if it cannot be drawn."""
        t = {'pats': dict(s['pats']), 'units': dict(s['units']), 'objs': dict(s['objs'])}
        r = rng.random()
        if r < 0.5:
            b = rng.choice(self.free)
            p = list(t['pats'][b])
            if rng.random() < 0.6:
                q = rng.randrange(4); p[q] = '#' if p[q] == '.' else '.'
                if tuple(p) not in self.pats: return None
                t['pats'][b] = tuple(p)
            else: t['pats'][b] = rng.choice(self.pats)
        elif r < 0.62:
            w = rng.choice(sorted(t['units']))
            cur = t['units'][w]
            near = [b for b in self.free if 0 < abs(b[0] - cur[0]) + abs(b[1] - cur[1]) <= 2]
            b = rng.choice(near) if near and rng.random() < 0.6 else rng.choice(self.free)
            other = next((v for v, bb in t['units'].items() if bb == b), None)
            if other is not None: t['units'][other] = cur          # two units swap places
            t['units'][w] = b
        else:
            i = rng.choice(self.boulders + self.people) if r < 0.9 or not self.people else rng.choice(self.people)
            kind, _ = self.grid(t)
            c0 = t['objs'][i]
            fl = [c for c, k in kind.items() if k == '.' and c in self.obj_area and c not in t['objs'].values()]
            near = [c for c in fl if abs(c % self.W - c0 % self.W) + abs(c // self.W - c0 // self.W) <= 3]
            pool = near if near and rng.random() < 0.7 else fl
            if not pool: return None
            t['objs'][i] = rng.choice(pool)
        return t

    def anneal(self, rng, iters, enough=25):
        """search until `enough` distinct valid designs appear (or `iters` steps); the best first."""
        seen_ = set()
        s = self.random_state(rng)
        e, sc = self.energy(s)
        cur = e * 10 - (sc or 0)
        found = []
        T0, T1 = 30.0, 0.5
        for it in range(iters):
            T = T0 * (T1 / T0) ** (it / iters)
            t = self.mutate(s, rng)
            if t is None: continue
            e2, sc2 = self.energy(t)
            j = e2 * 10 - (sc2 or 0)
            if j <= cur or rng.random() < math.exp((cur - j) / T):
                s, cur = t, j
                if e2 == 0:
                    key = (tuple(sorted(t['pats'].items())), tuple(sorted(t['units'].items())), tuple(sorted(t['objs'].items())))
                    if key not in seen_: seen_.add(key); found.append((sc2, t))
                    if len(found) >= enough: break
        found.sort(key=lambda f: -f[0])
        return found[:enough]

    def moves_of(self, s, with_score=False):
        """(exact boulder moves, submoves) of a design, or None if it is not valid; with_score: and
        the earlier (classic) score"""
        self.classic = False; self.drop_k = 0      # the fewest submoves, whatever the moves
        try:
            self.last_moves = self.last_subs = 0
            e, sc = self.energy(s)
        finally: self.classic = True; self.drop_k = K
        if e: return None
        mv = (self.last_moves, self.last_subs)
        if not with_score: return mv
        return mv, sc - self.W_MOVES * mv[0] - self.W_SUB * mv[1]

    def harder(self, s, sc, rng, steps):
        """a valid design's boulders moved one at a time (nothing else changes) towards more submoves; every design passed on the way, as ((moves, submoves, score), score, s)"""
        cur = self.moves_of(s)
        if cur is None: return []
        out = []
        for it in range(steps):
            if time.time() > self.hard_until: break
            i = rng.choice(self.boulders)
            kind, _ = self.grid(s)
            c0 = s['objs'][i]
            fl = [c for c in kind if kind[c] == '.' and c in self.obj_area and c not in s['objs'].values()]
            near = [c for c in fl if abs(c % self.W - c0 % self.W) + abs(c // self.W - c0 // self.W) <= 3]
            pool = near if near and rng.random() < 0.7 else fl
            if not pool: continue
            t = {'pats': s['pats'], 'units': s['units'], 'objs': dict(s['objs'])}
            t['objs'][i] = rng.choice(pool)
            r = self.moves_of(t, with_score=True)      # the earlier checks, its score, the moves
            if r is None: continue
            mv, sc2 = r
            if mv[1] < cur[1]: continue
            s, cur, sc = t, mv, sc2
            out.append(((mv[1], sc2), sc2, t))
        return out

    def draw(self, s, rng):
        """design s drawn as a map: hole blocks and warps moved, every free block picked to match its
        pattern and its neighbours."""
        m2 = copy.deepcopy(self.base)
        out = bytearray(m2.blocks)
        unit_at = {b: w for w, b in s['units'].items()}
        for w, (bx, by) in s['units'].items():
            out[by * self.Wb + bx] = self.unit_blk[w]
            q = self.unit_q[w]
            m2.warps[w].x, m2.warps[w].y = bx * 2 + (q & 1), by * 2 + (q >> 1)
        for (bx, by) in self.free:          # rows top to bottom, so left and up are drawn first
            if (bx, by) in unit_at: continue
            i = by * self.Wb + bx
            left = out[i - 1] if bx > 0 else None
            up = out[i - self.Wb] if by > 0 else None
            p = s['pats'][(bx, by)]
            blk = None
            # the room's own blocks first, then any other map's with this tileset
            for cat in (self.own_cat, self.cat):
                if cat.has(p):
                    blk = cat.choose(p, left, up, rng, ok=lambda b, cat=cat, p=p: self.plain(cat, b, p) and b in self.allowed)
                    if blk is not None: break
            if blk is None: return None
            out[i] = blk
        m2.blocks = bytes(out)
        for i, c in s['objs'].items():
            m2.objects[i].x, m2.objects[i].y = c % self.W, c // self.W
        return m2

    def check(self, m2):
        mm2 = build_model(self.d, m2, self.perms)
        if requirement_matrix(mm2, boulders=False) != self.vmatrix: return 'requirements differ'
        bad = bad_boulder_room(self.d, self.m, m2, self.perms, self.cat)
        if bad: return str(bad)
        from maprando.checks.trainers import unsafe_trainers
        if unsafe_trainers(mm2): return 'trainer trap'
        return None

    def generate(self, seed, chains=4, iters=8000, keep=40):
        """the new map for a seed: the best designs of a few annealing chains, drawn and checked in
        turn (on HARD_MAPS ranked by submoves and made harder). Raises GymBoulderError if
        none passes."""
        rng = random.Random(f'{seed}-{self.m.name}-scratch-gym')
        found = []
        for k in range(chains):
            found += self.anneal(random.Random(f'{seed}-{self.m.name}-{k}'), iters)
        found.sort(key=lambda f: -f[0])
        if self.classic and self.m.name in self.HARD_MAPS:
            # the earlier designs, the hardest first: submoves (runs of one boulder's pushes in one
            # direction), then the earlier score
            ranked, keys = [], set()
            for sc, s in found:
                key = (tuple(sorted(s['pats'].items())), tuple(sorted(s['units'].items())), tuple(sorted(s['objs'].items())))
                if key in keys: continue
                keys.add(key)
                self.classic = False
                try:
                    self.last_moves = self.last_subs = 0
                    e, _ = self.energy(s)
                    mv = (self.last_moves, self.last_subs) if e == 0 else (0, 0)
                finally: self.classic = True
                ranked.append(((mv[1], sc), sc, s))
            ranked.sort(key=lambda t: t[0], reverse=True)
            # the boulders alone moved for more moves: walls, holes and ladders stay as designed
            rng_h = random.Random(f'{seed}-{self.m.name}-harder')
            done, extra = set(), []
            self.hard_until = time.time() + self.hard_time
            for _, sc, s0 in ranked:
                lay = (tuple(sorted(s0['pats'].items())), tuple(sorted(s0['units'].items())))
                if lay in done: continue
                done.add(lay)
                if len(done) > self.hard_designs: break
                extra += self.harder(s0, sc, rng_h, self.hard_steps)
            ranked += extra
            ranked.sort(key=lambda t: t[0], reverse=True)
            if getattr(self, 'debug', False):
                from collections import Counter
                print('  designs', len(ranked), Counter(t[0][:2] for t in ranked).most_common(10))
            found = [(sc, s) for _, sc, s in ranked]
        seen = set(); last = None
        self.found = len(found)
        for sc, s in found:
            key = (tuple(sorted(s['pats'].items())), tuple(sorted(s['units'].items())), tuple(sorted(s['objs'].items())))
            if key in seen: continue
            seen.add(key)
            if len(seen) > keep: break
            m2 = self.draw(s, rng)
            if m2 is None: last = 'not drawable'; continue
            why = self.check(m2)
            if why is None:
                self.score = sc; self.state = s
                return m2
            last = why
        raise GymBoulderError(f'{self.m.name}: no from-scratch room passed ({len(found)} valid designs; last: {last})')
