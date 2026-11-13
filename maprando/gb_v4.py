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
import copy, math, random
from collections import deque
from .model import build_model
from .reach import requirement_matrix
from .catalog import Catalog
from .boulderroom import stonetable, BoulderRoom, bad_boulder_room
from .blocking import people_cells

D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
PLAIN_WALL = (0x07, 0x15, 0x14)


class GymBoulderError(Exception):
    pass


class BoulderGymScratch:
    # the push searches only need W, _nbrs and _label
    _push = BoulderRoom._push
    _joint = BoulderRoom._joint
    holes_reached = BoulderRoom.holes_reached

    def __init__(self, d, m, perms):
        self.d, self.m, self.perms = d, m, perms
        self.mm = mm = build_model(d, m, perms)
        self.W, self.H, self.Wb, self.Hb = mm.w, mm.h, m.width, m.height
        same = [x for x in d.maps.values() if x.tileset == m.tileset]
        self.own_cat = Catalog(d, m.tileset, perms, [m])
        self.cat = Catalog(d, m.tileset, perms, same)
        self.vmatrix = requirement_matrix(mm)
        self.pairs = stonetable(m)                               # (boulder object, hole warp)
        self.hole_of = dict(self.pairs)
        self.holes = [w for _, w in self.pairs]
        self.stairs = [i for i in range(len(m.warps)) if i not in self.holes]
        self.boulders = [i for i, o in enumerate(m.objects) if 'BOULDER' in o.sprite]
        self.extras = [i for i in self.boulders if i not in self.hole_of]
        self.people = [i for i, o in enumerate(m.objects) if i not in self.boulders]
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
        self.pats = [p for p in (tuple('#' if k >> q & 1 else '.' for q in range(4)) for k in range(16))
                     if any(plain(self.cat, b, p) for b in self.cat.cands.get(p, []))]
        if ('.',) * 4 not in self.pats or ('#',) * 4 not in self.pats:
            raise GymBoulderError('the tileset cannot draw plain floor and solid wall')
        # the top block row is the wall drawn along the top: kept
        self.free = [(bx, by) for by in range(1, self.Hb) for bx in range(self.Wb)]
        self.top = {}
        for bx in range(self.Wb):
            for q in range(4):
                c = mm.idx(bx * 2 + (q & 1), q >> 1)
                self.top[c] = self._kind(c)
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

    def _kind(self, c):
        mm = self.mm
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
        wcell = {}
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

    def push_search(self, walk, box, starts, obstacles, own, holes, limit=4000, adj=None, cache=None):
        """pushes of one boulder: (holes it can fall into, closest it gets to `own` (0: in),
        pushes to fall into own or None)."""
        W = self.W
        if adj is None: adj = {c: tuple(n for n in self._nbrs(c) if n in walk) for c in walk}
        okey = frozenset(obstacles)
        labs = cache.setdefault(okey, {}) if cache is not None else {}
        def lab(b):
            r = labs.get(b)
            if r is None: r = labs[b] = self._fastlab(adj, obstacles | {b})
            return r
        L0 = lab(box)
        init = {(box, L0[c]) for c in starts if c in L0}
        seen = set(init); q = deque((st, 0) for st in init)
        got = set(); ox, oy = (own % W, own // W) if own is not None else (0, 0)
        best = abs(box % W - ox) + abs(box // W - oy) if own is not None else 0
        first = None
        while q and len(seen) < limit:
            (b, pl), n = q.popleft()
            L = lab(b)
            for dx, dy in D4:
                p = b - dx - dy * W
                if abs(p % W - b % W) > 1 or L.get(p) != pl: continue
                t = b + dx + dy * W
                if abs(t % W - b % W) > 1 or t in obstacles: continue
                if t in holes:
                    got.add(t)
                    if t == own and first is None: first = n + 1
                    continue
                if t not in walk: continue
                if own is not None: best = min(best, abs(t % W - ox) + abs(t // W - oy))
                st = (t, lab(t).get(b))
                if st[1] is None or st in seen: continue
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
        for i in self.boulders:
            got, _, _ = self.push_search(walkS, objs[i], every | {objs[i]}, set(), None, hs, adj=adj, cache=cache)
            e += 5 * len(got - {own.get(i)})
        # each boulder with a hole, alone (the others where they stand): how close it gets
        for i, c in own.items():
            obst = {objs[j] for j in own if j != i} | exc
            lab = self._fastlab(adj, obst | {objs[i]}); ks = {lab[x] for x in ent if x in lab}
            st_ = {x for x, k in lab.items() if k in ks}
            _, dist, _ = self.push_search(walkS, objs[i], st_, obst, c, hs, adj=adj, cache=cache)
            if dist: e += 1 + min(dist, 8)
        if e: return e, None
        # every boulder into its own hole
        pb = {i: objs[i] for i in own}
        def starts(blocked):
            lab = self._label(walkS, blocked); ks = {lab[c] for c in ent if c in lab}
            return {c for c, k in lab.items() if k in ks}
        left = dict(pb); score = 0
        while left:
            for i in sorted(left):
                obst = {c for j, c in left.items() if j != i} | exc
                n, _, turns = self._push(walkS, left[i], starts(obst | {left[i]}), obst, own[i], hs)
                if n is not None:
                    score += n + 2 * turns; del left[i]; break
            else:
                r = self._joint(walkS - exc, left, own, hs, ent)
                if r is None: r = self._joint(walkS, {**left, **{i: objs[i] for i in self.extras}}, own, hs, ent, limit=4000)
                if r is None: return 5 * len(left), None
                i, pos, n, turns = r
                score += n + 2 * turns + 3
                left = {j: pb[j] for j in left if j != i}
        # rooms of corridors, not open halls: walls inside the room count a little
        walls = sum(1 for (bx, by) in self.free for q in range(4)
                    if s['pats'].get((bx, by), ('.',) * 4)[q] == '#' and (bx, by) not in s['units'].values())
        return 0, score + 0.15 * walls

    # ------------------------------------------------------------------ search
    def random_state(self, rng):
        pats = {b: (('.',) * 4 if rng.random() < 0.45 else rng.choice(self.pats)) for b in self.free}
        blocks = list(self.free); rng.shuffle(blocks)
        units = {w: blocks[k] for k, w in enumerate(sorted(self.unit_pat))}
        s = {'pats': pats, 'units': units, 'objs': {}}
        kind, _ = self.grid(s)
        fl = sorted(c for c, k in kind.items() if k == '.'); rng.shuffle(fl)
        s['objs'] = {i: fl[k] for k, i in enumerate(self.boulders + self.people)}
        return s

    def mutate(self, s, rng):
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
            i = rng.choice(self.boulders + self.people) if r < 0.9 else rng.choice(self.people)
            kind, _ = self.grid(t)
            c0 = t['objs'][i]
            fl = [c for c, k in kind.items() if k == '.' and c not in t['objs'].values()]
            near = [c for c in fl if abs(c % self.W - c0 % self.W) + abs(c // self.W - c0 // self.W) <= 3]
            pool = near if near and rng.random() < 0.7 else fl
            if not pool: return None
            t['objs'][i] = rng.choice(pool)
        return t

    def anneal(self, rng, iters):
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
                if e2 == 0: found.append((sc2, t))
        return found

    def draw(self, s, rng):
        m2 = copy.deepcopy(self.m)
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
                    blk = cat.choose(p, left, up, rng, ok=lambda b, cat=cat, p=p: self.plain(cat, b, p))
                    if blk is not None: break
            if blk is None: return None
            out[i] = blk
        m2.blocks = bytes(out)
        for i, c in s['objs'].items():
            m2.objects[i].x, m2.objects[i].y = c % self.W, c // self.W
        return m2

    def check(self, m2):
        mm2 = build_model(self.d, m2, self.perms)
        if requirement_matrix(mm2) != self.vmatrix: return 'requirements differ'
        bad = bad_boulder_room(self.d, self.m, m2, self.perms, self.cat)
        if bad: return str(bad)
        from .trainers import unsafe_trainers
        if unsafe_trainers(mm2): return 'trainer trap'
        return None

    def generate(self, seed, chains=6, iters=2500, keep=40):
        rng = random.Random(f'{seed}-{self.m.name}-scratch-gym')
        found = []
        for k in range(chains):
            found += self.anneal(random.Random(f'{seed}-{self.m.name}-{k}'), iters)
        found.sort(key=lambda f: -f[0])
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
