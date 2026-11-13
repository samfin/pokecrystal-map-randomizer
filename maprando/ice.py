"""Ice rooms: open ice with scattered rocks, arranged so crossing takes as many slides as possible.

Stepping onto ice slides you until the next tile blocks you (you stop on the ice) or you reach
a tile that is not ice. An ice room is a connected set of all-ice blocks (rocks allowed); it keeps
its outline and the vanilla tiles around it, and only the rocks inside are redesigned:

  - each block is plain ice or ice with one rock in one corner (the only ice blocks the tileset has)
  - simulated annealing moves rocks to maximise, for every pair of outside areas that the room
    joins in vanilla, the number of slides needed to get from one to the other
  - hard constraints: exactly the vanilla set of (outside area -> outside area) crossings, and no
    trap: every tile you can slide to can still slide back out of the room

Everything outside the rooms stays vanilla; the full requirement matrix is checked at the end."""
import copy, math, random
from collections import defaultdict, deque
from .model import build_model, classify, COLL_ICE, COLL_PIT, DIRS
from .reach import requirement_matrix
from .jumps import bad_jumps

D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIRNAME = {v: k for k, v in DIRS.items()}   # (dx, dy) -> direction constant

class IceError(Exception): pass

class IceGenerator:
    # objective 'shape': path_w * (slides on the quickest routes) - corner_cost * corners
    #                    - dead_end_cost * dead ends - rock_cost * rocks
    objective = 'shape'       # or 'decisions': the number of nontrivial slides (Ice Path 1F)
    path_w = 1.0
    corner_cost = 2.0    # per rock-made corner (an L-shaped pocket)
    dead_end_cost = 4.0  # per rock-made dead end (a U-shaped pocket)
    rock_cost = 0.15
    spread = False       # no two placed rocks in any 2x2 square (no rocks touching, even diagonally)
    edge_trivial = False # a stop is a decision only if two ways on end somewhere but the room's edge
    def __init__(self, disasm, m, perms, catalog=None):
        self.d, self.m, self.perms = disasm, m, perms
        self.mm = build_model(disasm, m, perms)
        self.vmm = self.mm
        self.vtraps = len(traps(self.mm))
        self.matrix = requirement_matrix(self.mm)
        self.vjumps = {(c, d) for c, d, _ in bad_jumps(self.mm)}   # ledge jumps into walls vanilla already has
        coll = disasm.tileset_coll[m.tileset]
        mm = self.mm
        def cls(c):
            if c == COLL_ICE: return 'I'
            return '#' if classify(mm, c) is None and not (0x70 <= c <= 0x7f or c == COLL_PIT) else '?'
        # canonical block per ice pattern: the one vanilla uses most
        use = defaultdict(int)
        for mp in disasm.maps.values():
            if mp.tileset == m.tileset:
                for b in mp.blocks: use[b] += 1
        self.block_of = {}
        for b, q in enumerate(coll):
            p = ''.join(cls(c) for c in q)
            if p.count('I') >= 3 and set(p) <= {'I', '#'}:
                if p not in self.block_of or use[b] > use[self.block_of[p]]: self.block_of[p] = b
        self.options = [p for p in ('IIII', '#III', 'I#II', 'II#I', 'III#') if p in self.block_of]
        if 'IIII' not in self.block_of: raise IceError('no plain ice block')
        self._rooms()

    # ------------------------------------------------------------------ rooms
    def _rooms(self):
        m, mm = self.m, self.mm
        W, Wb, Hb = mm.w, m.width, m.height
        busy = set()                                   # cells holding events
        for w in m.warps: busy.add((w.x, w.y))
        for e in m.coord_events: busy.add((e.x, e.y))
        for e in m.bg_events: busy.add((e.x, e.y))
        from .jumps import ledge_cells
        for q in ledge_cells(mm): busy.add((q % W, q // W))   # ledges: keep where jumps land
        items = set()
        for o in m.objects:
            busy.add((o.x, o.y))
            items.add((o.x // 2, o.y // 2))
        coll = self.d.tileset_coll[m.tileset]
        pat = {}
        for by in range(Hb):
            for bx in range(Wb):
                q = coll[m.blocks[by * Wb + bx]]
                p = ''.join('I' if c == COLL_ICE else ('#' if classify(mm, c) is None and not (0x70 <= c <= 0x7f or c == COLL_PIT) else '?') for c in q)
                pat[(bx, by)] = p
        def ok(bx, by):
            p = pat[(bx, by)]
            if not (p.count('I') >= 3 and set(p) <= {'I', '#'}): return False
            if any((bx*2 + (q & 1), by*2 + (q >> 1)) in busy for q in range(4)): return False
            return True
        free = {b for b in pat if ok(*b)}
        self.rooms = []
        seen = set()
        for b0 in sorted(free):
            if b0 in seen: continue
            comp, st = [b0], [b0]; seen.add(b0)
            while st:
                bx, by = st.pop()
                for dx, dy in D4:
                    n = (bx + dx, by + dy)
                    if n in free and n not in seen: seen.add(n); comp.append(n); st.append(n)
            if len(comp) >= 4: self.rooms.append(sorted(comp))

    # ------------------------------------------------------------------ simulation
    def _setup(self, room):
        """static data for one room: its cells, the rock collision byte, and the outside areas
        (non-ice floor; ledges are one way, so they split areas)."""
        mm = self.mm; W, H = mm.w, mm.h
        cells = {(bx*2 + (q & 1), by*2 + (q >> 1)) for bx, by in room for q in range(4)}
        self.cells = cells
        self._last_grid = None; self._succ = {}; self._dirs = {}; self._solid = None
        coll = self.d.tileset_coll[self.m.tileset]
        self.rock = next(c for p, b in self.block_of.items() if '#' in p for c in coll[b] if c != COLL_ICE)
        def icy(c): return mm.coll[c] == COLL_ICE or (c % W, c // W) in cells
        self.icy = icy
        # only tiles reachable from some entry in vanilla count (cave filler is walkable but void)
        if not hasattr(self, 'vreach'):
            from .reach import Searcher, entry_starts
            S = Searcher(self.vmm); self.vreach = set()
            for e in self.vmm.anchors:
                if e.is_entry: self.vreach |= set(S.search(entry_starts(self.vmm, e)))
        comp, k = {}, 0
        for c0 in range(W * H):
            if c0 not in self.vreach: continue
            if c0 in comp or icy(c0) or classify(mm, mm.coll[c0]) != 'land' or (mm.coll[c0] & 0xF0) == 0xA0: continue
            k += 1; comp[c0] = k; st = [c0]
            while st:
                c = st.pop(); x, y = c % W, c // W
                if c in mm.warp_cells and mm.coll[c] != 0x70: continue
                for dx, dy in D4:
                    if not (0 <= x + dx < W and 0 <= y + dy < H): continue
                    n = c + dx + dy * W
                    if n in comp or n not in self.vreach or icy(n) or classify(mm, mm.coll[n]) != 'land' or (mm.coll[n] & 0xF0) == 0xA0: continue
                    if n in mm.objects and mm.objects[n][0] == 'wall': continue
                    comp[n] = k; st.append(n)
        self.area = comp
        # hidden items (and signs) checked from a simulated tile: you must come to rest next to
        # them. Those next to plain floor are reached with that floor area and need nothing extra.
        self.target_at = defaultdict(list)
        for b in self.m.bg_events:
            c0 = mm.idx(b.x, b.y)
            nbs = [c0 + dx + dy * W for dx, dy in D4 if 0 <= b.x + dx < W and 0 <= b.y + dy < H]
            if any(n in comp for n in nbs): continue
            for n in nbs:
                if icy(n) or classify(mm, mm.coll[n]) == 'land': self.target_at[n].append(f'bg:{b.index}')
        # everything walkable that is not plain floor (ice, ledges) is simulated move by move
        self.entries = defaultdict(list)          # area -> its cells next to simulated tiles
        for c, a in comp.items():
            x, y = c % W, c // W
            for dx, dy in D4:
                if not (0 <= x + dx < W and 0 <= y + dy < H): continue
                n = c + dx + dy * W
                if n not in comp and (icy(n) or classify(mm, mm.coll[n]) == 'land'):
                    self.entries[a].append(c); break

    def _explore(self, grid, need_moves=True, need_traps=True):
        """grid: {(x,y): 'I' or '#'} for room cells. Uses the real movement model (sliding, ledges,
        warps). Returns (crossings {area_in: {area_out}}, slides {(in, out): min moves} (only with
        need_moves), traps); decisions per crossing / hidden item go to self.last_choices."""
        from .reach import Searcher
        mm = self.mm; W = mm.w
        area = self.area
        # moves from a tile depend only on tiles in its row and column (slides and ledge jumps are
        # straight), so cached moves survive a rock change elsewhere: drop only the rows and
        # columns of tiles that changed since the last call
        last = self.__dict__.get('_last_grid')
        succ = self.__dict__.setdefault('_succ', {})
        dcache = self.__dict__.setdefault('_dirs', {})
        if last is None or set(last) != set(grid):
            succ.clear(); dcache.clear()
        else:
            ch = [k for k, v in grid.items() if last[k] != v]
            if ch:
                # a move from tile c can only depend on a changed tile if nothing solid (in both the
                # old and the new grid) lies between them on c's row or column: walk outward from
                # each changed tile and drop cached moves up to the first such tile
                H = mm.h
                solid = self.__dict__.get('_solid')
                if solid is None:
                    # plain walls only: ledges and one-sided walls can be crossed from some sides
                    solid = self._solid = {c for c in range(W * H) if (c % W, c // W) not in grid
                                           and classify(mm, mm.coll[c]) is None
                                           and (mm.coll[c] & 0xF0) not in (0xA0, 0xB0, 0xC0)
                                           and c not in mm.warp_cells and c not in mm.objects}
                def blocks(x, y):
                    if (x, y) in grid: return grid[(x, y)] == '#' and last[(x, y)] == '#'
                    return (y * W + x) in solid
                drop = set()
                for (x0, y0) in ch:
                    drop.add(y0 * W + x0)
                    for dx, dy in D4:
                        x, y = x0 + dx, y0 + dy
                        while 0 <= x < W and 0 <= y < H and not blocks(x, y):
                            drop.add(y * W + x); x += dx; y += dy
                        # a ledge jump lands across one solid tile: the tile beyond it may jump here
                        x, y = x + dx, y + dy
                        if 0 <= x < W and 0 <= y < H: drop.add(y * W + x)
                for c in drop:
                    succ.pop(c, None); dcache.pop(c, None)
        self._last_grid = dict(grid)
        Sbox = []
        def searcher():
            if not Sbox:
                mm2 = copy.copy(mm); col = list(mm.coll)
                for (x, y), v in grid.items(): col[y * W + x] = COLL_ICE if v == 'I' else self.rock
                mm2.coll = col; Sbox.append(Searcher(mm2))
            return Sbox[0]
        ALL = (1 << 40) - 1
        def nxt(c):
            r = succ.get(c)
            if r is None: r = succ[c] = [t for t, _ in searcher().neighbors(c, ALL)]
            return r
        terminal = self.__dict__.get('_terminal')
        if terminal is None: terminal = self._terminal = Searcher(mm).terminal
        cross = defaultdict(set); slides = {}; reach_all = set()
        if need_moves:
            for a, starts in self.entries.items():
                dist = {}; q = deque()
                for e in starts:
                    for t in nxt(e):
                        if t not in area and t not in dist: dist[t] = 1; q.append(t)
                while q:
                    c = q.popleft()
                    if c in terminal: continue
                    for t in nxt(c):
                        if t not in area:
                            if t not in dist: dist[t] = dist[c] + 1; q.append(t)
                        else:
                            b = area[t]
                            if dist[c] + 1 < slides.get((a, b), 1 << 30): slides[(a, b)] = dist[c] + 1
        # decisions: a move is nontrivial when, where it starts, there are at least two ways to go
        # besides straight back; forced moves (corners) are free. 0-1 BFS over (tile, arrived-by).
        # The same search gives the crossings and every tile you can reach.
        # directions as 0..3 (R, L, D, U); the opposite of d is d ^ 1; states are tile * 4 + d
        def moves(c):
            r = dcache.get(c)
            if r is None:
                out = []
                for t in nxt(c):
                    dx, dy = t % W - c % W, t // W - c // W
                    if (dx == 0) == (dy == 0): continue        # not a straight move
                    di = (0 if dx > 0 else 1) if dx else (2 if dy > 0 else 3)
                    out.append((t, di))
                mask = 0
                for t, di in out:
                    if self.edge_trivial:
                        # a slide that ends against the room's own edge (not one of our rocks, not
                        # off the ice) is hardly a choice: only the others count
                        tx, ty = t % W, t // W
                        ex, ey = tx + (1, -1, 0, 0)[di], ty + (0, 0, 1, -1)[di]
                        if (tx, ty) in grid and grid[(tx, ty)] == 'I' and (ex, ey) not in grid: continue
                    mask |= 1 << di
                # forward options when arriving by each direction: all directions but straight back
                nfwd = tuple(bin(mask & ~(1 << (ad ^ 1))).count('1') for ad in range(4))
                r = dcache[c] = (out, nfwd)
            return r
        choices = {}
        target_at = self.target_at
        BIG = 1 << 30
        for a, starts in self.entries.items():
            best = {}; q = deque()
            for e in starts:
                for t, di in moves(e)[0]:
                    st = t * 4 + di
                    if t not in area and best.get(st, BIG) > 0: best[st] = 0; q.append(st)
            while q:
                st = q.popleft(); k = best[st]
                c = st >> 2; arr = st & 3
                reach_all.add(c)
                tg = target_at.get(c)
                if tg:
                    for x in tg:
                        if k < choices.get((a, x), BIG): choices[(a, x)] = k
                if c in terminal: continue
                ms, nfwd = moves(c)
                forced = nfwd[arr] < 2
                back = arr ^ 1
                for t, di in ms:
                    nk = k if (forced and di != back) else k + 1
                    if t not in area:
                        s2 = t * 4 + di
                        if nk < best.get(s2, BIG):
                            best[s2] = nk
                            (q.appendleft if nk == k else q.append)(s2)
                    else:
                        b = area[t]; cross[a].add(b)
                        kk = (a, b)
                        if nk < choices.get(kk, BIG): choices[kk] = nk
        self.last_choices = choices
        self.last_reach = reach_all
        if not need_traps:
            return {a: frozenset(v) for a, v in cross.items()}, slides, 0
        good = set(); rev = defaultdict(list)
        for c in reach_all:
            if c in terminal: good.add(c); continue
            for t in nxt(c):
                if t not in area: rev[t].append(c)
                else: good.add(c)
        q = deque(good)
        while q:
            t = q.popleft()
            for c in rev[t]:
                if c not in good: good.add(c); q.append(c)
        self.last_reach = reach_all
        return {a: frozenset(v) for a, v in cross.items()}, slides, len(reach_all - good)

    def _traps_only(self):
        """trap count for the grid of the last _explore call (moves are still cached)."""
        area = self.area; succ = self._succ; reach_all = self.last_reach
        terminal = self._terminal
        good = set(); rev = defaultdict(list)
        for c in reach_all:
            if c in terminal: good.add(c); continue
            for t in succ[c]:
                if t not in area: rev[t].append(c)
                else: good.add(c)
        q = deque(good)
        while q:
            t = q.popleft()
            for c in rev[t]:
                if c not in good: good.add(c); q.append(c)
        return len(reach_all - good)

    # ------------------------------------------------------------------ search
    def _grid(self, room, choice):
        g = {}
        for (bx, by), p in zip(room, choice):
            for q in range(4): g[(bx*2 + (q & 1), by*2 + (q >> 1))] = p[q]
        return g

    def shapes(self, g):
        """(corners, dead ends) made by rocks we placed. A corner is an ice tile boxed in on two
        perpendicular sides (an L-shaped pocket, e.g. two rocks touching diagonally): stopping
        there only ever leads to the obvious turn. A dead end is boxed in on three sides: the
        only way out is back. At least one side must be a placed rock; pockets made only by the
        room's own walls do not count."""
        mm = self.mm
        def rock(x, y): return g.get((x, y)) == '#'
        def solid(x, y):
            if (x, y) in g: return g[(x, y)] == '#'
            if not mm.inb(x, y): return True
            return not self.icy(mm.idx(x, y)) and classify(mm, mm.coll[mm.idx(x, y)]) is None
        corners = dead = 0
        for (x, y), v in g.items():
            if v == '#': continue
            sides = [(dx, dy) for dx, dy in D4 if solid(x + dx, y + dy)]
            if not any(rock(x + dx, y + dy) for dx, dy in sides): continue
            if len(sides) >= 3: dead += 1
            elif len(sides) == 2 and sides[0][0] != -sides[1][0] and sides[0][1] != -sides[1][1] or \
                 len(sides) == 2 and (sides[0][0] == 0) != (sides[1][0] == 0): corners += 1
        return corners, dead

    def design(self, room, rng, iters=None, blocks=None):
        self._setup(room)
        coll = self.d.tileset_coll[self.m.tileset]
        blocks = blocks or self.m.blocks
        van = []
        for bx, by in room:
            q = coll[blocks[by * self.m.width + bx]]
            van.append(''.join('I' if c == COLL_ICE else '#' for c in q))
        vcross, vslides, vtraps = self._explore(self._grid(room, van))
        pairs = [(a, b) for a, bs in vcross.items() for b in bs if a != b] or \
                [(a, b) for a, bs in vcross.items() for b in bs]
        vtargets = {k for k in self.last_choices if isinstance(k[1], str)}   # (area, 'bg:i') in vanilla
        pairs += sorted(vtargets)
        def evaluate(choice):
            g = self._grid(room, choice)
            cross, slides, _ = self._explore(g, need_traps=False)   # move counts guide the search
            bad = sum(len(vcross.get(a, frozenset()) ^ cross.get(a, frozenset())) for a in set(vcross) | set(cross))
            # hidden items: reachable from exactly the same floor areas as in vanilla
            bad += len(vtargets ^ {k for k in self.last_choices if isinstance(k[1], str)})
            if self.spread:
                # rocks never share a 2x2 square: no two within one tile of each other
                rocks = {c for c, v in g.items() if v == '#'}
                bad += sum(1 for (x, y) in rocks for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)) if (x + dx, y + dy) in rocks)
            # traps only matter for a layout that is otherwise valid
            traps = self._traps_only() if bad == 0 else 0
            # number of decisions (nontrivial slides) needed for every crossing; moves only break
            # ties; every rock costs a little so only useful rocks stay
            ch = self.last_choices
            if self.objective == 'shape':
                # path length (slides on the quickest route for every crossing) against pockets
                # that make the puzzle dull: corners (forced turns) and dead ends (only way out is back)
                cn, de = self.shapes(g)
                score = (self.path_w * sum(slides.get(k, 0) for k in pairs) - self.corner_cost * cn
                         - self.dead_end_cost * de - self.rock_cost * sum(p != 'IIII' for p in choice))
            else:   # 'decisions'
                score = (sum(ch.get(k, 0) for k in pairs) + 0.02 * sum(slides.get(k, 0) for k in pairs)
                         - self.rock_cost * sum(p != 'IIII' for p in choice)
                         )
            return score - 40 * bad - 40 * max(0, traps - vtraps), bad == 0 and traps <= vtraps, score
        n = len(room)
        iters = iters or min(8000, 120 * n)
        # start from vanilla's rocks (valid by construction) shuffled to other blocks where that
        # stays valid, then anneal; the result shares nothing with vanilla but the outline
        cur = list(van)
        cj, cok, cs = evaluate(cur)
        for _ in range(4 * n):
            i, k = rng.randrange(n), rng.randrange(n)
            a, b = cur[i], rng.choice(self.options)
            cur[i] = b
            j, ok, sc = evaluate(cur)
            if ok: cj, cok, cs = j, ok, sc
            else: cur[i] = a
        best = (cs, list(cur)) if cok else None
        T0, T1 = 4.0, 0.15
        for i in range(iters):
            T = T0 * (T1 / T0) ** (i / iters)
            k = rng.randrange(n)
            old = cur[k]
            cur[k] = rng.choice([o for o in self.options if o != old])
            j, ok, sc = evaluate(cur)
            if j >= cj or rng.random() < math.exp((j - cj) / T):
                cj = j
                if ok and (best is None or sc > best[0]): best = (sc, list(cur))
            else:
                cur[k] = old
        self._explore(self._grid(room, van))
        vscore = (sum(self.last_choices.get(k, 0) for k in pairs), sum(vslides.get(k, 0) for k in pairs))
        self.vshapes = self.shapes(self._grid(room, van))
        if best is not None:   # report (decisions, moves)
            _, sl, _ = self._explore(self._grid(room, best[1]))
            best = ((sum(self.last_choices.get(k, 0) for k in pairs), sum(sl.get(k, 0) for k in pairs)), best[1])
        return best, vscore

    def generate(self, seed, tries=3):
        m2 = copy.deepcopy(self.m)
        blocks = bytearray(m2.blocks)
        self.report = []
        # rooms on one map share the same floor areas, so the quickest crossing can go through
        # any of them: design all of them together as one set of blocks
        groups = [[b for r in self.rooms for b in r]] if self.rooms else []
        for ri, room in enumerate(groups):
            for t in range(tries):
                rng = random.Random(f'{seed}-{self.m.name}-ice{ri}-{t}')
                best, vscore = self.design(room, rng, blocks=bytes(blocks))
                if best is None: continue
                trial = bytearray(blocks)
                for (bx, by), p in zip(room, best[1]):
                    trial[by * self.m.width + bx] = self.block_of[p]
                m3 = copy.deepcopy(m2); m3.blocks = bytes(trial)
                mm3 = build_model(self.d, m3, self.perms)
                if requirement_matrix(mm3) == self.matrix and len(traps(mm3)) <= self.vtraps and \
                        not ({(c, d) for c, d, _ in bad_jumps(mm3)} - self.vjumps):
                    blocks = trial; self.report.append((len(room), vscore, best[0]))
                    self.mm = mm3                      # later rooms see this one as it now is
                    break
            else:
                self.report.append((len(room), None, None))   # room stays vanilla
        m2.blocks = bytes(blocks)
        self.m2 = m2
        if not any(r[2] is not None for r in self.report): raise IceError(f'{self.m.name}: no ice room could be redesigned')
        return m2, 0

def traps(mm):
    """tiles you can reach from some entry but from which no warp or map edge can be reached."""
    from .reach import Searcher, entry_starts
    S = Searcher(mm); ALL = (1 << 40) - 1
    exits = set()
    for e in mm.anchors:
        if e.kind in ('warp', 'edge'): exits |= set(e.cells)
    reach = set()
    for e in mm.anchors:
        if e.is_entry: reach |= set(S.search(entry_starts(mm, e)))
    rev = defaultdict(list)
    for c in reach:
        if c in S.terminal and c not in exits: continue
        for t, _ in S.neighbors(c, ALL): rev[t].append(c)
    good = set(c for c in reach if c in exits); q = deque(good)
    while q:
        t = q.popleft()
        for c in rev[t]:
            if c in reach and c not in good: good.add(c); q.append(c)
    return reach - good

