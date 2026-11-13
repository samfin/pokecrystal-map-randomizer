"""One-tile dead ends: every hidden item sits at the tip of one, and no other nook is left."""
from maprando.generators.caves.common import D4


class NookMixin:
    """One-tile dead ends for hidden items, and no stray ones elsewhere."""
    def fill_stray_nooks(self, m2, locked_cells):
        """a one-cell nook must mean 'hidden item here': fill every other one."""
        P = self.P; W, H = len(P[0]), len(P)
        keep = {(o.x, o.y) for o in m2.objects} | {(b.x, b.y) for b in m2.bg_events} | set(locked_cells)
        keep |= {(w.x, w.y) for w in m2.warps}
        dev = set()
        for by in range(self.Hb):
            for bx in range(self.Wb):
                g = self.G[by][bx]
                if isinstance(g, tuple) and g[0] == 'DEV':
                    dev |= {(bx*2 + (q & 1), by*2 + (q >> 1)) for q in range(4)}
        def walk(x, y): return 0 <= x < W and 0 <= y < H and P[y][x] not in ('#', 'V')
        changed = True; rounds = 0
        while changed and rounds < 6:
            changed = False; rounds += 1
            for y in range(H):
                for x in range(W):
                    if P[y][x] != '.' or (x, y) in keep or (x, y) in dev: continue
                    nb = [(x+dx, y+dy) for dx, dy in D4 if walk(x+dx, y+dy)]
                    if len(nb) != 1: continue
                    if any(P[y+dy][x+dx] not in ('.', '#', 'V') for dx, dy in D4 if 0 <= x+dx < W and 0 <= y+dy < H): continue
                    if any(n in dev for n in nb): continue
                    near = [(x+dx, y+dy) for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)]
                    if any(0 <= a < W and 0 <= b < H and (P[b][a] in ('v',) or P[b][a].startswith('L')) for a, b in near):
                        # probably a ledge landing: widen it instead of filling it
                        z = self.Zc[y][x]
                        fx, fy = nb[0][0] - x, nb[0][1] - y          # direction of the way out
                        for dx, dy in ((fy, fx), (-fy, -fx)):        # sideways only
                            a, b = x + dx, y + dy
                            if not (0 <= a < W and 0 <= b < H) or P[b][a] not in ('#', 'V') or (a, b) in locked_cells: continue
                            # the new cell must also open onto the same floor, so neither cell is a nook
                            if not (0 <= a+fx < W and 0 <= b+fy < H) or P[b+fy][a+fx] != '.' or self.Zc[b+fy][a+fx] != z: continue
                            nbs = [(a+ex, b+ey) for ex, ey in D4 if 0 <= a+ex < W and 0 <= b+ey < H
                                   and (a+ex, b+ey) not in ((x, y), (a+fx, b+fy))]
                            if any(P[nb_][na] not in ('#', 'V') for na, nb_ in nbs): continue
                            olds = [(na, nb_, P[nb_][na]) for na, nb_ in nbs if P[nb_][na] == 'V']
                            for na, nb_, _ in olds: P[nb_][na] = '#'
                            P[b][a] = '.'
                            blks = {(a // 2, b // 2)} | {(na // 2, nb // 2) for na, nb, _ in olds}
                            if all(self.cat.has(tuple(P[q2y*2 + (q >> 1)][q2x*2 + (q & 1)] for q in range(4))) for q2x, q2y in blks):
                                self.Zc[b][a] = z; changed = True; break
                            P[b][a] = '#'
                            for na, nb_, v in olds: P[nb_][na] = v
                        continue
                    old = P[y][x]
                    P[y][x] = '#'
                    bx, by = x // 2, y // 2
                    pat = tuple(P[by*2 + (q >> 1)][bx*2 + (q & 1)] for q in range(4))
                    if not self.cat.has(pat): P[y][x] = old; continue
                    self.Zc[y][x] = None; changed = True

    def _drawable(self, bx, by):
        P = self.P
        pat = tuple(P[by*2 + (q >> 1)][bx*2 + (q & 1)] for q in range(4))
        # walls of a nook must stay solid: wall or headbutt trees, never passable void
        return any(self.cat.has(p) for p in (pat, tuple('H' if c == '#' else c for c in pat)))

    def make_nook(self, z, used, locked_cells, rng, terrain='.'):
        """Turn a floor tile of zone z into a nook tip by walling up all but one of its open
        neighbours (only neighbours of z that nothing needs: not fixed, not locked, not in use),
        keeping zone z in one piece and every block drawable. Prefers tiles at the end of the
        dead-end branches made for items. Returns the tip or None."""
        P, Zc = self.P, self.Zc
        W, H = len(P[0]), len(P)
        kc = getattr(self, 'keep_clear', set())
        pk = getattr(self, 'pockets', set())
        def inb(x, y): return 0 <= x < W and 0 <= y < H
        dev = self._device_cells()
        def open_(x, y): return inb(x, y) and P[y][x] not in ('#', 'V', 'H')
        zone = {(x, y) for y in range(H) for x in range(W) if Zc[y][x] == z and open_(x, y)}
        def pieces(cells):
            k, seen = 0, set()
            for c0 in cells:
                if c0 in seen: continue
                k += 1; st = [c0]; seen.add(c0)
                while st:
                    cx, cy = st.pop()
                    for dx, dy in D4:
                        n = (cx + dx, cy + dy)
                        if n in cells and n not in seen: seen.add(n); st.append(n)
            return k
        base = pieces(zone)
        cands = []
        for (x, y) in zone:
            if P[y][x] != terrain or (x, y) in used or (x, y) in kc or (x, y) in locked_cells: continue
            nbs = [(x + dx, y + dy) for dx, dy in D4 if open_(x + dx, y + dy)]
            if any(Zc[ny][nx] != z for nx, ny in nbs): continue
            if any((x + dx, y + dy) in dev for dx, dy in D4): continue    # a cut tree beside it
            cands.append(((x, y) not in pk and (x // 2, y // 2) not in pk, len(nbs), rng.random(), (x, y), nbs))
        cands.sort()
        for _, _, _, f, nbs in cands[:60]:
            for keep in sorted(nbs, key=lambda c: rng.random()):
                walls = [c for c in nbs if c != keep]
                if any(c in self.fixed or c in locked_cells or c in used or c in kc for c in walls): continue
                # the kept neighbour must lead on, and z must stay in one piece
                if sum(1 for dx, dy in D4 if open_(keep[0] + dx, keep[1] + dy)) < 2: continue
                if pieces(zone - set(walls)) > base: continue
                olds = [(c, P[c[1]][c[0]], Zc[c[1]][c[0]]) for c in walls]
                for (cx, cy) in walls: P[cy][cx] = '#'; Zc[cy][cx] = None
                blks = {(c[0] // 2, c[1] // 2) for c in walls + [f, keep]}
                if all(self._drawable(bx, by) for bx, by in blks):
                    used.add(keep)            # keep the cell in front free of NPCs
                    return f
                for (cx, cy), pv, zv in olds: P[cy][cx] = pv; Zc[cy][cx] = zv
        return None

    def water_nook(self, z, used, locked_cells, rng):
        """A water tile next to land zone z becomes a dead end: its other water neighbours are
        walled up (unlocked, unused, keeping every water zone in one piece). The item sits on the
        water tile and is picked up from the land in front of it. Returns the tile or None."""
        P, Zc = self.P, self.Zc
        W, H = len(P[0]), len(P)
        kc = getattr(self, 'keep_clear', set())
        def inb(x, y): return 0 <= x < W and 0 <= y < H
        def open_(x, y): return inb(x, y) and P[y][x] not in ('#', 'V', 'H')
        def pieces(cells):
            k, seen = 0, set()
            for c0 in cells:
                if c0 in seen: continue
                k += 1; st = [c0]; seen.add(c0)
                while st:
                    cx, cy = st.pop()
                    for dx, dy in D4:
                        n = (cx + dx, cy + dy)
                        if n in cells and n not in seen: seen.add(n); st.append(n)
            return k
        cands = []
        for y in range(H):
            for x in range(W):
                if P[y][x] != '~' or (x, y) in locked_cells or (x, y) in used or (x, y) in kc: continue
                lands = [(x + dx, y + dy) for dx, dy in D4 if open_(x + dx, y + dy) and P[y + dy][x + dx] == '.'
                         and Zc[y + dy][x + dx] == z and (x + dx, y + dy) not in used and (x + dx, y + dy) not in kc]
                if not lands: continue
                cands.append((rng.random(), (x, y), lands))
        cands.sort()
        for _, (x, y), lands in cands[:80]:
            for front in sorted(lands, key=lambda c: rng.random()):
                walls = [(x + dx, y + dy) for dx, dy in D4 if open_(x + dx, y + dy) and (x + dx, y + dy) != front]
                if any(P[c[1]][c[0]] != '~' or c in self.fixed or c in locked_cells or c in used or c in kc for c in walls): continue
                zw = Zc[y][x]
                water = {(i, j) for j in range(H) for i in range(W) if Zc[j][i] == zw and P[j][i] == '~'}
                if water - {(x, y)} - set(walls) and pieces(water - {(x, y)} - set(walls)) > pieces(water): continue
                olds = [(c, P[c[1]][c[0]], Zc[c[1]][c[0]]) for c in walls + [(x, y)]]
                for (cx, cy) in walls: P[cy][cx] = '#'; Zc[cy][cx] = None
                Zc[y][x] = z
                blks = {(c[0] // 2, c[1] // 2) for c in walls + [(x, y), front]}
                if all(self._drawable(bx, by) for bx, by in blks):
                    used.add(front)
                    return (x, y)
                for (cx, cy), pv, zv in olds: P[cy][cx] = pv; Zc[cy][cx] = zv
        return None

    def _device_cells(self):
        """cells of gate device blocks (cut trees, whirlpools): wall in the plan, open once cleared"""
        out = set()
        for by, row in enumerate(self.G):
            for bx, g in enumerate(row):
                if isinstance(g, tuple) and g[0] == 'DEV':
                    out |= {(bx*2 + (q & 1), by*2 + (q >> 1)) for q in range(4)}
        return out

    def carve_nook(self, z, used, locked_cells, rng, terrain='.', tip=None):
        """Cut a one-cell nook into a wall bordering zone z and return its cell. The hidden item
        goes at the nook's tip and is picked up by facing it from the cell in front.
        Smoothing removes every natural one-cell spike, so in a generated cave a one-cell nook
        always marks a hidden item."""
        P, Zc = self.P, self.Zc
        W, H = len(P[0]), len(P)
        def inb(x, y): return 0 <= x < W and 0 <= y < H
        cands = []
        dev = self._device_cells()
        for y in range(H):
            for x in range(W):
                if P[y][x] != '#' or (x, y) in locked_cells or (x, y) in used: continue
                if (x, y) in getattr(self, 'keep_clear', ()): continue
                # walls next to kept blocks are fine (the nook's other sides stay wall), but not
                # cells of gate devices or moved warp blocks, whose graphics are fixed
                g = self.G[y // 2][x // 2]
                if g is not None and not (isinstance(g, tuple) and g[0] == 'Z'): continue
                fronts, ok = [], True
                for dx, dy in D4:
                    nx, ny = x + dx, y + dy
                    if not inb(nx, ny): continue
                    v = P[ny][nx]
                    if (nx, ny) in dev: ok = False; break      # a cut tree beside it: not a dead end once cut
                    if v in ('#', 'V'): continue
                    if v == terrain and Zc[ny][nx] == z: fronts.append((nx, ny)); continue
                    ok = False; break
                if not ok or len(fronts) != 1: continue
                f = fronts[0]
                if f in used or f in getattr(self, 'keep_clear', ()): continue
                # the front cell must be ordinary open floor (not itself a nook or corridor stub)
                fn = sum(1 for dx, dy in D4 if inb(f[0]+dx, f[1]+dy) and P[f[1]+dy][f[0]+dx] == terrain and Zc[f[1]+dy][f[0]+dx] == z)
                if fn < 1: continue
                cands.append(((x, y), f, fn))
        cands.sort(key=lambda c: -min(c[2], 3) + rng.random())
        cands = [(w, f) for w, f, _ in cands]
        for (x, y), f in cands:
            # void touching the nook becomes wall so the nook never opens onto it
            backs = [(x+dx, y+dy) for dx, dy in D4 if inb(x+dx, y+dy) and P[y+dy][x+dx] == 'V']
            for a, b in backs: P[b][a] = '#'
            P[y][x] = tip or terrain; Zc[y][x] = z
            blocks = {(x // 2, y // 2)} | {(a // 2, b // 2) for a, b in backs}
            def drawable(bx, by):
                pat = tuple(P[by*2 + (q >> 1)][bx*2 + (q & 1)] for q in range(4))
                # forests draw walls with headbutt trees: the same nook may exist in that form
                return any(self.cat.has(p) for p in (pat, tuple('H' if c == '#' else c for c in pat)))
            if all(drawable(bx, by) for bx, by in blocks):
                used.add(f)   # keep the cell in front free of NPCs
                return (x, y)
            P[y][x] = '#'; Zc[y][x] = None
            for a, b in backs: P[b][a] = 'V'
        return None
