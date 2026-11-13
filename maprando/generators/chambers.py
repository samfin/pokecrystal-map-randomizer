"""Boulder rooms built from chambers (Ice Path B1F): small pieces designed whole - walls, holes and
boulders together - then put into the room and joined by corridors.

A piece is a block-aligned rectangle with one doorway: a tile on its edge, with a porch tile just
outside it in the corridor. Outside the piece everything else touching it is rock, so a piece can
be designed on its own: from the porch, with the exact checks a small room allows -
  - the fewest boulder moves (a move: a run of pushes of one boulder) to drop every boulder into
    its own hole (pushes.drop_moves), as many as possible;
  - 'open' pieces: every hole can be walked to without pushing; 'gated': none can (Strength);
  - no boulder can reach another's hole, the doorway or the porch, over every push (alone);
  - no position shuts the player in (boulderroom.player_traps, the porch is the way out).
The room's moves are the sum of its pieces' (pieces never share a boulder or a way)."""
import math, random
from collections import deque
from maprando.checks.pushes import drop_moves, K
# maps whose pieces are chosen by their fewest submoves (`fewest_submoves`, tools/subscore_pieces.py)
SUBMOVE_MAPS = {'BlackthornGym2F'}
from maprando.checks.boulder_room import player_traps

D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
# piece sizes (blocks) per map: Blackthorn 2F is only 5 blocks wide
SIZES_FOR = {'IcePathB1F': [(4, 4), (4, 3), (3, 4)], 'BlackthornGym2F': [(3, 3), (3, 4), (4, 3)]}
SIZES = SIZES_FOR['IcePathB1F']


def piece_kinds(g):
    """[(mode, hole warps, boulders with no hole)]: the holes vanilla reaches without Strength
    ('open') and the rest ('gated'); boulders with no hole split between them, the larger share
    to the gated piece (they can be its gate)."""
    row = g.vmatrix.get(f'warp:{g.stairs[0]}', {})
    open_h = [w for w in g.holes if 0 in row.get(f'warp:{w}', [])]
    gated_h = [w for w in g.holes if w not in open_h]
    kinds = [[m, hs, 0] for m, hs in (('open', open_h), ('gated', gated_h)) if hs]
    n = len(g.extras)
    for k in range(n):
        kinds[-1 - (k % len(kinds))][2] += 1
    return [tuple(k) for k in kinds]
SIDES = [(0, 1), (0, -1), (1, 0), (-1, 0)]


def rock_sides_for(pats):
    """sides of a piece where the tileset cannot draw rock just outside it: above a piece needs a
    block with a wall along its bottom half, below one along its top half, and so on."""
    ps = set(pats)
    need = {(0, -1): (2, 3), (0, 1): (0, 1), (-1, 0): (1, 3), (1, 0): (0, 2)}
    return tuple(sd for sd, (a, b) in need.items() if not any(p[a] == '#' and p[b] == '#' for p in ps))


