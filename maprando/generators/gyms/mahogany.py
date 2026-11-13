"""Mahogany Gym: an ice floor designed from scratch.

Unlike Ice Path, this ice has no rocks: what stops a slide is a single floor tile in the ice (you
slide onto it and stop), a pillar, a person, or the walls. The design is a tile grid of ice ('I'),
floor ('.') and pillar ('#') over the free blocks, kept drawable with the gym's own blocks (each
2x2 pattern must be one of them). Simulated annealing scores it with a quick slide simulation:

  + presses on the quickest way from the door to Pryce (up to ~1.6x vanilla)
  + stop spots you can reach (a richer floor) - spots you can slide into but never leave (traps,
    a softlock) - people you cannot reach - stop tiles far from vanilla's share

Pryce, the trainers and the guide keep their spots; the floor around them changes. Every design
is then checked with the real movement model: the same requirement matrix (everyone reachable),
no tile you can reach but not leave, no trainer that can trap you, no ledge jump into a wall."""
import copy, math, random
from collections import deque
from maprando.model.movement import build_model, classify, COLL_ICE
from maprando.model.reach import requirement_matrix
from maprando.generators.ice import traps
from maprando.checks.jumps import bad_jumps
from maprando.checks.trainers import unsafe_trainers
from maprando.generators.caves import GenError

D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))

