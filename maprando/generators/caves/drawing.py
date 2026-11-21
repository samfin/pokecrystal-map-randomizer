"""Drawing the plan with real blocks: grass and headbutt trees, then block choice."""
from collections import defaultdict
from maprando.generators.caves.common import D4, GenError


class DrawingMixin:
    """Drawing the cell plan with the tileset's real blocks."""
    def tables(self):
        """encounter tables for this map: Surf / grass / headbutt."""
        return {k: self.m.const in v for k, v in self.d.wild_maps.items()}

    def decorate(self, rng):
        """tall grass and headbutt trees, painted onto the finished plan.
        Neither changes where you can walk, so logic is untouched."""
        P, Zc = self.P, self.Zc
        W, H = len(P[0]), len(P)
        mm, zg = self.mm, self.zg
        tab = self.tables()
        locked = {(x, y) for (bx, by) in self.lock for x in (bx*2, bx*2+1) for y in (by*2, by*2+1)}
        # --- tall grass: each zone gets about as much as it had in vanilla
        if tab['grass']:
            vg, lg = defaultdict(int), defaultdict(int)
            for c, z in zg.zone_of.items():
                if mm.coll[c] in (0x18, 0x14):
                    vg[z] += 1
                    if (c % mm.w, c // mm.w) in locked: lg[z] += 1
            for z in self.Z:
                want = round(max(0, vg[z] - lg[z]) / 4 * rng.uniform(0.7, 1.2))
                if want <= 0: continue
                cand = [b for b in self.region[z]
                        if all(P[b[1]*2 + (q >> 1)][b[0]*2 + (q & 1)] == '.' for q in range(4))]
                cs = set(cand); done = set()
                while len(done) < want and cs - done:
                    seed = rng.choice(sorted(cs - done)); front = [seed]
                    while front and len(done) < want:
                        b = front.pop(rng.randrange(len(front)))
                        if b in done: continue
                        done.add(b)
                        for dx, dy in D4:
                            n = (b[0] + dx, b[1] + dy)
                            if n in cs and n not in done and rng.random() < 0.8: front.append(n)
                for (bx, by) in done:
                    for q in range(4): P[by*2 + (q >> 1)][bx*2 + (q & 1)] = ','
            if not any(P[y][x] == ',' for y in range(H) for x in range(W)) and any(vg[z] for z in self.Z):
                # every grass zone ended up without free open blocks: plant one patch anywhere open
                cand = [b for z in self.Z if self.kind[z] == 'land' for b in self.region[z]
                        if all(P[b[1]*2 + (q >> 1)][b[0]*2 + (q & 1)] == '.' for q in range(4))]
                if cand:
                    bx, by = rng.choice(cand)
                    for q in range(4): P[by*2 + (q >> 1)][bx*2 + (q & 1)] = ','
        # --- headbutt trees: only on maps with a headbutt table, on walls facing open ground
        if tab['headbutt']:
            van = sum(1 for by in range(self.Hb) for bx in range(self.Wb)
                      if (bx, by) not in self.lock and 'H' in self.cat.pattern[self.m.blocks[by*self.Wb + bx]])
            want = max(1, round(van * rng.uniform(0.7, 1.2)))
            cand = []
            for by in range(self.Hb):
                for bx in range(self.Wb):
                    gg = self.G[by][bx]
                    if gg == 'LOCK' or (isinstance(gg, tuple) and gg[0] == 'DEV'): continue
                    cells = [(bx*2 + (q & 1), by*2 + (q >> 1)) for q in range(4)]
                    pat = tuple(P[y][x] for x, y in cells)
                    if '#' not in pat: continue
                    newp = tuple('H' if c == '#' else c for c in pat)
                    if not self.cat.has(newp): continue
                    faces = any(0 <= x+dx < W and 0 <= y+dy < H and P[y+dy][x+dx] in ('.', ',')
                                for x, y in cells if P[y][x] == '#' for dx, dy in D4)
                    if faces: cand.append((bx, by, cells))
            rng.shuffle(cand)
            for bx, by, cells in cand[:want]:
                for x, y in cells:
                    if P[y][x] == '#': P[y][x] = 'H'
            # block-aligned corridors have no half walls: turn a solid wall block beside a
            # corridor into a half block of trees whose open half faces the path (an alcove)
            short = want - min(want, len(cand))
            if short > 0:
                alc = []
                for by in range(self.Hb):
                    for bx in range(self.Wb):
                        if self.G[by][bx] is not None: continue
                        cells = [(bx*2 + (q & 1), by*2 + (q >> 1)) for q in range(4)]
                        if any(P[y][x] != '#' or (x, y) in locked or (x, y) in self.fixed for x, y in cells): continue
                        for dy, pat in ((1, ('H', 'H', '.', '.')), (-1, ('.', '.', 'H', 'H'))):
                            if not self.cat.has(pat): continue
                            ry = by*2 + (1 if dy == 1 else 0)          # the row that opens
                            fy = ry + dy                               # the corridor row it faces
                            if not (0 <= fy < H): continue
                            zs = {Zc[fy][x] for x in (bx*2, bx*2+1) if P[fy][x] in ('.', ',')}
                            if len(zs) != 1 or any(P[fy][x] not in ('.', ',') for x in (bx*2, bx*2+1)): continue
                            z = next(iter(zs))
                            if z is None or self.kind.get(z) != 'land': continue
                            # the opened cells' other neighbours: wall or the same zone only
                            ok = True
                            for x in (bx*2 - 1, bx*2 + 2):
                                if 0 <= x < W and (P[ry][x] not in ('#', 'H') and Zc[ry][x] != z): ok = False
                            if ok: alc.append((bx, by, ry, dy, z))
                rng.shuffle(alc)
                for bx, by, ry, dy, z in alc[:short]:
                    hy = by*2 + (0 if dy == 1 else 1)
                    for x in (bx*2, bx*2+1):
                        P[ry][x] = '.'; Zc[ry][x] = z; P[hy][x] = 'H'

    def pick_blocks(self, rng):
        """a block for every free block of the plan whose cells match it (locked blocks keep
        vanilla's): the map's new block data."""
        P = self.P
        out = bytearray(self.Wb * self.Hb)
        W, H = len(P[0]), len(P)
        self.blk_ok = {}; self.blk_pat = {}
        for by in range(self.Hb):
            for bx in range(self.Wb):
                i = by * self.Wb + bx
                if self.G[by][bx] == 'LOCK':
                    out[i] = self.m.blocks[i]; continue
                if i in self.cut_ids:
                    out[i] = self.cut_ids[i]; continue
                if isinstance(self.G[by][bx], tuple) and self.G[by][bx][0] == 'WB':
                    out[i] = self.G[by][bx][2]; continue
                pat = tuple(P[by*2 + (q >> 1)][bx*2 + (q & 1)] for q in range(4))
                def ok(b, bx=bx, by=by):
                    for q, ws in enumerate(self.cat.walls[b]):
                        x, y = bx*2 + (q & 1), by*2 + (q >> 1)
                        for dname in ws:
                            dx, dy = {'R': (1, 0), 'L': (-1, 0), 'D': (0, 1), 'U': (0, -1)}[dname]
                            if 0 <= x+dx < W and 0 <= y+dy < H and P[y+dy][x+dx] not in ('#', 'V', 'H'): return False
                    return True
                left = out[i-1] if bx > 0 else None
                up = out[i-self.Wb] if by > 0 else None
                if self.route and self.cursed:
                    b = self._cursed_block(pat, ok, rng)
                    if b is not None:
                        out[i] = b; self.blk_pat[i] = pat; self.blk_ok[i] = ok; continue
                if self.route and pat == ('#',) * 4 and self.cat.deep:
                    # solid wall on a route: the forest block vanilla uses (cliffs, fences and
                    # signs scattered through corridor walls look like noise)
                    deep = [b for b, _ in self.cat.deep.most_common(3) if b not in self.cat.fake_floor]
                    if deep:
                        out[i] = deep[0] if rng.random() < 0.85 or len(deep) == 1 else rng.choice(deep)
                        self.blk_pat[i] = pat; self.blk_ok[i] = (lambda b: True); continue
                b = self.cat.choose(pat, left, up, rng, ok, self.freq_w)
                if b is None and 'V' in pat:
                    pat = tuple('#' if c == 'V' else c for c in pat)
                    b = self.cat.choose(pat, left, up, rng, ok, self.freq_w)
                if b is None and '#' in pat:
                    p0 = pat
                    pat = tuple('V' if c == '#' else c for c in p0)
                    b = self.cat.choose(pat, left, up, rng, ok, self.freq_w)
                    if b is None:
                        # forests draw walls with headbutt trees: same collision for walking
                        pat = tuple('H' if c in ('#', 'V') else c for c in p0)
                        b = self.cat.choose(pat, left, up, rng, ok, self.freq_w)
                self.blk_pat[i] = pat
                if b is None: raise GenError(f'no block for pattern {pat}')
                out[i] = b
                self.blk_ok[i] = ok
        if not (self.route and self.cursed): self.refine_blocks(out, rng)
        self.m2.blocks = bytes(out)

    def _cursed_block(self, pat, ok, rng):
        """cursed mode: any block of the tileset with exactly this collision (and side walls that
        fit), whatever it looks like - houses, fences, water and flowers in a patchwork. Cells that
        nothing reaches ('V') may be wall or floor. None if no block fits."""
        raw = [tuple('#' if c == 'V' else c for c in pat), tuple('.' if c == 'V' else c for c in pat)]
        cs = [b for r in dict.fromkeys(raw) for b in self.cat.by_pattern.get(r, ()) if ok(b)]
        return rng.choice(cs) if cs else None

    def refine_blocks(self, out, rng, passes=3):
        """re-pick every free block given all four neighbours (iterated conditional modes).
        Candidates keep the exact collision pattern already chosen, so logic is unaffected."""
        Wb, Hb = self.Wb, self.Hb
        order = [i for i in range(Wb * Hb) if self.G[i // Wb][i % Wb] != 'LOCK' and i not in self.cut_ids
                 and not (isinstance(self.G[i // Wb][i % Wb], tuple) and self.G[i // Wb][i % Wb][0] == 'WB')]
        for _ in range(passes):
            rng.shuffle(order)
            changed = 0
            for i in order:
                bx, by = i % Wb, i // Wb
                cur = out[i]
                pat = self.cat.pattern[cur]
                # candidates: blocks with the same raw collision pattern and the same usage pattern
                upat = self.blk_pat[i]
                cs = [b for b in self.cat.cands.get(upat, []) if self.cat.pattern[b] == pat and self.blk_ok[i](b)]
                if len(cs) < 2: continue
                nb = {'L': out[i-1] if bx > 0 else None, 'R': out[i+1] if bx + 1 < Wb else None,
                      'U': out[i-Wb] if by > 0 else None, 'D': out[i+Wb] if by + 1 < Hb else None}
                best = max(cs, key=lambda b: self.cat.score(b, upat, nb, self.freq_w) + rng.random() * 0.2)
                if best != cur: out[i] = best; changed += 1
            if not changed: break