def single_reach(W, floor, box, starts, targets, limit=20000):
    """targets one boulder can be pushed onto (the others gone), the player starting from
    `starts`; floor: tiles the player walks on and the boulder moves over (int cells). Areas are
    bitmasks."""
    n = max(set(floor) | set(targets) | {box}) + W + 1
    F = 0
    for c in floor: F |= 1 << c
    notL = notR = 0
    for c in range(n):
        if c % W != 0: notL |= 1 << c
        if c % W != W - 1: notR |= 1 << c
    comps = {}
    def comp(b, c):
        cs = comps.get(b)
        if cs is None: cs = comps[b] = []
        bit = 1 << c
        for m in cs:
            if m & bit: return m
        free = F & ~(1 << b)
        if not free & bit: return 0
        r = bit
        while True:
            m = (r | ((r << 1) & notL) | ((r >> 1) & notR) | (r << W) | (r >> W)) & free
            if m == r: break
            r = m
        cs.append(r)
        return r
    tg = set(targets)
    got = set()
    init = set()
    for s in starts:
        if s != box and (F >> s) & 1:
            R = comp(box, s)
            if R: init.add((box, R))
    seen = set(init); q = deque(init)
    while q and len(seen) < limit:
        b, R = q.popleft()
        for dd in (1, -1, W, -W):
            if dd in (1, -1) and ((b + dd) // W != b // W or (b - dd) // W != b // W): continue
            p, t = b - dd, b + dd
            if p < 0 or not (R >> p) & 1: continue
            if t in tg: got.add(t); continue
            if t < 0 or not (F >> t) & 1: continue
            st = (t, comp(t, b))
            if st[1] and st not in seen: seen.add(st); q.append(st)
    return got


class Piece:
    """one chamber: bw x bh blocks, a doorway on side `side` ((dx, dy) pointing out of the piece),
    holes `holes` = [(warp, hole block pattern, hole quadrant)], one boulder per hole, `mode`
    'open' or 'gated'. pats: the 2x2 floor/wall shapes the tileset draws."""
    weight = K      # drop_moves weighting (0: fewest submoves, whatever the moves)
    def __init__(self, bw, bh, side, holes, mode, pats, doors=None, extras=0, rock_sides=()):
        """extras: boulders with no hole of their own (blockers: they may reach no hole, the
        doorway or the porch); rock_sides: sides where the tileset cannot draw rock just outside the
        piece (Blackthorn: no block has a wall along its bottom half, so not above a piece): the
        piece's own edge there is rock, but for its doorway."""
        self.bw, self.bh, self.side, self.holes, self.mode = bw, bh, side, holes, mode
        self.W, self.H = bw * 2 + 2, bh * 2 + 2          # with a ring of rock around (porch in it)
        self.pats = set(pats)
        W = self.W
        dx, dy = side
        # doorway candidates: edge tiles of the piece on that side
        if dx: xs = [W - 2] if dx > 0 else [1]; self.doors = [(xs[0], y) for y in range(1, self.H - 1)]
        else: ys = [self.H - 2] if dy > 0 else [1]; self.doors = [(x, ys[0]) for x in range(1, W - 1)]
        if doors is not None: self.doors = [c for c in self.doors if c in set(doors)]
        self.extras = [('x', k) for k in range(extras)]
        self.rock_sides = tuple(rock_sides)
        self.edge = {}
        for sd in self.rock_sides:
            dx, dy = sd
            if dx: xs = W - 2 if dx > 0 else 1; self.edge[sd] = [(xs, y) for y in range(1, self.H - 1)]
            else: ys = self.H - 2 if dy > 0 else 1; self.edge[sd] = [(x, ys) for x in range(1, W - 1)]
        self.blocks = [(bx, by) for by in range(bh) for bx in range(bw)]

    def cells(self, bx, by):
        return [((bx * 2 + 1 + (q & 1)), (by * 2 + 1 + (q >> 1))) for q in range(4)]

    def idx(self, x, y): return y * self.W + x

    def grid(self, s):
        """{(x, y): '.', '#', 'H'} inside the piece and the hole cell of each warp."""
        g = {}
        hb = {b: k for k, b in enumerate(s['hole_at'])}
        hcell = {}
        for b in self.blocks:
            k = hb.get(b)
            cs = self.cells(*b)
            if k is not None:
                w, pat, q = self.holes[k]
                for i, c in enumerate(cs):
                    g[c] = 'H' if i == q else ('.' if pat[i] == '.' else '#')
                hcell[w] = cs[q]
            else:
                for c in cs: g[c] = s['tiles'][c]
        return g, hcell

    def evaluate(self, s, exact=True):
        """(violations, moves, info)."""
        g, hcell = self.grid(s)
        W = self.W
        door = s['door']; dx, dy = self.side
        porch = (door[0] + dx, door[1] + dy)
        e = 0
        if g[door] != '.': e += 3
        # sealed by its own rock where nothing outside can be drawn as rock
        for sd, cs in self.edge.items():
            e += sum(1 for c in cs if c != door and g[c] != '#')
        # block shapes the tileset can draw
        hb = set(s['hole_at'])
        for b in self.blocks:
            if b in hb: continue
            if tuple(g[c] for c in self.cells(*b)) not in self.pats: e += 2
        floor = {self.idx(*c) for c, k in g.items() if k == '.'} | {self.idx(*porch)}
        hole_c = {w: self.idx(*c) for w, c in hcell.items()}
        hs = set(hole_c.values())
        bl = {w: self.idx(*s['boulders'][w]) for w in list(hole_c) + self.extras}
        if any(b not in floor or b == self.idx(*porch) for b in bl.values()): return e + 20, 0, None
        if len(set(bl.values())) < len(bl): return e + 20, 0, None
        P = self.idx(*porch); D = self.idx(*door)
        # walk without pushing: the holes are reached ('open') or not ('gated')
        def flood(start, walk):
            seen = {start}; st = [start]
            while st:
                a = st.pop()
                for n in (a - 1, a + 1, a - W, a + W):
                    if n in walk and n not in seen: seen.add(n); st.append(n)
            return seen
        S0 = flood(P, floor - set(bl.values()))
        for h in hs:
            touched = any(n in S0 for n in (h - 1, h + 1, h - W, h + W))
            if (self.mode == 'open') != touched: e += 4
        # floor nobody reaches (even pushing everything away)
        SA = flood(P, floor)
        e += 0.2 * (len(floor) - len(SA))
        # no boulder reaches another's hole, the doorway or the porch
        for w, b in bl.items():
            bad = (hs - {hole_c.get(w)}) | {D, P}
            got = single_reach(W, floor - {D, P} | {D, P}, b, [P], bad)
            e += 4 * len(got)
        if e or not exact: return e, 0, None
        own = {w: hole_c[w] for w in bl if w in hole_c}
        mvs = drop_moves(W, floor, bl, own, [P], limit=60000, detail=True, weight=self.weight)
        if mvs is None: return 5, 0, None
        mv, sub = mvs
        tr = player_traps(W, floor, frozenset(), hs, own, bl, [P], {P}, limit=60000)
        if tr is None or tr[1]: return 5, 0, None
        walls = sum(1 for k in g.values() if k == '#')
        return 0, mv, dict(moves=mv, submoves=sub, states=tr[0], walls=walls)

    def edges_ok(self, s):
        """the rock edges are rock (but for the doorway)."""
        if not self.edge: return True
        g, _ = self.grid(s)
        return all(c == s['door'] or g[c] == '#' for cs in self.edge.values() for c in cs)

    def random_state(self, rng):
        """a random design for the piece (tiles, hole positions, door and boulders), with rock on its
        rock edges."""
        for _ in range(200):
            s = self._random_state(rng)
            if not self.edge: return s
            # blocks on a rock edge get a shape with rock there; holes stay off it
            ec = {c for cs in self.edge.values() for c in cs}
            for b in self.blocks:
                cs = self.cells(*b)
                if not any(c in ec for c in cs): continue
                ok = [p for p in sorted(self.pats) if all(k == '#' or c not in ec or c == s['door'] for c, k in zip(cs, p))]
                if ok:
                    for c, k in zip(cs, rng.choice(ok)): s['tiles'][c] = k
            if self.edges_ok(s): return s
        return s

    def _random_state(self, rng):
        tiles = {}
        for b in self.blocks:
            # mostly rock to start with: narrow ways keep boulders where they belong
            r = rng.random()
            p = ('#',) * 4 if r < 0.45 and ('#',) * 4 in self.pats else rng.choice(sorted(self.pats)) if r < 0.85 else ('.',) * 4
            for c, k in zip(self.cells(*b), p): tiles[c] = k
        hole_at = rng.sample(self.blocks, len(self.holes))
        s = {'tiles': tiles, 'hole_at': hole_at, 'door': rng.choice(self.doors), 'boulders': {}}
        g, _ = self.grid(s)
        fl = [c for c, k in g.items() if k == '.' and c != s['door']]
        rng.shuffle(fl)
        ids = [w for w, _, _ in self.holes] + self.extras
        if len(fl) < len(ids): fl = fl + [c for c in g if c not in fl]
        s['boulders'] = {w: fl[k] for k, w in enumerate(ids)}
        return s

    def mutate(self, s, rng):
        """a neighbouring design of s: one tile flipped, a block reshaped, a hole, the door or a
        boulder moved."""
        t = {'tiles': dict(s['tiles']), 'hole_at': list(s['hole_at']), 'door': s['door'], 'boulders': dict(s['boulders'])}
        r = rng.random()
        if r < 0.32:
            c = (rng.randrange(1, self.W - 1), rng.randrange(1, self.H - 1))
            t['tiles'][c] = '#' if t['tiles'][c] == '.' else '.'
        elif r < 0.5:
            # a whole block to another shape the tileset draws (few shapes: one tile at a time
            # would pass through undrawable ones)
            b = rng.choice(self.blocks)
            for c, k in zip(self.cells(*b), rng.choice(sorted(self.pats))): t['tiles'][c] = k
        elif r < 0.6:
            k = rng.randrange(len(t['hole_at']))
            b = rng.choice(self.blocks)
            if b in t['hole_at']: return None
            t['hole_at'][k] = b
        elif r < 0.67:
            t['door'] = rng.choice(self.doors)
        else:
            w = rng.choice(sorted(t['boulders'], key=str))
            c0 = t['boulders'][w]
            g, _ = self.grid(t)
            fl = [c for c, k in g.items() if k == '.' and c not in t['boulders'].values()]
            near = [c for c in fl if abs(c[0] - c0[0]) + abs(c[1] - c0[1]) <= 2]
            pool = near if near and rng.random() < 0.7 else fl
            if not pool: return None
            t['boulders'][w] = rng.choice(pool)
        if not self.edges_ok(t): return None       # rock edges are never opened
        return t

    def generate(self, rng, iters=6000, polish=1500):
        """anneal: what is wrong first, then the most moves (and a few walls)."""
        s = self.random_state(rng)
        # moves first, submoves (runs of pushes in one direction) as a tiebreak, a few walls
        def J(e, mv, info): return -10 * e + (8 * mv + 0.6 * info['submoves'] + 0.05 * info['walls'] if info else 0)
        e, mv, info = self.evaluate(s)
        cur = J(e, mv, info)
        best = None; left = None
        for it in range(iters):
            if left is not None:
                left -= 1
                if left <= 0: break
                T = 1.5 * left / polish + 0.1
            else:
                T = 8.0 * (0.3 / 8.0) ** (it / iters)
            t = self.mutate(s, rng)
            if t is None: continue
            e2, mv2, info2 = self.evaluate(t)
            if left is not None and e2: continue
            j = J(e2, mv2, info2)
            if j >= cur or rng.random() < math.exp((j - cur) / T):
                s, cur = t, j
                if e2 == 0:
                    if best is None or j > best[0]: best = (j, t, info2)
                    if left is None: left = polish
        return best

    def show(self, s):
        """design s as text (D door, P where the player enters, o boulders, O holes), for tools and
        debugging."""
        g, hcell = self.grid(s)
        out = []
        for y in range(self.H):
            row = ''
            for x in range(self.W):
                c = (x, y)
                if c == s['door']: row += 'D'; continue
                dx, dy = self.side
                if (x - dx, y - dy) == s['door']: row += 'P'; continue
                b = [w for w, v in s['boulders'].items() if v == c]
                if b: row += 'o'; continue
                row += g.get(c, ' ').replace('H', 'O')
            out.append(row)
        return '\n'.join(out)


def best_piece(bw, bh, side, holes, mode, pats, doors, rng, tries=6, extras=0, rock_sides=()):
    """the piece with the most moves over a few runs (None if no run finds one)."""
    best = None
    for k in range(tries):
        pc = Piece(bw, bh, side, holes, mode, pats, doors, extras=extras, rock_sides=rock_sides)
        if not pc.doors: return None
        r = pc.generate(random.Random(f'{rng.random()}'))
        if r and (best is None or (r[2]['moves'], r[2]['submoves']) > (best[2]['moves'], best[2]['submoves'])): best = (pc, r[1], r[2])
    return best


_LIB = {}
def library(name='IcePathB1F'):
    """the piece library for a map (maprando/data/<name>_pieces.json; made by tools/make_pieces.py)."""
    if name not in _LIB:
        import json
        from importlib import resources
        f = resources.files('maprando') / 'data' / f'{name}_pieces.json'
        _LIB[name] = json.loads(f.read_text())['pieces'] if f.is_file() else []
    return _LIB[name]


def from_library(bw, bh, side, holes, mode, pats, doors, rng, name='IcePathB1F', extras=0, rock_sides=()):
    """a piece from the library for this spot (doorway among `doors`, local piece coordinates),
    pieces needing more moves more likely; None if none fits."""
    key = f'{bw},{bh},{side[0]},{side[1]},{mode}'
    ok = set(map(tuple, doors))
    cands = [q for q in library(name) if q['key'] == key and tuple(q['door']) in ok
             and len(q['boulders']) == len(holes) + extras]
    if not cands: return None
    if name in SUBMOVE_MAPS:
        cands = [q for q in cands if 'fewest_submoves' in q]
        if not cands: return None
        weights = [q['fewest_submoves'] ** 3 for q in cands]
    else: weights = [q['moves'] ** 3 * (1 + q.get('submoves', 0) / 4) for q in cands]
    q = rng.choices(cands, weights)[0]
    pc = Piece(bw, bh, side, holes, mode, pats, extras=extras, rock_sides=rock_sides)
    tiles = {(x + 1, y + 1): row[x] for y, row in enumerate(q['tiles']) for x in range(len(row))}
    ids = [h[0] for h in holes] + pc.extras
    st = {'tiles': tiles, 'hole_at': [tuple(b) for b in q['holes']], 'door': tuple(q['door']),
          'boulders': {ids[i]: tuple(q['boulders'][i]) for i in range(len(ids))}}
    return pc, st, dict(moves=q['moves'], submoves=q.get('submoves', 0), fewest_submoves=q.get('fewest_submoves', 0))


def build_room(g, rng, layouts=25, log=None, name='IcePathB1F'):
    """a boulder room of two pieces (`open`: holes reached without pushing, `gated`: holes behind
    Strength, as vanilla's requirement matrix says) and corridors, on the free blocks of the
    BoulderGymScratch `g` (Ice Path B1F). Returns (map, moves) or None."""
    W, H = g.W, g.H
    free = set(g.free)
    fixed = g.top                                     # cells outside the free blocks: vanilla
    kinds = piece_kinds(g)
    rs = rock_sides_for(g.pats)
    ladders = list(g.fixed_wcell.values())
    def walkable_fixed(c): return fixed.get(c) == '.'
    sizes = SIZES_FOR.get(name, SIZES)
    for lay in range(layouts):
        rects = []
        ok = True
        for mode, hs, nx in kinds:
            for _ in range(60):
                bw, bh = rng.choice(sizes)
                bx0, by0 = rng.choice(sorted(free))
                blks = {(bx0 + i, by0 + j) for i in range(bw) for j in range(bh)}
                if not blks <= free or any(blks & r[2] for r in rects): continue
                # nothing walkable that stays as in vanilla may touch it (the piece is sealed)
                x0, y0, x1, y1 = bx0 * 2, by0 * 2, (bx0 + bw) * 2 - 1, (by0 + bh) * 2 - 1
                # (a side the piece seals with its own rock needs nothing outside)
                ring = [(x, y) for x in range(x0, x1 + 1) for y, sd in ((y0 - 1, (0, -1)), (y1 + 1, (0, 1))) if sd not in rs] + \
                       [(x, y) for y in range(y0, y1 + 1) for x, sd in ((x0 - 1, (-1, 0)), (x1 + 1, (1, 0))) if sd not in rs]
                if any(0 <= x < W and 0 <= y < H and walkable_fixed(y * W + x) for x, y in ring): continue
                if any(0 <= x < W and 0 <= y < H and (y * W + x) in ladders for x, y in ring): continue
                # a side whose porches lie in corridor space
                sides = []
                for side in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                    dx, dy = side
                    if dx: cand = [((x1 if dx > 0 else x0), y) for y in range(y0, y1 + 1)]
                    else: cand = [(x, (y1 if dy > 0 else y0)) for x in range(x0, x1 + 1)]
                    porches = [(x + dx, y + dy) for x, y in cand]
                    good = [c for c, pc_ in zip(cand, porches) if 0 <= pc_[0] < W and 0 <= pc_[1] < H
                            and (pc_[0] // 2, pc_[1] // 2) in free and (pc_[0] // 2, pc_[1] // 2) not in blks
                            and not any((pc_[0] // 2, pc_[1] // 2) in r[2] for r in rects)]
                    if good: sides.append((side, good))
                if not sides: continue
                side, doors = rng.choice(sides)
                rects.append((mode, hs, blks, (bx0, by0, bw, bh), side, doors, nx))
                break
            else:
                ok = False; break
        if not ok: continue
        # the pieces, each designed for its spot
        pieces = []
        for mode, hs, blks, (bx0, by0, bw, bh), side, doors, nx in rects:
            holes = [(w, g.unit_pat[w], g.unit_q[w]) for w in hs]
            local = [(x - bx0 * 2 + 1, y - by0 * 2 + 1) for x, y in doors]
            # the library first (pieces made ahead, tools/make_pieces.py), else made here
            bp = from_library(bw, bh, side, holes, mode, g.pats, local, rng, name=name, extras=nx, rock_sides=rs)
            if bp is None: bp = best_piece(bw, bh, side, holes, mode, g.pats, local, rng, extras=nx, rock_sides=rs)
            if bp is None: break
            pieces.append((bp, (bx0, by0), side))
        if len(pieces) < len(rects):
            if log: log(f'layout {lay}: a piece found no design')
            continue
        # stairs that move (Ice Path's ladders): in corridor blocks clear of the pieces and their
        # porches, far apart
        lad = dict(g.fixed_wcell)
        moving = [w for w in g.stairs if w not in lad]
        if moving:
            pblk = set().union(*[r[2] for r in rects])
            near = {(bx + dx, by + dy) for bx, by in pblk for dx, dy in ((0, 0),) + D4}
            porch_b = set()
            for (pc, st, info), (bx0, by0), side in pieces:
                dr = st['door']; px, py = dr[0] + bx0 * 2 - 1 + side[0], dr[1] + by0 * 2 - 1 + side[1]
                porch_b.add((px // 2, py // 2))
            cand = sorted(b for b in free if b not in near and b not in porch_b)
            if len(cand) < len(moving): continue
            chosen = [rng.choice(cand)]
            while len(chosen) < len(moving):
                rest = [b for b in cand if b not in chosen]
                far = sorted(rest, key=lambda b: -min(abs(b[0] - c[0]) + abs(b[1] - c[1]) for c in chosen))
                chosen.append(rng.choice(far[:max(1, len(far) // 4)]))
            for w, (bx, by) in zip(moving, chosen):
                q = g.unit_q[w]
                lad[w] = (by * 2 + (q >> 1)) * W + bx * 2 + (q & 1)
        m2 = corridors_and_draw(g, pieces, rng, log, ladders=lad)
        if m2 is None: continue
        moves = (sum(bp[2]['moves'] for bp, _, _ in pieces), sum(bp[2].get('submoves', 0) for bp, _, _ in pieces),
                 sum(bp[2].get('fewest_submoves', 0) for bp, _, _ in pieces))
        why = g.check(m2)
        if why:
            if log: log(f'layout {lay}: {why}')
            continue
        return m2, moves
    return None


class _NoLayout(Exception):
    """the corridors cannot join the pieces, or the room cannot be drawn."""


def corridors_and_draw(g, pieces, rng, log=None, iters=4000, ladders=None):
    """the free blocks outside the pieces: corridors joining both ladders and every porch (no
    pushing), as long a walk between them as a tidy layout allows; then the map is drawn.
    None (with the reason sent to log) if that fails."""
    try:
        room = _Room(g, pieces, ladders)
        room.anneal(rng, iters)
        room.place_people(rng)
        return room.draw(rng)
    except _NoLayout as e:
        if log: log(str(e))
        return None


class _Room:
    """A boulder room being laid out on cells: the pieces stamped in (kind), the corridor cells
    between them (corr; grid holds their current tiles) and the stairs and porches to join."""

    def __init__(self, g, pieces, ladders=None):
        self.g = g
        W = self.W = g.W
        free = set(g.free)
        kind = dict(g.top)
        pcells, forced, porches = set(), {}, []
        self.holes_at, self.boulders_at = {}, {}
        extras_left = list(g.extras)
        walk_p = set()
        for (pc, st, info), (bx0, by0), side in pieces:
            gg, hcell = pc.grid(st)
            ox, oy = bx0 * 2 - 1, by0 * 2 - 1
            for (x, y), k in gg.items():
                c = (y + oy) * W + x + ox
                kind[c] = 'W' if k == 'H' else k; pcells.add(c)
                if k != '#': walk_p.add(c)
            for w, (x, y) in hcell.items(): self.holes_at[w] = (x + ox, y + oy)
            for w, (x, y) in st['boulders'].items():
                if isinstance(w, tuple): bi = extras_left.pop(0)          # a boulder with no hole
                else: bi = next(i for i, ww in g.pairs if ww == w)
                self.boulders_at[bi] = (x + ox, y + oy)
            dx, dy = side
            dr = st['door']; porch = (dr[0] + ox + dx, dr[1] + oy + dy)
            porches.append(porch[1] * W + porch[0])
        # rock just outside the piece wherever its floor reaches its edge (its own rock edges need none)
        for c in walk_p:
            x, y = c % W, c // W
            for n in (c - 1 if x > 0 else -1, c + 1 if x < W - 1 else -1, c - W, c + W):
                if 0 <= n < W * g.H and n not in pcells: forced[n] = '#'
        for c in porches: forced[c] = '.'
        # moving stairs: their own block, as in vanilla, in the corridor space
        self.lad = lad = dict(ladders if ladders is not None else g.fixed_wcell)
        self.lad_blk = lad_blk = {}
        for w, c in lad.items():
            b = ((c % W) // 2, (c // W) // 2)
            if b in free:
                lad_blk[b] = w
                for q in range(4):
                    cc = (b[1] * 2 + (q >> 1)) * W + b[0] * 2 + (q & 1)
                    k = g.unit_pat[w][q]
                    kind[cc] = 'W' if k == 'W' else ('.' if k == '.' else '#')
                    if cc in forced and kind[cc] != forced[cc]: raise _NoLayout('a ladder against a piece')
                    forced[cc] = kind[cc] if kind[cc] != 'W' else '#'
        self.corr = sorted(c for c in range(W * g.H) if ((c % W) // 2, (c // W) // 2) in free and c not in pcells
                           and ((c % W) // 2, (c // W) // 2) not in lad_blk)
        self.var = [c for c in self.corr if c not in forced]
        self.grid = dict(kind)
        for c in self.corr: self.grid[c] = forced.get(c, '.')
        self.ladders = list(lad.values())
        self.targets = self.ladders + porches
        self.cblocks = sorted({((c % W) // 2, (c // W) // 2) for c in self.corr})
        for b, w in lad_blk.items():
            for q in range(4):
                cc = (b[1] * 2 + (q >> 1)) * W + b[0] * 2 + (q & 1)
                self.grid[cc] = kind[cc]
        self.free, self.pcells, self.porches, self.forced = free, pcells, porches, forced

    def blk_ok(self, bx, by):
        """can the tileset draw block (bx, by) as the grid has it?"""
        W = self.W
        p = tuple(self.grid[(by * 2 + (q >> 1)) * W + bx * 2 + (q & 1)] for q in range(4))
        return p in self.g.pats

    def walk(self, c):
        return self.grid.get(c) == '.' and c not in self.pcells or c in self.porches

    def bfs(self, src):
        """steps from src over corridor floor (stairs are reached but not walked through)."""
        W, H, ladders = self.W, self.g.H, self.ladders
        dist = {src: 0}; q = deque([src])
        while q:
            a = q.popleft()
            x = a % W
            for n in (a - 1 if x > 0 else -1, a + 1 if x < W - 1 else -1, a - W, a + W):
                if 0 <= n < W * H and n not in dist and (self.walk(n) or n in ladders):
                    dist[n] = dist[a] + 1
                    if n not in ladders: q.append(n)
        return dist

    def score(self):
        """(score, errors): errors count undrawable blocks and unjoined stairs, porches and floor;
        the score also rewards a long walk between them and punishes open 2x2 floor."""
        W, grid = self.W, self.grid
        e = sum(2 for b in self.cblocks if not self.blk_ok(*b))
        d0 = self.bfs(self.ladders[0])
        e += 10 * sum(1 for t in self.targets if t not in d0)
        fl = [c for c in self.corr if grid[c] == '.']
        e += 0.5 * sum(1 for c in fl if c not in d0)
        opn = sum(1 for c in fl if grid.get(c + 1) == '.' and grid.get(c + W) == '.' and grid.get(c + W + 1) == '.')
        walkv = sum(min(d0.get(t, 0), 60) for t in self.targets)
        return -10 * e + 0.3 * walkv - 0.6 * opn, e

    def anneal(self, rng, iters):
        """simulated annealing over the corridor tiles; keeps the best layout with no errors."""
        W, grid, var, g = self.W, self.grid, self.var, self.g
        cur, ce = self.score()
        best = (cur, ce, dict(grid))
        varset = set(var)
        vblocks = sorted({((c % W) // 2, (c // W) // 2) for c in var})
        def bcells(b): return [(b[1] * 2 + (q >> 1)) * W + b[0] * 2 + (q & 1) for q in range(4)]
        for it in range(iters):
            T = 4.0 * (1 - it / iters) + 0.1
            if rng.random() < 0.4 and vblocks:
                # a whole block to another drawable shape that keeps its forced tiles
                b = rng.choice(vblocks); cs = bcells(b)
                opts = [p for p in g.pats if all(cc in varset or grid.get(cc) == k for cc, k in zip(cs, p))]
                if not opts: continue
                p = rng.choice(opts)
                olds = [(cc, grid[cc]) for cc in cs if cc in varset]
                for cc, k in zip(cs, p):
                    if cc in varset: grid[cc] = k
            else:
                c = rng.choice(var)
                olds = [(c, grid[c])]; grid[c] = '#' if grid[c] == '.' else '.'
            sc, e = self.score()
            if sc >= cur or rng.random() < math.exp((sc - cur) / T):
                cur, ce = sc, e
                if e == 0 and sc > best[0]: best = (sc, e, dict(grid))
            else:
                for cc, k in olds: grid[cc] = k
        if best[1] != 0: raise _NoLayout('corridors: no layout joins everything')
        self.grid = best[2]

    def place_people(self, rng):
        """people in the room (Blackthorn's trainers: walls, they never move) stand in the
        corridors: never beside the stairs, never shutting anything off, and each can be walked up
        to. Sets self.people_at {object index: cell}."""
        W, grid, ladders, g = self.W, self.grid, self.ladders, self.g
        self.people_at = {}
        if not g.people: return
        stair_near = set(ladders) | {n for c in ladders for n in (c - 1, c + 1, c - W, c + W)}
        cand = [c for c in self.corr if grid[c] == '.' and c not in self.forced and c not in stair_near
                and c not in self.porches]
        rng.shuffle(cand)
        occ = set()
        for i in g.people:
            for c in cand:
                if c in occ: continue
                grid[c] = '#'
                d0 = self.bfs(ladders[0])
                ok = all(t in d0 for t in self.targets) and \
                     all(cc in d0 for cc in self.corr if grid[cc] == '.') and \
                     any(n in d0 for n in (c - 1, c + 1, c - W, c + W))
                grid[c] = '.'
                if ok:
                    occ.add(c); self.people_at[i] = c; grid[c] = '#'; break
            else:
                raise _NoLayout('no room for a person')
        for c in occ: grid[c] = '.'

    def draw(self, rng):
        """the map: hole and stair blocks as vanilla's, the rest from the room's own blocks by
        shape; warps, boulders and people moved."""
        import copy
        W, grid, g = self.W, self.grid, self.g
        m2 = copy.deepcopy(g.base)
        out = bytearray(m2.blocks)
        hole_blk = {}
        for w, (x, y) in self.holes_at.items():
            hole_blk[(x // 2, y // 2)] = w
            m2.warps[w].x, m2.warps[w].y = x, y
        for w, c in self.lad.items():
            m2.warps[w].x, m2.warps[w].y = c % W, c // W
        for (bx, by) in sorted(self.free, key=lambda b: (b[1], b[0])):
            i = by * g.Wb + bx
            if (bx, by) in hole_blk: out[i] = g.unit_blk[hole_blk[(bx, by)]]; continue
            if (bx, by) in self.lad_blk: out[i] = g.unit_blk[self.lad_blk[(bx, by)]]; continue
            p = tuple(grid[(by * 2 + (q >> 1)) * W + bx * 2 + (q & 1)] for q in range(4))
            p = tuple('#' if k == '#' else '.' for k in p)
            left = out[i - 1] if bx > 0 else None
            up = out[i - g.Wb] if by > 0 else None
            blk = None
            for cat in (g.own_cat, g.cat):
                if cat.has(p):
                    blk = cat.choose(p, left, up, rng, ok=lambda b, cat=cat, p=p: g.plain(cat, b, p) and b in g.allowed)
                    if blk is not None: break
            if blk is None: raise _NoLayout(f'no block draws {p}')
            out[i] = blk
        m2.blocks = bytes(out)
        for i, (x, y) in self.boulders_at.items():
            m2.objects[i].x, m2.objects[i].y = x, y
        for i, c in self.people_at.items():
            m2.objects[i].x, m2.objects[i].y = c % W, c // W
        return m2
