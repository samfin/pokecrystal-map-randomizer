"""Ledge loops: merging empty ledge steps, splitting an area into one-way parts, long ledges."""
from maprando.generators.caves.common import D4, LEDGE_PATTERNS
from maprando.model.movement import DIRS, LEDGE_DIRS, classify


class LedgeMixin:
    """Ledge loops: empty ledge steps merged, areas split into parts joined by one-way ledges, long
    ledges."""
    def merge_ledge_steps(self):
        """an area that is only a step between ledges (A drops into B, B drops only into C) and holds
        nothing (no person, item, sign, warp, door, trigger, Fly spot) is merged into C: whatever
        reached B reached C anyway, and nothing in B needed reaching, so the requirements stay the
        same while the map loses a pointless ledge. Repeated until none is left. Returns the
        merges [(B, C)]."""
        zg = self.zg
        merged = []
        # every vanilla link between areas, one-way, with what it needs (gate people included)
        edges = {(a, b, r) for a, b, r in zg.edges}
        while True:
            used = set()
            for zs in self.anchor_zones.values(): used |= set(zs)
            for d_ in ('warp_units', 'door_units'):
                used |= {zz for (_ws, _bid, zz) in getattr(self, d_, {}).values()}
            used |= set(getattr(self, 'warp_spot_zone', {}).values())
            used |= {z for g in getattr(self, 'multi_gates', []) for z in g[1:3]}
            pick = None
            for B in sorted(self.Z):
                if self.kind.get(B) != 'land' or B in used: continue
                outs = set()
                ok = True
                for t, a, b, _r in self.pairs:
                    if t == 'ledge':
                        if a == B: outs.add(b)
                    elif B in (a, b):
                        ok = False; break         # any two-way link (walk, surf, gate...) out of B
                if not ok or len(outs) != 1: continue
                C = next(iter(outs))
                if C == B or self.kind.get(C) != 'land': continue
                # and in vanilla too: every way out of B is a free drop into C
                if any(a == B and (b != C or r != 0) for a, b, r in edges): continue
                pick = (B, C); break
            if pick is None: return merged
            B, C = pick
            zg.zones[C] = list(zg.zones[C]) + list(zg.zones[B])
            for c in zg.zones[B]: zg.zone_of[c] = C
            del zg.zones[B]
            self.Z = [z for z in self.Z if z != B]
            self.size[C] = len(zg.zones[C]); self.size.pop(B, None); self.kind.pop(B, None)
            pairs, seen = [], set()
            for t, a, b, r in self.pairs:
                a, b = (C if a == B else a), (C if b == B else b)
                if a == b or (t, a, b, r) in seen: continue
                seen.add((t, a, b, r)); pairs.append((t, a, b, r))
            self.pairs = pairs
            edges = {((C if a == B else a), (C if b == B else b), r) for a, b, r in edges}
            edges = {e for e in edges if e[0] != e[1]}
            if hasattr(self, '_zreq'): self._zreq = None
            for c_ in ('_lsz', '_comp_memo', '_surf_pairs'):
                self.__dict__.pop(c_, None)
            merged.append(pick)

    def split_zone(self, rng):
        """ledge loops, on maps whose vanilla has them: one land area is split into k parts
        joined only by one-way ledges in a loop (A -> B -> ... -> A), so every part still reaches
        every other (the requirements stay the same) but crossing the area means dropping down a
        ledge and going round to the next one to get back. Used sparingly: only on maps where
        vanilla has many ledges inside one area (one-way shortcuts: Route 4, 9, 2, 1, 26, 29...),
        one area per map, k = 2 to 6 parts (the cap grows with vanilla's ledges and the area's
        room; the top of the range most often), plus 1 to k-2 extra one-way ledges."""
        mm, zg = self.mm, self.zg
        if not self.cat.has(LEDGE_PATTERNS[(0, 1)]): return None
        inner = 0
        for c in range(mm.w * mm.h):
            cA = mm.coll[c]
            if (cA & 0xF0) != 0xA0: continue
            x, y = c % mm.w, c // mm.w
            for dd in LEDGE_DIRS[cA & 7]:
                dx, dy = DIRS[dd]
                if mm.inb(x + 2 * dx, y + 2 * dy) and zg.zone_of.get(c) is not None and \
                   zg.zone_of.get(c) == zg.zone_of.get(mm.idx(x + 2 * dx, y + 2 * dy)): inner += 1
        if inner < 10: return None
        # how many parts: 2 up to 6, more where vanilla has more ledges (one more per 8 such tiles
        # past 10), fewer if a try with more found nothing (split_cap); the area's free room caps
        # it below (each part needs 30 free tiles). More ledges is better: the top of the range
        # is picked most often.
        kmax = min(6, 2 + (inner - 10) // 8, getattr(self, 'split_cap', None) or 6)
        def blk(c): return ((c % mm.w) // 2, (c // mm.w) // 2)
        bad = set()
        for t, a, b, _ in self.pairs:
            if t not in ('ledge', 'surf', 'waterfall', 'gate'): bad |= {a, b}
        for aid, zs in self.anchor_zones.items():
            if len(zs) > 1: bad |= zs
        bad |= set(getattr(self, 'objz', {}))
        # (locked tiles keep their vanilla shape and simply belong to the part they fall in)
        cands = [z for z in self.Z if self.kind.get(z) == 'land' and z not in bad and
                 sum(1 for c in zg.zones[z] if blk(c) not in self.lock) >= 80]
        if not cands: return None
        z = max(cands, key=lambda q: (len(zg.zones[q]), rng.random()))
        nfree = sum(1 for c in zg.zones[z] if blk(c) not in self.lock)
        kmax = max(2, min(kmax, nfree // 40))
        k = rng.randint(max(2, kmax - 1), kmax)
        # split it into k stretches along the map's long side: locked pieces of the area
        # (connected locked tiles) are sorted along that axis and grouped into k bands, and the
        # free tiles go to the nearest band (parts that interleave cannot be joined up)
        cells = list(zg.zones[z]); cs = set(cells)
        axis = 0 if mm.w >= mm.h else 1
        def pos(c): return (c % mm.w, c // mm.w)[axis]
        lockc = {c for c in cells if blk(c) in self.lock}
        pieces, seen = [], set()
        for c0 in sorted(lockc):
            if c0 in seen: continue
            comp, st = [c0], [c0]; seen.add(c0)
            while st:
                c = st.pop(); x, y = c % mm.w, c // mm.w
                for dx, dy in D4:
                    if mm.inb(x + dx, y + dy):
                        n = mm.idx(x + dx, y + dy)
                        if n in lockc and n not in seen: seen.add(n); comp.append(n); st.append(n)
            pieces.append(comp)
        freec = [c for c in cells if c not in lockc]
        lo, hi = min(pos(c) for c in cells), max(pos(c) for c in cells)
        # band boundaries: equal shares of the free tiles along the axis
        fs = sorted(pos(c) for c in freec)
        cuts = [fs[len(fs) * i // k] for i in range(1, k)]
        def band(c):
            p_ = pos(c); return sum(1 for t in cuts if p_ >= t)
        part = {}
        for comp in pieces:
            mid = sorted(pos(c) for c in comp)[len(comp) // 2]
            i = sum(1 for t in cuts if mid >= t)
            for c in comp: part[c] = i
        for c in freec: part[c] = band(c)
        # every part must have free room
        if any(sum(1 for c in freec if part[c] == i) < 30 for i in range(k)): return None
        for c in cells: part.setdefault(c, 0)
        new = [z] + [max(zg.zones) + 1 + i for i in range(k - 1)]
        for i, nz in enumerate(new):
            zg.zones[nz] = [c for c in cells if part[c] == i]
            for c in zg.zones[nz]: zg.zone_of[c] = nz
        self.Z = sorted(set(self.Z) | set(new))
        for nz in new:
            self.kind[nz] = 'land'; self.size[nz] = len(zg.zones[nz])
        # what was in the area goes to the part whose cells it touches (else any part)
        for aid, zs in self.anchor_zones.items():
            if z not in zs: continue
            a = next((a for a in mm.anchors if a.id == aid), None)
            got = {zg.zone_of.get(c) for c in (a.cells if a else [])} & set(new)
            self.anchor_zones[aid] = {next(iter(got)) if got else rng.choice(new)}
        # links to other areas: to a random part
        self.pairs = [(t, rng.choice(new) if a == z else a, rng.choice(new) if b == z else b, r)
                      for t, a, b, r in self.pairs]
        for d_ in ('warp_units', 'door_units'):
            units = getattr(self, d_, {})
            for blk_, (ws, bid, zz) in list(units.items()):
                if zz == z:
                    got = {zg.zone_of.get(mm.idx(w.x, w.y + 1)) for w in ws} & set(new)
                    units[blk_] = (ws, bid, next(iter(got)) if got else rng.choice(new))
        if hasattr(self, 'warp_spot_zone'):
            for wi, zz in list(self.warp_spot_zone.items()):
                if zz == z: self.warp_spot_zone[wi] = zg.zone_of.get(mm.idx(self.m.warps[wi].x, self.m.warps[wi].y), z)
        # the loop of ledges
        order = new[:]; rng.shuffle(order)
        for i in range(k):
            self.pairs.append(('ledge', order[i], order[(i + 1) % k], 0))
        # more ledges: one-way shortcuts between other parts (any extra edge keeps the loop
        # strongly connected), 1 to k-2 of them
        if k >= 3:
            have = {(order[i], order[(i + 1) % k]) for i in range(k)}
            spare = [(a, b) for a in new for b in new if a != b and (a, b) not in have]
            rng.shuffle(spare)
            for a, b in spare[:rng.randint(1, k - 2)]:
                self.pairs.append(('ledge', a, b, 0)); have.add((a, b))
        if hasattr(self, '_zreq'): self._zreq = None
        # caches built from the area ids before the split
        for c_ in ('_lsz', '_comp_memo', '_surf_pairs'):
            self.__dict__.pop(c_, None)
        return dict(zone=z, parts=k, sizes=[len(zg.zones[nz]) for nz in new], ids=new)

    def widen_ledges(self, rng):
        """the ledges of a ledge loop are drawn as long ledges, not one-block gates: each loop
        ledge grows sideways (1-3 blocks in all) where the ground on both sides can grow with it,
        so you see a long drop with open ground above and below, as in vanilla's Route 9."""
        info = getattr(self, 'split_info', None)
        if not info: return
        ids = set(info.get('ids', ()))
        G = self.G
        def dev_ok(n, run):
            g = G[n[1]][n[0]]
            return not (isinstance(g, tuple) and g[0] == 'DEV') or n in run
        def side_free(B, z, S):
            g = G[B[1]][B[0]]
            if g == ('Z', z): return 'have'
            if g is not None: return None
            # claimable once S is a ledge: S lists B among its ends
            for dx, dy in D4:
                n = (B[0] + dx, B[1] + dy)
                if not self.inb(*n) or n == S: continue
                gn = G[n[1]][n[0]]
                if gn is None: continue
                if gn == 'LOCK':
                    if any(z2 != z for z2 in self.lock_side_zones(n[0], n[1], (-dx, -dy))): return None
                elif gn[0] == 'DEV': return None
                elif gn[0] == 'Z' and gn[1] != z and not self.contact_ok(gn[1], z): return None
            return 'claim'
        for t, pos, d, src, dst, obj in list(self.devices):
            if t != 'ledge' or src not in ids or dst not in ids: continue
            g = G[pos[1]][pos[0]]
            _, _, ends, d, src, dst, obj, pa = g
            hop = d if src == pa else (-d[0], -d[1])
            za, zb = pa, (dst if src == pa else src)
            perp = (hop[1], hop[0])
            target = rng.choice((1, 2, 2, 3))       # blocks wide: a drop, not a wall of ledges
            run = [pos]
            sides = [perp, (-perp[0], -perp[1])]; rng.shuffle(sides)
            grown = True
            while len(run) < target and grown:
                grown = False
                for s in sides:
                    if len(run) >= target: break
                    end = max(run, key=lambda b: b[0] * s[0] + b[1] * s[1])
                    S = (end[0] + s[0], end[1] + s[1])
                    if not (1 <= S[0] < self.Wb - 1 and 1 <= S[1] < self.Hb - 1): continue
                    if G[S[1]][S[0]] is not None: continue
                    if not all(dev_ok((S[0] + dx, S[1] + dy), run) for dx, dy in D4 if self.inb(S[0] + dx, S[1] + dy)): continue
                    US, LS = (S[0] - hop[0], S[1] - hop[1]), (S[0] + hop[0], S[1] + hop[1])
                    if not (self.inb(*US) and self.inb(*LS)): continue
                    if US in self.reserved or LS in self.reserved: continue
                    fa, fb = side_free(US, za, S), side_free(LS, zb, S)
                    if not (fa and fb): continue
                    # the block beyond the new end stays clear of the two parts' floors (a zone
                    # floor beside the ledge row is fine: ledges are walls from the side)
                    G[S[1]][S[0]] = ('DEV', 'ledge', {US, LS}, d, src, dst, obj, pa)
                    self.devices.append(('ledge', S, d, src, dst, obj))
                    if fa == 'claim': self.claim(US[0], US[1], za)
                    if fb == 'claim': self.claim(LS[0], LS[1], zb)
                    self.reserved.discard(S)
                    run.append(S); grown = True

    def loop_ok(self, mm2):
        """the parts of a ledge loop stay apart on foot: no plain walk joins two of them."""
        info = getattr(self, 'split_info', None)
        if not info or not hasattr(self, 'Zc'): return None
        ids = set(info.get('ids', ()))
        W, H = mm2.w, mm2.h
        def land(c): return classify(mm2, mm2.coll[c]) == 'land' and mm2.objects.get(c, ('',))[0] != 'wall'
        lab = {}
        for c0 in range(W * H):
            if c0 in lab or not land(c0): continue
            comp, st, seen_parts = [c0], [c0], set()
            lab[c0] = c0
            while st:
                c = st.pop(); x, y = c % W, c // W
                z = self.Zc[y][x] if y < len(self.Zc) and x < len(self.Zc[0]) else None
                if z in ids: seen_parts.add(z)
                for dx, dy in D4:
                    if 0 <= x + dx < W and 0 <= y + dy < H:
                        n = (y + dy) * W + x + dx
                        if n not in lab and land(n): lab[n] = c0; st.append(n)
            if len(seen_parts) > 1:
                return f'ledge loop parts {sorted(seen_parts)} joined on foot near {(c0 % W, c0 // W)}'
        return None

    def ledge_close(self, lock):
        """a ledge tile in a locked block keeps the tiles you jump from, over and onto (two tiles
        on) locked with it, to a fixed point (locks added while generating included)."""
        mm = self.mm
        changed = True
        while changed:
            changed = False
            for (bx, by) in list(lock):
                for q in range(4):
                    x0, y0 = bx * 2 + (q & 1), by * 2 + (q >> 1)
                    if not mm.inb(x0, y0): continue
                    c = mm.idx(x0, y0)
                    if (mm.coll[c] & 0xF0) != 0xA0: continue
                    for d in LEDGE_DIRS[mm.coll[c] & 7]:
                        ddx, ddy = DIRS[d]
                        for k in (-1, 1, 2):
                            if mm.inb(x0 + k * ddx, y0 + k * ddy):
                                n = ((x0 + k * ddx) // 2, (y0 + k * ddy) // 2)
                                if n not in lock: lock.add(n); changed = True