class IceGymGenerator:
    """Mahogany Gym: an ice floor designed tile by tile around the fixed people, by simulated annealing
    (see the module docstring)."""
    W_PRESS, W_STOPS, W_TRAP, W_MISS, W_SHARE, W_CLUMP = 2.0, 0.03, 30.0, 25.0, 40.0, 1.5

    def __init__(self, d, m, perms, cache=None):
        self.d, self.m, self.perms = d, m, perms
        self.mm = mm = build_model(d, m, perms)
        self.matrix = requirement_matrix(mm)
        if any(x != 0 for e in self.matrix.values() for v in e.values() for x in v):
            raise GenError(f'{m.name}: requirements')
        self.vtraps = len(traps(mm))
        self.vjumps = {(c, dd) for c, dd, _ in bad_jumps(mm)}
        self.vunsafe = {(o.index, o.x, o.y) for o in unsafe_trainers(mm)}
        W, H = self.W, self.H = m.width, m.height
        self.W2, self.H2 = W * 2, H * 2
        coll = d.tileset_coll[m.tileset]
        def cls(c):
            if c == COLL_ICE: return 'I'
            return '#' if classify(mm, c) is None else '.'
        self.bpat = {b: tuple(cls(c) for c in q) for b, q in enumerate(coll)}
        # blocks this gym draws its ice with, per pattern (most used first)
        use = {}
        for b in m.blocks: use[b] = use.get(b, 0) + 1
        self.pats = {}
        for b in sorted(use, key=lambda b: -use[b]):
            p = self.bpat[b]
            if 'I' in p and set(p) <= {'I', '.', '#'}: self.pats.setdefault(p, []).append(b)
        # free blocks: ice blocks with no event on them; the rest stays
        busy = {(w.x // 2, w.y // 2) for w in m.warps} | {(b.x // 2, b.y // 2) for b in m.bg_events} | \
               {(e.x // 2, e.y // 2) for e in m.coord_events}
        self.free_blocks = [(bx, by) for by in range(1, H) for bx in range(W)
                            if (bx, by) not in busy and self.bpat[m.blocks[by * W + bx]] in self.pats]
        fb = set(self.free_blocks)
        self.van = [[self.bpat[m.blocks[(y // 2) * W + x // 2]][(y % 2) * 2 + x % 2] for x in range(self.W2)]
                    for y in range(self.H2)]
        self.people = {(o.x, o.y) for o in m.objects}
        self.fixed = {(x, y) for y in range(self.H2) for x in range(self.W2) if (x // 2, y // 2) not in fb} | self.people
        self.free_tiles = [(x, y) for y in range(self.H2) for x in range(self.W2) if (x, y) not in self.fixed]
        self.ft = set(self.free_tiles)
        self.starts = [(w.x, w.y) for w in m.warps]
        self.leader = next(o for o in m.objects if o.sprite == 'SPRITE_PRYCE')
        vf = [self.van[y][x] for (x, y) in self.free_tiles]
        self.van_floor = sum(c == '.' for c in vf) / max(1, len(vf))
        self.van_pillar = sum(c == '#' for c in vf) / max(1, len(vf))
        r = self.score(self.van)
        self.van_press = r[1]['press'] if r else 10
        self.cap = max(self.van_press * 2.0, self.van_press + 8)

    # ------------------------------------------------------------------ simulation
    def moves(self, g, c):
        """the cells where the player stops, sliding from cell c on grid g in each direction."""
        W2, H2 = self.W2, self.H2
        out = []
        for dx, dy in D4:
            x, y = c[0] + dx, c[1] + dy
            def blocked(x, y):
                return not (0 <= x < W2 and 0 <= y < H2) or g[y][x] == '#' or (x, y) in self.people
            if blocked(x, y): continue
            while g[y][x] == 'I' and not blocked(x + dx, y + dy):
                x, y = x + dx, y + dy
            out.append((x, y))
        return out

    def score(self, g):
        """(score, info) of ice grid g, or None if Pryce cannot be reached; info counts the presses on
        the quickest way (press) and the traps."""
        dist = {}
        q = deque()
        for s in self.starts: dist[s] = 0; q.append(s)
        succ = {}
        while q:
            c = q.popleft()
            succ[c] = self.moves(g, c)
            for t in succ[c]:
                if t not in dist: dist[t] = dist[c] + 1; q.append(t)
        # traps: reachable stops that cannot get back to the door
        rev = {}
        for c, ts in succ.items():
            for t in ts: rev.setdefault(t, []).append(c)
        good = set(self.starts); q = deque(good)
        while q:
            t = q.popleft()
            for c in rev.get(t, ()):
                if c not in good: good.add(c); q.append(c)
        trapped = len(set(dist) - good)
        # people: stop next to them
        miss = 0
        press_l = None
        for o in self.m.objects:
            nb = [(o.x + dx, o.y + dy) for dx, dy in D4]
            got = [dist[n] for n in nb if n in dist]
            if not got: miss += 1
            elif o is self.leader: press_l = min(got)
        if press_l is None: return None
        vf = [g[y][x] for (x, y) in self.free_tiles]
        fl = sum(c == '.' for c in vf) / max(1, len(vf)); pl = sum(c == '#' for c in vf) / max(1, len(vf))
        # stop tiles read best one at a time (vanilla's are single tiles in the ice)
        clump = sum(1 for (x, y) in self.free_tiles if g[y][x] == '.' and
                    any(0 <= x+dx < self.W2 and 0 <= y+dy < self.H2 and g[y+dy][x+dx] == '.' and (x+dx, y+dy) in self.ft
                        for dx, dy in ((1, 0), (0, 1))))
        s = (self.W_PRESS * min(press_l, getattr(self, 'cap', 1e9)) + self.W_STOPS * len(dist)
             - self.W_TRAP * trapped - self.W_MISS * miss - self.W_CLUMP * clump
             - self.W_SHARE * (abs(fl - getattr(self, 'van_floor', fl)) + abs(pl - getattr(self, 'van_pillar', pl))))
        return s, dict(press=press_l, stops=len(dist), traps=trapped, miss=miss, floor=round(fl, 2), pillars=round(pl, 2))

    def drawable(self, g, blks):
        for (bx, by) in blks:
            p = tuple(g[by*2 + (q >> 1)][bx*2 + (q & 1)] for q in range(4))
            if p not in self.pats: return False
        return True

    # ------------------------------------------------------------------ search
    def generate(self, seed, iters=None, chains=3, keep=20):
        """the new map for a seed: the best design of a few annealing chains that passes every check."""
        iters = iters or 150 * len(self.free_tiles)
        found = []
        for k in range(chains):
            rng = random.Random(f'{seed}-{self.m.name}-ice-{k}')
            g = [row[:] for row in self.van]
            for (x, y) in self.free_tiles: g[y][x] = 'I'
            r = self.score(g); cur = r[0] if r else -1e9
            T0 = 5.0
            for it in range(iters):
                T = T0 * (1 - it / iters) + 0.05
                x, y = rng.choice(self.free_tiles)
                v = rng.choice('II..#')
                # pillars stand two tiles tall in a block: change the tile and the one sharing its
                # block column together
                cells = [(x, y), (x, y ^ 1)] if (v == '#' or g[y][x] == '#') and (x, y ^ 1) in self.ft else [(x, y)]
                if all(g[b][a] == v for a, b in cells): continue
                olds = [(a, b, g[b][a]) for a, b in cells]
                for a, b in cells: g[b][a] = v
                if not self.drawable(g, [(x // 2, y // 2)]):
                    for a, b, o in olds: g[b][a] = o
                    continue
                r = self.score(g)
                s = r[0] if r else -1e9
                if s >= cur or rng.random() < math.exp((s - cur) / T):
                    cur = s
                    if r and r[1]['traps'] == 0 and r[1]['miss'] == 0:
                        found.append((s, tuple(tuple(row) for row in g), r[1]))
                else:
                    for a, b, o in olds: g[b][a] = o
        found.sort(key=lambda t: -t[0])
        seen, last = set(), None
        for s, g, info in found:
            if g in seen: continue
            seen.add(g)
            if len(seen) > keep: break
            m2 = copy.deepcopy(self.m)
            out = bytearray(m2.blocks)
            for (bx, by) in self.free_blocks:
                p = tuple(g[by*2 + (q >> 1)][bx*2 + (q & 1)] for q in range(4))
                out[by * self.W + bx] = self.pats[p][0]
            m2.blocks = bytes(out)
            mm2 = build_model(self.d, m2, self.perms)
            why = None
            if requirement_matrix(mm2) != self.matrix: why = 'matrix'
            elif len(traps(mm2)) > self.vtraps: why = 'traps'
            elif {(c, dd) for c, dd, _ in bad_jumps(mm2)} - self.vjumps: why = 'jumps'
            elif {(o.index, o.x, o.y) for o in unsafe_trainers(mm2)} - self.vunsafe: why = 'trainer'
            if getattr(self, 'debug', False): print('  cand', round(s, 1), info, why)
            if why is None:
                self.m2, self.info = m2, dict(info, score=round(s, 1))
                return m2
            last = why
        raise GenError(f'{self.m.name}: no ice design passed ({last})')
