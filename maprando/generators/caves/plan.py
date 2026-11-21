"""The cell plan: blocks turned into cells, then eroded, smoothed and sealed at the edges."""
from collections import deque
from maprando.generators.caves.common import D4, GenError, LEDGE_CELLS, LEDGE_PATTERNS, NORM


class PlanMixin:
    """The cell plan (self.P): the block grid turned into cells, then roughened and sealed."""
    def build_plan(self, rng):
        """the cell plan of the placed zones: every zone block as floor or water, gate devices and
        locked blocks as in vanilla, walls elsewhere (self.P, zone per cell in self.Zc)."""
        W, H = self.Wb * 2, self.Hb * 2
        P = [['#'] * W for _ in range(H)]
        Zc = [[None] * W for _ in range(H)]
        self.fixed = set()   # cells that must not be eroded
        self.boulder_clear = set()   # cells a pushed boulder lands on: no objects there
        self.cut_ids = {}    # block index -> fixed cut-tree block id
        cat = self.cat
        for by in range(self.Hb):
            for bx in range(self.Wb):
                g = self.G[by][bx]
                cells = [(bx * 2 + (q & 1), by * 2 + (q >> 1)) for q in range(4)]
                if g is None: continue
                if g == 'LOCK':
                    pat = cat.pattern[self.m.blocks[by * self.Wb + bx]]
                    for q, (x, y) in enumerate(cells):
                        P[y][x] = NORM.get(pat[q], pat[q]); Zc[y][x] = self.zg.zone_of.get(self.mm.idx(x, y)); self.fixed.add((x, y))
                    continue
                if g[0] == 'DEV' and g[1] == 'cut':
                    _, _, _, d, src, dst, blk, pa = g
                    pat = [NORM.get(c, c) for c in cat.pattern[blk]]
                    for q, (x, y) in enumerate(cells):
                        P[y][x] = pat[q]; self.fixed.add((x, y))
                    # open cells take the zone of the side they face (src side is -d)
                    SIDES = {(0, -1): (0, 1), (0, 1): (2, 3), (-1, 0): (0, 2), (1, 0): (1, 3)}
                    for q, (x, y) in enumerate(cells):
                        if P[y][x] != '.': continue
                        Zc[y][x] = src if q in SIDES[(-d[0], -d[1])] else dst
                    self.cut_ids[by * self.Wb + bx] = blk
                    continue
                if g[0] == 'WB':
                    _, z, bid = g
                    pat = cat.pattern[bid]
                    cave = bid in getattr(self, 'cave_bids', ()) and (bx, by) in self.warp_blocks
                    for q, (x, y) in enumerate(cells):
                        c = pat[q]
                        if cave and c == '.': c = '#'      # the cliff top above a cave mouth
                        P[y][x] = '#' if c == 'W' else NORM.get(c, c)    # warp tile: not walkable floor for planning
                        Zc[y][x] = z if c == '.' else None
                        self.fixed.add((x, y))
                    continue
                if g[0] == 'Z':
                    z = g[1]
                    ch = '~' if self.kind[z] == 'water' else '.'
                    for x, y in cells: P[y][x] = ch; Zc[y][x] = z
                    continue
                _, t, ends, d, src, dst, obj, pa = g
                for x, y in cells: self.fixed.add((x, y))
                if t == 'gate' and obj is not None and getattr(obj, 'movement', '') == 'SPRITEMOVEDATA_STRENGTH_BOULDER' \
                        or (t == 'gate' and obj is not None and getattr(obj, 'sprite', '') == 'SPRITE_BOULDER'):
                    # a boulder is pushed through into the block on the far side, from either side:
                    # both end blocks stay fully open (no erosion) and free of objects
                    for ex, ey in ends:
                        for q in range(4):
                            c = (ex * 2 + (q & 1), ey * 2 + (q >> 1))
                            self.fixed.add(c); self.boulder_clear.add(c)
                if t == 'ledge':
                    hop = d if src == pa else (-d[0], -d[1])
                    if hop not in LEDGE_PATTERNS: raise GenError('bad ledge direction')
                    pat = LEDGE_PATTERNS[hop]
                    for q, (x, y) in enumerate(cells): P[y][x] = pat[q]
                    for q in LEDGE_CELLS[hop]: Zc[cells[q][1]][cells[q][0]] = pa
                elif t == 'waterfall':
                    for x, y in cells: P[y][x] = 'v'
                elif t == 'gate':
                    if d[0] != 0:   # horizontal passage on the top row
                        pas = [cells[0], cells[1]]
                    else:           # vertical passage on the left column
                        pas = [cells[0], cells[2]]
                    for x, y in pas: P[y][x] = '.'
                    # object on the cell nearer the source side
                    o_cell = pas[0] if (d[0] > 0 or d[1] > 0) else pas[1]
                    far = pas[1] if o_cell == pas[0] else pas[0]
                    Zc[far[1]][far[0]] = dst
                    if obj is not None and hasattr(obj, 'kind'): P[o_cell[1]][o_cell[0]] = '#'   # hidden item in the wall
                    self.gate_cells.append((o_cell, obj))
        # neighbours of devices/locks are fixed too (keep contact cells intact)
        for (x, y) in list(self.fixed):
            for dx, dy in D4:
                if 0 <= x+dx < W and 0 <= y+dy < H: self.fixed.add((x+dx, y+dy))
        self.P, self.Zc = P, Zc

    def erode(self, rng, p_side=0.6, p_corner=0.35, check_only=False):
        """roughen the zones' edges: wall tiles set into the floor along sides and corners where the
        tileset can still draw the block (check_only: only check that every block can be
        drawn)."""
        P, Zc = self.P, self.Zc
        W, H = len(P[0]), len(P)
        def legal(bx, by):
            pat = tuple(P[by*2 + (q >> 1)][bx*2 + (q & 1)] for q in range(4))
            return self.drawable(pat)
        mmw = self.mm.w
        locked_xy = {(x, y) for (bx, by) in self.lock for x in (bx*2, bx*2+1) for y in (by*2, by*2+1)}
        jumps = {}   # vanilla moves between locked cells that are not plain steps (ledges, waterfalls)
        for c, es in self.cell_edges.items():
            a = (c % mmw, c // mmw)
            if a not in locked_xy: continue
            for dcell, r in es:
                b = (dcell % mmw, dcell // mmw)
                if b in locked_xy and abs(a[0]-b[0]) + abs(a[1]-b[1]) > 1:
                    jumps.setdefault(a, set()).add(b); jumps.setdefault(b, set()).add(a)
        def connected(z):
            cells = [(x, y) for y in range(H) for x in range(W) if Zc[y][x] == z and P[y][x] not in ('#', 'V')]
            if not cells: return False
            seen = {cells[0]}; st = [cells[0]]; cs = set(cells)
            while st:
                x, y = st.pop()
                for n in [(x+dx, y+dy) for dx, dy in D4] + list(jumps.get((x, y), ())):
                    if n in cs and n not in seen: seen.add(n); st.append(n)
            return len(seen) == len(cells)
        keep = self._route_contacts(locked_xy) if self.route else set()
        def makes_stub(cells):
            # cursed routes: walling cells must not leave a one-tile dead end beside them, which
            # would look like a hidden item's nook
            near = {(x + dx, y + dy) for x, y in cells for dx, dy in D4}
            for x, y in near:
                if not (0 <= x < W and 0 <= y < H) or P[y][x] != '.': continue
                if sum(1 for dx, dy in D4 if 0 <= x+dx < W and 0 <= y+dy < H and P[y+dy][x+dx] not in ('#', 'V')) == 1:
                    return True
            return False
        if check_only:
            for z in self.Z:
                if any(Zc[y][x] == z for y in range(H) for x in range(W) if P[y][x] != '#') and not connected(z):
                    raise GenError(f'plan zone disconnected')
            return
        for z in self.Z:
            if self.kind[z] != 'land': continue
            for (bx, by) in self.region[z]:
                ops = []
                for dx, dy in D4:
                    nx, ny = bx + dx, by + dy
                    if self.inb(nx, ny) and self.G[ny][nx] is not None: continue
                    if rng.random() < p_side:
                        if dx == 1: ops.append([(1, 0), (1, 1)])
                        if dx == -1: ops.append([(0, 0), (0, 1)])
                        if dy == 1: ops.append([(0, 1), (1, 1)])
                        if dy == -1: ops.append([(0, 0), (1, 0)])
                for qx, qy in ((0, 0), (1, 0), (0, 1), (1, 1)):
                    if rng.random() < p_corner: ops.append([(qx, qy)])
                for op in ops:
                    cells = [(bx*2 + qx, by*2 + qy) for qx, qy in op]
                    if any(c in self.fixed or c in keep or P[c[1]][c[0]] != '.' for c in cells): continue
                    # only erode towards walls
                    outward = all(any(not (0 <= c[0]+dx < W and 0 <= c[1]+dy < H) or P[c[1]+dy][c[0]+dx] == '#'
                                      for dx, dy in D4) for c in cells)
                    if not outward: continue
                    old = [(c, P[c[1]][c[0]]) for c in cells]
                    for x, y in cells: P[y][x] = '#'
                    floor_left = sum(1 for q in range(4) if P[by*2+(q>>1)][bx*2+(q&1)] != '#')
                    if floor_left < 1 or not legal(bx, by) or (keep and makes_stub(cells)) or not connected(z):
                        for (x, y), v in old: P[y][x] = v
        for z in self.Z:
            if not connected(z) and any(Zc[y][x] == z for y in range(H) for x in range(W) if P[y][x] != '#'):
                raise GenError('erosion broke a zone')

    def smooth(self, rng, passes=3):
        """Cellular-automaton cleanup of land zones: fill notches and specks, shave spikes.
        Never touches fixed cells, never joins zones that must stay apart, keeps every zone
        connected, keeps Surf contacts, and only produces block patterns the tileset has."""
        P, Zc = self.P, self.Zc
        W, H = len(P[0]), len(P)
        surf = [(a, b) for t, a, b, _ in self.pairs if t == 'surf']
        locked = {(x, y) for (bx, by) in self.lock for x in (bx*2, bx*2+1) for y in (by*2, by*2+1)}
        N8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
        def inb(x, y): return 0 <= x < W and 0 <= y < H
        def is_z(x, y, z): return inb(x, y) and P[y][x] == '.' and Zc[y][x] == z
        def legal(x, y):
            bx, by = x // 2, y // 2
            pat = tuple(P[by*2 + (q >> 1)][bx*2 + (q & 1)] for q in range(4))
            return self.drawable(pat)
        def simple(x, y, z):
            # removing (x,y) keeps its same-zone neighbours connected inside the 3x3 window
            pts = [(x+dx, y+dy) for dx, dy in N8 if is_z(x+dx, y+dy, z)]
            if not pts: return False
            ps = set(pts); seen = {pts[0]}; st = [pts[0]]
            while st:
                a, b = st.pop()
                for dx, dy in D4:
                    n = (a+dx, b+dy)
                    if n in ps and n not in seen: seen.add(n); st.append(n)
            return len(seen) == len(ps)
        def contact(a, b):
            for y in range(H):
                for x in range(W):
                    if Zc[y][x] == a and P[y][x] != '#':
                        for dx, dy in D4:
                            if inb(x+dx, y+dy) and Zc[y+dy][x+dx] == b and P[y+dy][x+dx] != '#': return True
            return False
        for _ in range(passes):
            cells = [(x, y) for y in range(H) for x in range(W) if (x, y) not in self.fixed and (x, y) not in locked]
            rng.shuffle(cells)
            changed = False
            for x, y in cells:
                c = P[y][x]
                if c == '.':
                    z = Zc[y][x]
                    if z is None or self.kind.get(z) != 'land': continue
                    same8 = sum(1 for dx, dy in N8 if is_z(x+dx, y+dy, z))
                    same4 = sum(1 for dx, dy in D4 if is_z(x+dx, y+dy, z))
                    if same8 > 2 and same4 > 1: continue
                    if not simple(x, y, z): continue
                    P[y][x] = '#'; Zc[y][x] = None
                    ok = legal(x, y)
                    if ok:
                        for a, b in surf:
                            if z in (a, b) and any(inb(x+dx, y+dy) and Zc[y+dy][x+dx] == (b if z == a else a) for dx, dy in D4):
                                if not contact(a, b): ok = False; break
                    if not ok: P[y][x] = '.'; Zc[y][x] = z
                    else: changed = True
                elif c == '#':
                    zs = {Zc[y+dy][x+dx] for dx, dy in D4 if inb(x+dx, y+dy) and P[y+dy][x+dx] == '.'}
                    if len(zs) != 1: continue
                    z = next(iter(zs))
                    if z is None or self.kind.get(z) != 'land': continue
                    same8 = sum(1 for dx, dy in N8 if is_z(x+dx, y+dy, z))
                    if same8 < 5: continue
                    # every walkable 4-neighbour must be z or a permitted Surf partner
                    bad = False
                    for dx, dy in D4:
                        if not inb(x+dx, y+dy): continue
                        v = P[y+dy][x+dx]
                        if v in ('#', 'V'): continue
                        z2 = Zc[y+dy][x+dx]
                        if z2 != z and (z2 is None or not self.contact_ok(z, z2)): bad = True; break
                    if bad: continue
                    P[y][x] = '.'; Zc[y][x] = z
                    if not legal(x, y): P[y][x] = '#'; Zc[y][x] = None
                    else: changed = True
            if not changed: break

    def voidify(self, dist=2):
        """walls far from anything walkable become void (vanilla's unreachable filler)."""
        P = self.P; W, H = len(P[0]), len(P)
        from collections import deque
        INF = 99; D = [[INF] * W for _ in range(H)]
        q = deque()
        for y in range(H):
            for x in range(W):
                if P[y][x] not in ('#', 'V') or (x, y) in self.fixed and P[y][x] != '#':
                    D[y][x] = 0; q.append((x, y))
        while q:
            x, y = q.popleft()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < W and 0 <= ny < H and D[ny][nx] > D[y][x] + 1:
                        D[ny][nx] = D[y][x] + 1; q.append((nx, ny))
        locked = {(x, y) for (bx, by) in self.lock for x in (bx*2, bx*2+1) for y in (by*2, by*2+1)}
        for y in range(H):
            for x in range(W):
                if P[y][x] == '#' and D[y][x] >= dist and (x, y) not in locked: P[y][x] = 'V'

    def seal_edges(self):
        """the outermost tiles of a connected edge that are wall in vanilla stay wall (only the
        openings lead into the neighbouring map)."""
        W = self.Wb * 2
        for c in getattr(self, 'edge_walls', ()):
            x, y = c % W, c // W
            if (x // 2, y // 2) in self.lock: continue          # kept as in vanilla (and unreachable there)
            if self.P[y][x] != '#':
                self.P[y][x] = '#'
                if hasattr(self, 'Zc'): self.Zc[y][x] = None

    def _route_contacts(self, locked_xy):
        """floor cells that route erosion (cursed mode) leaves alone: those touching anything but
        wall or their own zone's floor (ledges, water, gates, doorways into locked blocks, other
        zones) and the tiles signs are read from. Caves keep their contacts in self.fixed."""
        P, Zc = self.P, self.Zc
        W, H = len(P[0]), len(P)
        out = set()
        for y in range(H):
            for x in range(W):
                if P[y][x] != '.': continue
                for dx, dy in D4:
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx < W and 0 <= ny < H): continue
                    v = P[ny][nx]
                    if v == '#' and (nx, ny) not in locked_xy: continue
                    if v == '.' and Zc[ny][nx] == Zc[y][x] and (nx, ny) not in locked_xy: continue
                    if v == '#': continue
                    out.add((x, y)); break
        for i, (x, y) in getattr(self, 'sign_place', {}).items():
            u = next((u for u in self.sign_units.values() if u[0].index == i), None)
            if u is not None: out.add((x + u[3][0], y + u[3][1]))
        # the pockets corridors() made for items and moving warps keep their shape
        for bx, by in getattr(self, 'pockets', ()):
            out |= {(bx*2 + (q & 1), by*2 + (q >> 1)) for q in range(4)}
        return out

