"""Placing zones on the block grid next to a placed neighbour, with the gate device between them."""
from collections import defaultdict, deque
from maprando.generators.caves.common import D4, GenError, LEDGE_PATTERNS, PairError, PortConflict
from maprando.model.movement import CUT, SURF, WATERFALL


class PlacementMixin:
    """Zones placed on the block grid (self.G), each next to a placed neighbour, with the gate device
    between them."""
    def inb(self, bx, by): return 0 <= bx < self.Wb and 0 <= by < self.Hb

    def contact_ok(self, z1, z2):
        if z1 == z2: return True
        sp = self.__dict__.get('_surf_pairs')
        if sp is None:
            sp = self._surf_pairs = {frozenset((a, b)) for t, a, b, _ in self.pairs if t == 'surf'}
        return frozenset((z1, z2)) in sp

    def lock_side_zones(self, bx, by, side):
        """zones of passable cells of locked block (bx,by) on the given side (dx,dy). Depends only
        on vanilla data fixed by _analyze, so it is cached."""
        cache = self.__dict__.setdefault('_lsz', {})
        key = (bx, by, side)
        r = cache.get(key)
        if r is None:
            r = cache[key] = frozenset(self._lock_side_zones(bx, by, side))
        return r

    def _lock_side_zones(self, bx, by, side):
        mm, zg = self.mm, self.zg
        dx, dy = side
        cells = []
        for q in range(4):
            qx, qy = q & 1, q >> 1
            if (dx == 1 and qx == 1) or (dx == -1 and qx == 0) or (dy == 1 and qy == 1) or (dy == -1 and qy == 0):
                cells.append(mm.idx(bx * 2 + qx, by * 2 + qy))
        zs = set()
        for c in cells:
            z = zg.zone_of.get(c)
            if z is not None and z in self.Z: zs.add(z)
            elif c in self.terminal:
                # a warp cell belongs to the zone(s) you step into from it
                into = {zg.zone_of[d] for d, r in self.cell_edges.get(c, ()) if zg.zone_of.get(d) in self.Z}
                # a door reached only through a gate cell (e.g. behind a guard) must not get a new
                # neighbour that walks straight onto it
                zs |= into or {-1}
            elif z is not None and z in self.objz:
                zs.add(-1)      # a gate object's cell: nothing new may border it
        return zs

    def can_claim(self, bx, by, z):
        """may zone z take free block (bx, by)? Not if it touches a zone it must stay apart from."""
        Wb, Hb, G = self.Wb, self.Hb, self.G
        if not (0 <= bx < Wb and 0 <= by < Hb) or G[by][bx] is not None or (bx, by) in self.reserved: return False
        ok = self.__dict__.get('_ok_contact')
        if ok is None:
            ok = self._ok_contact = defaultdict(set)
            for t, a, b, _ in self.pairs:
                if t == 'surf': ok[a].add(b); ok[b].add(a)
        allowed = ok[z]
        for dx, dy in D4:
            nx, ny = bx + dx, by + dy
            if not (0 <= nx < Wb and 0 <= ny < Hb): continue
            g = G[ny][nx]
            if g is None: continue
            if g == 'LOCK':
                for z2 in self.lock_side_zones(nx, ny, (-dx, -dy)):
                    if z2 != z and z2 not in allowed: return False
            elif g[0] == 'DEV':
                if (bx, by) not in g[2]: return False
            elif g[0] == 'Z':
                if g[1] != z and g[1] not in allowed: return False
        return True

    def side_taken(self, x, y, D):
        """is block (x,y), beside device block D, unusable as its flank? Empty blocks are fine, and
        so are locked blocks that are solid wall on the side facing D."""
        g = self.G[y][x]
        if g is None: return False
        if g == 'LOCK':
            dx, dy = D[0] - x, D[1] - y
            for q in range(4):
                qx, qy = q & 1, q >> 1
                if (dx == 1 and qx == 1) or (dx == -1 and qx == 0) or (dy == 1 and qy == 1) or (dy == -1 and qy == 0):
                    c = self.mm.idx(x * 2 + qx, y * 2 + qy)
                    if c in self.zg.zone_of or c in self.terminal: return True
            return False
        return True

    def claim(self, bx, by, z):
        self.G[by][bx] = ('Z', z)
        self.region[z].append((bx, by))
        # growth frontier: a superset of the free blocks beside z (stale ones are filtered on use)
        f = self._front.setdefault(z, set())
        for dx, dy in D4: f.add((bx + dx, by + dy))

    def frontier(self, z):
        G = self.G
        out = []
        for p in sorted(self._front.get(z, ())):
            if not self.can_claim(p[0], p[1], z): continue
            if any(0 <= p[0] + dx < self.Wb and 0 <= p[1] + dy < self.Hb and G[p[1] + dy][p[0] + dx] == ('Z', z)
                   for dx, dy in D4):
                out.append(p)
        return out

    def place(self, rng):
        """place every zone on the block grid: locked blocks first, then each zone next to a placed
        neighbour through the device of their pair (Surf shore, ledge, gate, waterfall,
        whirlpool). Raises GenError if a zone or pair does not fit."""
        self.G = [[None] * self.Wb for _ in range(self.Hb)]
        self.reserved = set()
        self.region = defaultdict(list)
        self._front = {}
        self._comp_memo = {}
        self.devices = []   # (type, (bx,by), dir, a, b, bits)
        for (bx, by) in self.lock: self.G[by][bx] = 'LOCK'
        placed = self._place_ports()
        # zones only inside locked blocks count as placed
        for z in self.Z:
            if all(((c % self.mm.w) // 2, (c // self.mm.w) // 2) in self.lock for c in self.zg.zones[z]):
                placed.add(z)
        if not placed:
            # every doorway moves: start the zone of the first warp somewhere
            zs = [z for _, (ws, bid, z) in sorted(self.warp_units.items())] or [self.Z[0]]
            if not self._seed_zone(zs[0], placed, rng): raise GenError('no seeded zone')
        pending = [p for p in self.pairs if not self._joined_in_lock(p)]
        rng.shuffle(pending)
        held = defaultdict(int)   # objects / signs that must stand inside one zone
        for aid, zs in self.anchor_zones.items():
            if len(zs) == 1 and (aid.startswith('obj:') or aid.startswith('bg:')): held[next(iter(zs))] += 1
        self.target = {z: max(1, round(self.size[z] / 4 * rng.uniform(0.6, 1.3)), 1 + held[z])
                       for z in self.Z}
        # join zones that start in several pieces (e.g. a corridor ringing a room) while the
        # map is still empty, before anything grows into the space they need
        order = [z for z in self.Z if z in placed]
        rng.shuffle(order)
        apron = {}
        for z in self.Z:
            for (bx, by) in self.region[z]:
                for dx in range(-2, 3):
                    for dy in range(-2, 3):
                        n = (bx + dx, by + dy)
                        if abs(dx) + abs(dy) <= 2 and self.inb(*n) and self.G[n[1]][n[0]] is None:
                            apron[n] = z if apron.get(n, z) == z else -1
        for z in order:
            if len(self.components(z)) > 1: self.unify(z, rng, apron)
        # maps whose small areas keep getting boxed in: grow less before attaching them
        fails = getattr(self, 'attach_fail', 0)
        pre_frac = 0.7 if fails < 10 else 0.3 if fails < 30 else 0.0
        for z in list(placed):
            if z in self.target and pre_frac: self.grow_zone(z, rng, pre_frac)
        self._attach_pending(pending, placed, rng, fails)
        missing = [z for z in self.Z if z not in placed]
        while missing:
            # zones joined to the rest only through warps that now move: seed them on free ground
            if not self._seed_zone(missing[0], placed, rng): raise GenError(f'unplaced zones {missing}')
            self._attach_pending(pending, placed, rng, fails)
            missing = [z for z in self.Z if z not in placed]
        self.cycle_pairs = pending   # both ends were placed independently
        # link them while there is still room between the two ends; retry the rest after growth
        late = []
        for p in self.cycle_pairs:
            if p[0] == 'surf' and self.touches(p[1], p[2]): continue
            if not self.link2(p, rng): late.append(p)
        # land will be thinned into corridors: give it room to wind first
        for z in self.Z:
            if self.kind[z] == 'land': self.target[z] = round(self.target[z] * self.room_now)
        if self.room_now > 1:
            # small areas on a big empty map: claim a share of the free blocks (split by vanilla
            # size) so the corridors have space to wind through, not just vanilla's area x3
            land = [z for z in self.Z if self.kind[z] == 'land']
            tot = sum(self.size[z] for z in land) or 1
            free = self.Wb * self.Hb - len(self.lock)
            for z in land:
                self.target[z] = max(self.target[z], round(self.fill * free * self.size[z] / tot))
        self.grow(rng)
        for z in self.Z:
            self.unify(z, rng)
        for p in late:
            if p[0] == 'surf' and self.touches(p[1], p[2]): continue
            t, a, b, _ = p
            if not self.link2(p, rng):
                raise PairError(p, f'could not connect {p}')

    def _place_ports(self):
        """claim the free blocks beside locked blocks for the zones whose doorways they hold: the
        set of zones placed that way. Raises PortConflict if two zones need the same block."""
        placed = set()
        for (bx, by) in sorted(self.lock):
            for dx, dy in D4:
                nx, ny = bx + dx, by + dy
                if not self.inb(nx, ny) or (nx, ny) in self.lock: continue
                if not self.lock_side_zones(bx, by, (dx, dy)): continue
                # zone of the vanilla neighbour's facing cells
                zs = self.lock_side_zones(nx, ny, (-dx, -dy)) - {-1}
                if not zs: continue
                if len(zs) > 1: raise GenError('port touches several zones')
                z = next(iter(zs))
                if self.G[ny][nx] is None:
                    if not self.can_claim(nx, ny, z): raise PortConflict((nx, ny))
                    self.claim(nx, ny, z)
                elif self.G[ny][nx] != ('Z', z): raise PortConflict((nx, ny))
                placed.add(z)
        return placed

    def _seed_zone(self, z, placed, rng):
        """claim a free block for zone z away from what is placed already. False if none is free."""
        free = [(bx, by) for by in range(self.Hb) for bx in range(self.Wb) if self.can_claim(bx, by, z)]
        if not free: return False
        def far(b):
            return min([abs(b[0] - r[0]) + abs(b[1] - r[1]) for zz in placed for r in self.region[zz]] or [99])
        top = max(far(b) for b in free)
        self.claim(*rng.choice([b for b in free if far(b) >= top - 2]), z)
        placed.add(z); return True

    def _attach_pending(self, pending, placed, rng, fails):
        """link every pending pair with one end placed to its other end, until none is left.
        Raises PairError if a pair cannot be attached."""
        progress = True
        while progress:
            progress = False
            for p in list(pending):
                t, a, b, bits = p
                if (a in placed) == (b in placed): continue
                src, dst = (a, b) if a in placed else (b, a)
                if not self.link(p, src, dst, False, rng):
                    self.attach_fail = fails + 1
                    raise PairError(p, f'could not attach {p}')
                placed.add(a); placed.add(b); pending.remove(p); progress = True
                self.grow_zone(dst, rng, 0.7)

    def _joined_in_lock(self, p):
        """is pair p already realised inside locked blocks (a locked gate zone, gate object or move
        of the right kind between its zones)?"""
        def in_lock(c): return ((c % self.mm.w) // 2, (c // self.mm.w) // 2) in self.lock
        t, a, b, bits = p
        if t == 'touch':
            o = self.touch_objs[(min(a, b), max(a, b))]
            return (o.x // 2, o.y // 2) in self.lock
        # a locked gate zone realises the pair only if going through it costs no more
        # than the pair itself (a locked cut tree is not a free ledge)
        need = {'surf': SURF, 'ledge': 0, 'touch': 0, 'gate': bits, 'waterfall': WATERFALL | SURF,
                'whirl': bits | SURF, 'xgate': bits}[t]
        gz = [(g, r) for g, r in self.objz_pairs.get((a, b), ())]
        if t in ('surf', 'gate', 'touch', 'whirl', 'xgate'):
            gz += [(g, r) for g, r in self.objz_pairs.get((b, a), ())]
        if any(r & ~need == 0 and
               all(((c % self.mm.w) // 2, (c // self.mm.w) // 2) in self.lock for c in self.zg.zones[g])
               for g, r in gz):
            return True
        if t in ('whirl', 'xgate'): return False
        if t == 'gate':
            # a gate object that stays in a locked block already joins a and b
            for cell, (kind, obits, o) in self.mm.objects.items():
                if kind == 'bits' and obits == bits and (o.x // 2, o.y // 2) in self.lock:
                    if {a, b} <= self.anchor_zones.get(f'obj:{o.const or o.index}', set()): return True
        if t == 'gate': return False   # gates are realised only by their own gate cells (above)
        # a locked move of the right kind and direction between the two zones
        zo = self.zg.zone_of
        for c, es in self.cell_edges.items():
            if not in_lock(c): continue
            zc = zo.get(c)
            if zc not in (a, b): continue
            for dcell, r in es:
                if not in_lock(dcell): continue
                zd = zo.get(dcell)
                if zd not in (a, b) or zd == zc: continue
                if t == 'surf': return True
                if t == 'ledge' and (zc, zd) == (a, b) and r == 0: return True
                if t == 'waterfall' and (zc, zd) == (a, b) and r & WATERFALL: return True
        return False

    def nodes(self, z):
        """blocks from which zone z can be extended: its region plus locked blocks whose side has z cells."""
        out = [(n, None) for n in self.region[z]]
        for L in self.lock:
            for dd in D4:
                if z in self.lock_side_zones(L[0], L[1], dd): out.append((L, dd))
        return out

    def is_dst(self, blk, from_dir, dst):
        """does block blk belong to placed zone dst on the side facing from_dir?"""
        g = self.G[blk[1]][blk[0]] if self.inb(*blk) else None
        if g == ('Z', dst): return True
        if g == 'LOCK': return dst in self.lock_side_zones(blk[0], blk[1], from_dir)
        return False

    def link(self, p, src, dst, dst_placed, rng):
        """extend src by a corridor and join it to dst with the gate of pair p. With corridors on,
        the join is made at a far end of the corridor search (longer walks); otherwise nearest."""
        t, a, b, bits = p
        if t == 'ledge':
            hops = [d for d in LEDGE_PATTERNS if self.cat.has(LEDGE_PATTERNS[d])]
            dirs = hops if src == a else [(-x, -y) for x, y in hops]
        elif t == 'waterfall': dirs = [(0, -1)] if src == a else [(0, 1)]
        else: dirs = D4[:]
        starts = self.nodes(src)
        rng.shuffle(starts)
        prev, depth = {}, {}
        q = deque()
        for n, side in starts:
            if n not in prev: prev[n] = None; depth[n] = 0; q.append((n, side))
        if t in ('whirl', 'xgate'): return False
        if t == 'gate' and bits == CUT:
            ok_d = {a for (a, b) in self.cat.cut_opts}
            dirs = [d for d in D4 if (-d[0], -d[1]) in ok_d]   # entering from the src side
            if not dirs: return False
        elif t == 'gate' and not self.take_gate_object(bits, dry=True): return False
        far = self.far_links
        found = []          # (depth, P, d, X, D)
        while q:
            P, side = q.popleft()
            rng.shuffle(dirs)
            for dx, dy in dirs:
                if side is not None and (dx, dy) != side: continue
                if t == 'surf':
                    X = (P[0] + dx, P[1] + dy)
                    ok = self.is_dst(X, (-dx, -dy), dst) if dst_placed else self.can_claim(X[0], X[1], dst)
                    if ok: found.append((depth[P], P, (dx, dy), X, None))
                else:
                    D = (P[0] + dx, P[1] + dy); X = (P[0] + 2 * dx, P[1] + 2 * dy)
                    if not (self.inb(*D) and self.inb(*X)): continue
                    if self.G[D[1]][D[0]] is not None or D in self.reserved or D in prev: continue
                    sides = [(D[0] + dy, D[1] + dx), (D[0] - dy, D[1] - dx)]
                    if any(self.inb(x, y) and (self.side_taken(x, y, D) or (x, y) in prev) for x, y in sides): continue
                    ok = self.is_dst(X, (-dx, -dy), dst) if dst_placed else (self.can_claim(X[0], X[1], dst) and X not in prev)
                    if ok: found.append((depth[P], P, (dx, dy), X, D))
            if found and not far: break
            if len(prev) > 400: break
            for dx, dy in D4:
                if side is not None and (dx, dy) != side: continue
                n = (P[0] + dx, P[1] + dy)
                if n in prev or not self.can_claim(n[0], n[1], src): continue
                prev[n] = P; depth[n] = depth[P] + 1; q.append((n, None))
        if not found: return False
        if far:
            top = max(f[0] for f in found)
            pool = [f for f in found if f[0] >= top * 0.6]
        else:
            low = min(f[0] for f in found)
            pool = [f for f in found if f[0] == low]
        rng.shuffle(pool)
        for dep, P, (dx, dy), X, D in pool:
            # the corridor to P must not cross the device / far block of this choice
            path, cur = set(), P
            while cur is not None: path.add(cur); cur = prev[cur]
            if X in path or (D is not None and D in path): continue
            if D is not None:
                sides = [(D[0] + dy, D[1] + dx), (D[0] - dy, D[1] - dx)]
                if any(self.inb(x, y) and (x, y) in path for x, y in sides): continue
            self.commit_path(prev, P, src)
            if D is None:
                if not dst_placed:
                    if not self.can_claim(X[0], X[1], dst): continue
                    self.claim(X[0], X[1], dst)
                return True
            self.make_device(t, D, (dx, dy), P, X, src, dst, a, b, bits)
            if not dst_placed: self.claim(X[0], X[1], dst)
            for x, y in sides:
                if self.inb(x, y): self.reserved.add((x, y))
            return True
        return False

    def tree(self, z, limit=600):
        """breadth-first search over free blocks from zone z's blocks: (prev, out) - the predecessor of
        every block reached, and the ways out found (up to limit blocks)."""
        prev = {}
        q = deque()
        for n, side in self.nodes(z):
            if n not in prev: prev[n] = None; q.append((n, side))
        out = {}
        while q and len(prev) < limit:
            P, side = q.popleft()
            out[P] = side
            for dx, dy in D4:
                if side is not None and (dx, dy) != side: continue
                n = (P[0] + dx, P[1] + dy)
                if n in prev or not self.can_claim(n[0], n[1], z): continue
                prev[n] = P; q.append((n, None))
        return prev, out

    def link2(self, p, rng):
        """join two placed zones, growing corridors from both sides."""
        t, a, b, bits = p
        if t in ('whirl', 'xgate'): return False
        if t == 'gate' and bits != CUT and not self.take_gate_object(bits, dry=True): return False
        pa, oa = self.tree(a); pb, ob = self.tree(b)
        def side_ok(n, o, d): return o.get(n, 0) is None or o.get(n) == d
        cands = []
        if t == 'surf':
            for n in oa:
                for dx, dy in D4:
                    m = (n[0] + dx, n[1] + dy)
                    if m in ob and side_ok(n, oa, (dx, dy)) and side_ok(m, ob, (-dx, -dy)) and m != n:
                        cands.append((n, m, None, (dx, dy)))
        else:
            if t == 'ledge': dirs = [d for d in LEDGE_PATTERNS if self.cat.has(LEDGE_PATTERNS[d])]
            elif t == 'waterfall': dirs = [(0, -1)]    # a below b
            elif t == 'gate' and bits == CUT:
                dirs = [d for d in D4 if ((-d[0], -d[1]), d) in self.cat.cut_opts]
            else: dirs = D4
            for n in oa:
                for dx, dy in dirs:
                    D = (n[0] + dx, n[1] + dy); m = (n[0] + 2*dx, n[1] + 2*dy)
                    if not (self.inb(*D) and m in ob): continue
                    if self.G[D[1]][D[0]] is not None or D in self.reserved: continue
                    if not side_ok(n, oa, (dx, dy)) or not side_ok(m, ob, (-dx, -dy)): continue
                    cands.append((n, m, D, (dx, dy)))
        rng.shuffle(cands)
        def plen(prev, n):
            k = 0
            while prev.get(n) is not None: n = prev[n]; k += 1
            return k
        cands.sort(key=lambda c: plen(pa, c[0]) + plen(pb, c[1]))
        for n, m, D, d in cands[:30]:
            snapshot = ([row[:] for row in self.G], {k: v[:] for k, v in self.region.items()}, set(self.reserved))
            ok = True
            pathA, cur = [], n
            while cur is not None and self.G[cur[1]][cur[0]] is None: pathA.append(cur); cur = pa[cur]
            pathB, cur = [], m
            while cur is not None and self.G[cur[1]][cur[0]] is None: pathB.append(cur); cur = pb[cur]
            if D is not None and (D in pathA or D in pathB): ok = False
            if ok:
                for c in reversed(pathA):
                    if not self.can_claim(*c, a): ok = False; break
                    self.claim(*c, a)
            if ok:
                for c in reversed(pathB):
                    if not self.can_claim(*c, b): ok = False; break
                    self.claim(*c, b)
            if ok and D is not None:
                sides = [(D[0] + d[1], D[1] + d[0]), (D[0] - d[1], D[1] - d[0])]
                if any(self.inb(x, y) and self.side_taken(x, y, D) for x, y in sides): ok = False
                else:
                    self.make_device(t, D, d, n, m, a, b, a, b, bits)
                    for x, y in sides:
                        if self.inb(x, y): self.reserved.add((x, y))
            if ok: return True
            self.G, self.region, self.reserved = snapshot[0], defaultdict(list, snapshot[1]), snapshot[2]
        return False

    def commit_path(self, prev, P, z):
        cur = P
        while cur is not None and self.G[cur[1]][cur[0]] is None:
            self.claim(cur[0], cur[1], z); cur = prev[cur]

    def take_gate_object(self, bits, dry=False, pair=None):
        pool = self.gate_pool.get(bits)
        if not pool: return None
        pick = 0
        if pair is not None:
            want = set(pair)
            for i, o in enumerate(pool):
                if self.anchor_zones.get(f'obj:{o.const or o.index}', set()) == want: pick = i; break
        return pool[pick] if dry else pool.pop(pick)

    def make_device(self, t, pos, d, src_blk, dst_blk, src, dst, a, b, bits):
        """build the gate device of type t (cut tree, rock, boulder, NPC, ledge, waterfall, whirlpool)
        at block pos, joining zone a's block src_blk to zone b's dst_blk in direction d."""
        if t == 'gate' and bits == CUT:
            # entry side seen from the block is the side facing src: -d
            opts = self.cat.cut_opts.get(((-d[0], -d[1]), d)) or []
            if not opts: raise GenError('no cut block for this direction')
            blk = opts[0]
            self.G[pos[1]][pos[0]] = ('DEV', 'cut', {src_blk, dst_blk}, d, src, dst, blk, a)
            self.devices.append(('cut', pos, d, src, dst, None))
            return
        obj = self.take_gate_object(bits, pair=(a, b)) if t == 'gate' else None
        if t == 'touch':
            obj = self.touch_objs[(min(a, b), max(a, b))]
            t = 'gate'
        self.G[pos[1]][pos[0]] = ('DEV', t, {src_blk, dst_blk}, d, src, dst, obj, a)
        self.devices.append((t, pos, d, src, dst, obj))

    def grow_zone(self, z, rng, frac):
        """grow zone z towards frac of its target size, preferring blocks that make corridors."""
        goal = max(1, round(self.target[z] * frac))
        while len(self.region[z]) < goal:
            front = self.frontier(z)
            if not front: return
            corridor = rng.random() < 0.6
            def score(p):
                n = sum(1 for dx, dy in D4 if self.inb(p[0]+dx, p[1]+dy) and self.G[p[1]+dy][p[0]+dx] == ('Z', z))
                return (-n if corridor else n) + rng.random() * 1.5
            self.claim(*max(front, key=score), z)

    def grow(self, rng):
        """grow every placed zone towards its target size, a block at a time in random order."""
        target = self.target
        active = [z for z in self.Z if self.region[z]]
        stuck = set()
        while True:
            todo = [z for z in active if len(self.region[z]) < target[z] and z not in stuck]
            if not todo: break
            z = rng.choice(todo)
            front = self.frontier(z)
            if not front: stuck.add(z); continue
            # water grows compact; land that will become corridors grows in tendrils (blocks
            # touching one block of its own), which reach into free space instead of boxing
            # the zone in
            snake = False
            def score(p):
                n = sum(1 for dx, dy in D4 if self.inb(p[0]+dx, p[1]+dy) and self.G[p[1]+dy][p[0]+dx] == ('Z', z))
                if snake: return (2 if n == 1 else -n) + rng.random() * 1.5
                return n + rng.random() * 1.5
            bx, by = max(front, key=score)
            self.claim(bx, by, z)

    @staticmethod
    def _scc(nodes, adj):
        """strongly connected components (Kosaraju, iterative)."""
        order, seen = [], set()
        for c0 in nodes:
            if c0 in seen: continue
            seen.add(c0); st = [(c0, iter(adj.get(c0, ())))]
            while st:
                c, it = st[-1]
                nxt = next((d for d in it if d in nodes and d not in seen), None)
                if nxt is None: order.append(c); st.pop()
                else: seen.add(nxt); st.append((nxt, iter(adj.get(nxt, ()))))
        radj = defaultdict(list)
        for c in nodes:
            for d in adj.get(c, ()):
                if d in nodes: radj[d].append(c)
        comps, seen = [], set()
        for c0 in reversed(order):
            if c0 in seen: continue
            comp, st = [c0], [c0]; seen.add(c0)
            while st:
                for d in radj[st.pop()]:
                    if d not in seen: seen.add(d); comp.append(d); st.append(d)
            comps.append(comp)
        return comps
