"""Ecruteak Gym: the invisible floor, designed from scratch.

The dark part of the gym is drawn with only four blocks: pits along the top, the left or the right
(two pits, two floor tiles each), or solid dark wall. Every pit is a warp back to the entrance.
A design picks one of the four for every dark block. Simulated annealing scores it:

  + the walk from the door to Morty (up to ~1.6x vanilla) + turns on it (an unseen maze is
    hard where it bends) + side branches that end in pits (unseen decoys)
  - pits nobody can step into (each pit is a warp event, and every warp must stay reachable)

Morty, the trainers and the guide keep their spots (their tiles stay floor). The pit warp events
then move onto the new pits (any spare ones double up on a pit). Each design is checked with the
real movement model: same requirement matrix, no trap tiles, no trainer that can trap you."""
import copy, math, random
from collections import deque
from maprando.model.movement import build_model, classify
from maprando.model.reach import requirement_matrix
from maprando.generators.ice import traps
from maprando.checks.trainers import unsafe_trainers
from maprando.generators.caves import GenError
from maprando.checks.trainers import FACING

D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
PIT = 0x60

class PitGymGenerator:
    """Ecruteak Gym: picks one of four dark blocks (pits top/left/right, or solid) for every dark block
    of the gym, by simulated annealing (see the module docstring)."""
    W_WALK, W_TURN, W_DECOY, W_LOST = 1.0, 0.8, 1.5, 0.0

    def __init__(self, d, m, perms, cache=None):
        self.d, self.m, self.perms = d, m, perms
        self.mm = mm = build_model(d, m, perms)
        self.matrix = requirement_matrix(mm)
        self.vtraps = len(traps(mm))
        self.vunsafe = {(o.index, o.x, o.y) for o in unsafe_trainers(mm)}
        W, H = self.W, self.H = m.width, m.height
        self.W2, self.H2 = W * 2, H * 2
        coll = d.tileset_coll[m.tileset]
        def cls(c): return 'P' if c == PIT else ('#' if classify(mm, c) is None else '.')
        self.bpat = {b: tuple(cls(c) for c in q) for b, q in enumerate(coll)}
        meta = d.tileset_meta[m.tileset]
        def tiles(b): return set(meta[b*16:(b+1)*16])
        pit_blocks = sorted({b for b in m.blocks if 'P' in self.bpat[b]})
        if not pit_blocks: raise GenError(f'{m.name}: no pits')
        dark = set().union(*(tiles(b) for b in pit_blocks))
        # the dark blocks: every block of the tileset drawn only with the pit blocks' tiles
        self.options = sorted(b for b in range(len(coll)) if tiles(b) and tiles(b) <= dark
                              and set(self.bpat[b]) <= {'P', '.', '#'})
        # the pit area: vanilla's pit blocks
        self.free_blocks = [(bx, by) for by in range(H) for bx in range(W) if m.blocks[by * W + bx] in pit_blocks]
        # the pit warps move with the pits; a warp event on plain floor in the pit area (vanilla
        # has one, which never fires) stays, and its tile stays floor you can reach
        fbs = set(self.free_blocks)
        self.pit_warps = [i for i, w in enumerate(m.warps) if (w.x // 2, w.y // 2) in fbs and self.bpat[m.blocks[(w.y // 2) * W + w.x // 2]][(w.y % 2) * 2 + w.x % 2] == 'P']
        # pit warps vanilla never lets you reach (two pits walled in by other pits) must stay out
        # of reach: they go on pits nobody can step into
        seen = {a for row in self.matrix.values() for a in row}
        self.lost_warps = [i for i in self.pit_warps if f'warp:{i}' not in seen]
        self.live_warps = [i for i in self.pit_warps if i not in self.lost_warps]
        self.floor_warps = {(w.x, w.y) for i, w in enumerate(m.warps) if (w.x // 2, w.y // 2) in fbs and i not in self.pit_warps}
        # trainers standing in the dark move: each goes to the end of a corridor, looking down it
        # (as in vanilla); everyone else keeps their spot
        self.movers = [o for o in m.objects if o.obj_type == 'OBJECTTYPE_TRAINER' and (o.x // 2, o.y // 2) in fbs]
        self.people = {(o.x, o.y) for o in m.objects if o not in self.movers}
        # every warp you can arrive by (the doors, where a fall lands you, the floor warp)
        self.arrivals = {(w.x, w.y) for i, w in enumerate(m.warps) if i not in self.pit_warps}
        self.leader = next(o for o in m.objects if o.sprite == 'SPRITE_MORTY')
        self.starts = [(w.x, w.y) for i, w in enumerate(m.warps) if w.dest_map != m.name.upper() and
                       not w.dest_map.startswith('ECRUTEAK_GYM')]
        self.base = [[self.bpat[m.blocks[(y // 2) * W + x // 2]][(y % 2) * 2 + x % 2] for x in range(self.W2)]
                     for y in range(self.H2)]

    def grid(self, choice):
        g = [row[:] for row in self.base]
        for (bx, by), b in zip(self.free_blocks, choice):
            for q in range(4): g[by*2 + (q >> 1)][bx*2 + (q & 1)] = self.bpat[b][q]
        return g

    def score(self, choice):
        """(score, info) of a choice of dark blocks, or None if it breaks a rule (a person or warp off
        the floor, an unreachable pit, a changed requirement)."""
        g = self.grid(choice)
        W2, H2 = self.W2, self.H2
        if any(g[y][x] != '.' for (x, y) in self.people | self.floor_warps): return None
        def ok(x, y): return 0 <= x < W2 and 0 <= y < H2 and g[y][x] == '.' and (x, y) not in self.people
        dist, prev = {}, {}
        q = deque()
        for s in self.starts: dist[s] = 0; q.append(s)
        while q:
            c = q.popleft()
            for dx, dy in D4:
                n = (c[0] + dx, c[1] + dy)
                if n not in dist and ok(*n): dist[n] = dist[c] + 1; prev[n] = c; q.append(n)
        for o in self.m.objects:
            if o in self.movers: continue
            if not any((o.x + dx, o.y + dy) in dist for dx, dy in D4): return None
        if any(c not in dist for c in self.floor_warps): return None
        L = self.leader
        tgt = min(((L[0], L[1]) for L in [(L.x + dx, L.y + dy) for dx, dy in D4] if (L[0], L[1]) in dist),
                key=lambda c: dist[c], default=None)
        if tgt is None: return None
        path = [tgt]
        while path[-1] in prev: path.append(prev[path[-1]])
        turns = sum(1 for i in range(1, len(path) - 1)
                    if (path[i][0] - path[i-1][0], path[i][1] - path[i-1][1]) != (path[i+1][0] - path[i][0], path[i+1][1] - path[i][1]))
        # trainers: corridor ends off the way to Morty, facing down the corridor
        ps0 = set(path)
        fbt = {(x, y) for (bx, by) in self.free_blocks for x in (bx*2, bx*2+1) for y in (by*2, by*2+1)}
        rng = random.Random(repr(choice))
        placed, watched = {}, 0
        for o in self.movers:
            f = FACING.get(o.movement)
            tips = []
            for c in sorted(dist):
                if c not in fbt or c in ps0 or c in placed.values() or c in self.floor_warps: continue
                # nobody stands on or next to a warp you can arrive by (the floor warp counts)
                if any((c[0] + dx, c[1] + dy) in self.arrivals for dx, dy in D4): continue
                nb = [(c[0]+dx, c[1]+dy) for dx, dy in D4 if (c[0]+dx, c[1]+dy) in dist]
                d = f or (nb[0][0] - c[0], nb[0][1] - c[1]) if nb else None
                if d is None or (c[0] + d[0], c[1] + d[1]) not in dist: continue
                tip = len(nb) == 1
                if not tip and len(nb) > 2: continue
                # how much of the way to Morty it watches
                k, seen = 1, 0
                while k <= int(o.sight or 0):
                    v = (c[0] + d[0]*k, c[1] + d[1]*k)
                    if v not in dist: break
                    seen += v in ps0; k += 1
                tips.append((seen > 0, tip, rng.random(), c))
            if not tips: return None
            tips.sort(reverse=True)
            placed[o.index] = tips[0][3]; watched += tips[0][0]
        self.last_placed = placed
        occ = set(placed.values())
        dist2 = {}
        q = deque()
        for st in self.starts: dist2[st] = 0; q.append(st)
        while q:
            c = q.popleft()
            for dx, dy in D4:
                n = (c[0] + dx, c[1] + dy)
                if n not in dist2 and ok(*n) and n not in occ: dist2[n] = dist2[c] + 1; q.append(n)
        if tgt not in dist2: return None
        dist = dist2
        pits = [(x, y) for y in range(H2) for x in range(W2) if g[y][x] == 'P']
        lost = sum(1 for (x, y) in pits if not any((x + dx, y + dy) in dist for dx, dy in D4))
        ps = set(path)
        fb = {(x, y) for (bx, by) in self.free_blocks for x in (bx*2, bx*2+1) for y in (by*2, by*2+1)}
        decoy = sum(1 for c in dist if c in fb and c not in ps and c not in self.people and
                    sum(1 for dx, dy in D4 if (c[0]+dx, c[1]+dy) in dist) == 1)
        extra = max(0, len(pits) - lost - len(self.live_warps))   # a pit with no warp event is not a pit
        extra += max(0, len(self.lost_warps) - lost)
        cap = getattr(self, 'cap', 1e9)
        s = self.W_WALK * min(len(path), cap) + 4.0 * watched + self.W_TURN * turns + self.W_DECOY * min(decoy, 4) - self.W_LOST * lost - 10 * extra
        self.last_pits = pits
        self.last_reached_pits = [c for c in pits if any((c[0] + dx, c[1] + dy) in dist for dx, dy in D4)]
        return s, dict(walk=len(path), watched=watched, turns=turns, decoys=decoy, pits=len(pits), lost=lost, extra=extra)

    def build(self, choice):
        """the map drawn with a choice of dark blocks, the pit warps moved onto the new pits."""
        m2 = copy.deepcopy(self.m)
        out = bytearray(m2.blocks)
        for (bx, by), b in zip(self.free_blocks, choice): out[by * self.W + bx] = b
        m2.blocks = bytes(out)
        # warp events go on the pits you can step into; a pit nobody can reach needs none (vanilla
        # has one), and spare events double up on reachable pits
        self.score(list(choice))
        pits = self.last_reached_pits
        lost = [c for c in self.last_pits if c not in set(pits)]
        for o in m2.objects:
            if o.index in self.last_placed: o.x, o.y = self.last_placed[o.index]
        for k, i in enumerate(self.live_warps):
            m2.warps[i].x, m2.warps[i].y = pits[k % len(pits)]
        for k, i in enumerate(self.lost_warps):
            m2.warps[i].x, m2.warps[i].y = lost[k]
        return m2

    def generate(self, seed, iters=6000, chains=3, keep=20):
        """the new map for a seed: the best of a few annealing chains that passes every check
        (self.info says how long the walk is)."""
        van = [self.m.blocks[by * self.W + bx] for (bx, by) in self.free_blocks]
        r = self.score(van)
        self.cap = max(r[1]['walk'] * 1.6, r[1]['walk'] + 10) if r else 40
        found = []
        n = len(self.free_blocks)
        for k in range(chains):
            rng = random.Random(f'{seed}-{self.m.name}-pit-{k}')
            # random designs almost never connect the door to Morty: start from vanilla's and
            # walk away from it (it shares nothing with vanilla after a few hundred moves)
            cur = list(van)
            r = self.score(cur); cs = r[0] if r else -1e9
            for it in range(iters):
                T = 3.0 * (1 - it / iters) + 0.05
                i = rng.randrange(n); old = cur[i]
                cur[i] = rng.choice([o for o in self.options if o != old])
                r = self.score(cur); s = r[0] if r else -1e9
                if s >= cs or rng.random() < math.exp((s - cs) / T):
                    cs = s
                    if r and r[1]['extra'] == 0: found.append((s, tuple(cur), r[1]))
                else:
                    cur[i] = old
        found.sort(key=lambda t: -t[0])
        # the space is small (four blocks for each of fifteen spots, with the trainers' tiles
        # fixed), so the very best design would come back every seed: take any good one
        seen, last, ok = set(), None, []
        top = found[0][0] if found else 0
        for s, ch, info in found:
            if ch in seen or ch == tuple(van): continue
            if s < top - 8 or len(ok) >= keep: break
            seen.add(ch)
            m2 = self.build(ch)
            mm2 = build_model(self.d, m2, self.perms)
            why = None
            mat = requirement_matrix(mm2)
            if mat != self.matrix:
                why = 'matrix ' + str([(e, a, self.matrix.get(e, {}).get(a), mat.get(e, {}).get(a)) for e in set(mat) | set(self.matrix)
                                       for a in set(mat.get(e, {})) | set(self.matrix.get(e, {}))
                                       if mat.get(e, {}).get(a) != self.matrix.get(e, {}).get(a)][:4])
            elif len(traps(mm2)) > self.vtraps: why = 'traps'
            elif {(o.index, o.x, o.y) for o in unsafe_trainers(mm2)} - self.vunsafe: why = 'trainer'
            if getattr(self, 'debug', False): print('  cand', round(s, 1), info, why)
            if why is None: ok.append((s, m2, info))
            else: last = why
        if ok:
            s, m2, info = random.Random(f'{seed}-{self.m.name}-pick').choice(ok)
            self.m2, self.info = m2, dict(info, score=round(s, 1), among=len(ok))
            return m2
        raise GenError(f'{self.m.name}: no pit design passed ({last})')
