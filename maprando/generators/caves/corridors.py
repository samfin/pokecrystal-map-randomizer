"""Thinning land zones into winding corridors, and joining zones with them."""
from collections import defaultdict, deque
from maprando.generators.caves.common import D4, UnifyError


class CorridorMixin:
    def corridors(self, rng, samples=6):
        """Thin every land zone into winding paths. Terminals (doorways into locked blocks, gate
        devices, one shore per Surf pair, and a pocket per item/NPC) are kept; other blocks are
        removed one at a time, never disconnecting anything, always picking (from a sample) the
        removal that lengthens the walks between terminals the most. What is left is a tree of
        one-block-wide corridors with walls between them; water zones stay open."""
        self.pockets = set()
        mm, W = self.mm, self.mm.w
        surf = [(a, b) for t, a, b, _ in self.pairs if t == 'surf']
        held = defaultdict(int)
        for aid, zs in self.anchor_zones.items():
            if len(zs) == 1 and (aid.startswith('obj:') or aid.startswith('bg:')): held[next(iter(zs))] += 1
        def blk(c): return ((c % W) // 2, (c // W) // 2)
        for z in self.Z:
            if self.kind[z] != 'land': continue
            R = {b for b in self.region[z] if self.G[b[1]][b[0]] == ('Z', z)}
            if len(R) < 3: continue
            if len(self.components(z)) != 1: continue
            # pieces of z inside locked blocks: strongly connected under vanilla moves there
            # (ledges are one way), plus the one-way links between pieces
            lc = {c for c in self.zg.zones[z] if blk(c) in self.lock}
            ladj = {c: [d for d, r in self.cell_edges.get(c, ()) if d in lc] for c in lc}
            piece = {}
            for i, comp in enumerate(self._scc(lc, ladj)):
                for c in comp: piece[c] = ('P', i)
            plink = defaultdict(set)
            for c in lc:
                for d in ladj[c]:
                    if piece[c] != piece[d]: plink[piece[c]].add(piece[d])
            # graph: region blocks + lock pieces; terminals
            ext = defaultdict(set)     # region block -> lock-piece nodes it opens onto
            T = set()
            for b in R:
                for dx, dy in D4:
                    n = (b[0] + dx, b[1] + dy)
                    if not self.inb(*n): continue
                    g = self.G[n[1]][n[0]]
                    if g == 'LOCK':
                        side = (-dx, -dy)
                        for q in range(4):
                            qx, qy = q & 1, q >> 1
                            if (side[0] == 1 and qx == 1) or (side[0] == -1 and qx == 0) or \
                               (side[1] == 1 and qy == 1) or (side[1] == -1 and qy == 0):
                                c = mm.idx(n[0] * 2 + qx, n[1] * 2 + qy)
                                if c in piece: ext[b].add(piece[c]); T.add(b)
                        if z in self.lock_side_zones(n[0], n[1], side): T.add(b)
                    elif g is not None and g[0] == 'DEV' and b in g[2]:
                        T.add(b)
            for a, w in surf:
                other = w if a == z else a if w == z else None
                if other is None: continue
                cand = [b for b in R if any(self.inb(b[0]+dx, b[1]+dy) and
                        (self.G[b[1]+dy][b[0]+dx] == ('Z', other) or
                         (self.G[b[1]+dy][b[0]+dx] == 'LOCK' and other in self.lock_side_zones(b[0]+dx, b[1]+dy, (-dx, -dy))))
                        for dx, dy in D4)]
                if cand and not (set(cand) & T): T.add(rng.choice(cand))
            nw = sum(1 for _, (ws, bid, zz) in self.warp_units.items() if zz == z) + \
                 sum(1 for i, zz in getattr(self, 'warp_spot_zone', {}).items() if zz == z)
            if not T:
                # nothing fixed in this zone (every doorway moves: Tin Tower's stairs): start from a
                # corner block; the pockets below spread the movable warps and items out from it
                if held[z] + nw < 2: continue
                corner = max(R, key=lambda b: (abs(2 * b[0] - self.Wb) + abs(2 * b[1] - self.Hb), rng.random()))
                T.add(corner); self.pockets.add(corner)
            def nbrs(b, C):
                for dx, dy in D4:
                    n = (b[0] + dx, b[1] + dy)
                    if n in C: yield n
                for p in ext.get(b, ()): yield p
            def full_adj(C):
                adj = {b: list(nbrs(b, C)) for b in C}
                for b in C:
                    for p in ext.get(b, ()): adj.setdefault(p, []).append(b)
                return adj
            def strong(C):
                """is everything left (blocks + lock pieces they open onto) one strong component?"""
                adj = full_adj(C)
                for p, qs in plink.items():
                    if p in adj: adj[p] = adj[p] + [q for q in qs if q in adj]
                nodes = set(adj)
                return len(self._scc(nodes, adj)) <= 1
            def dist_from(src, adj):
                dd = {src: 0}; q = deque([src])
                while q:
                    u = q.popleft()
                    for v in adj[u]:
                        if v not in dd: dd[v] = dd[u] + 1; q.append(v)
                return dd
            # pockets: far-away blocks for the things that stand in this zone
            C = set(R)
            adj = full_adj(C)
            for _ in range(min(held[z] + nw, 6)):
                dmin = {}
                for t in T:
                    for b, k in dist_from(t, adj).items():
                        if b in C and k < dmin.get(b, 1 << 30): dmin[b] = k
                far = [b for b in C if b not in T and dmin.get(b, 0) >= 2]
                if not far: break
                top = max(dmin[b] for b in far)
                p = rng.choice([b for b in far if dmin[b] >= top - 1])
                T.add(p); self.pockets.add(p)
            Tl = list(T); rng.shuffle(Tl)
            def score(adj):
                tot = 0
                for i in range(len(Tl) - 1):
                    dd = dist_from(Tl[i], adj)
                    if Tl[i + 1] not in dd: return None
                    tot += dd[Tl[i + 1]]
                return tot
            def articulation(adj):
                """non-cut vertices among region blocks (Tarjan, iterative)."""
                disc, low, cut, t = {}, {}, set(), [0]
                for root in adj:
                    if root in disc: continue
                    disc[root] = low[root] = t[0]; t[0] += 1
                    st = [(root, None, iter(adj[root]))]; kids = 0
                    while st:
                        u, par, it = st[-1]
                        v = next(it, None)
                        if v is None:
                            st.pop()
                            if st:
                                p = st[-1][0]
                                low[p] = min(low[p], low[u])
                                if st[-1][1] is not None and low[u] >= disc[p]: cut.add(p)
                            continue
                        if v == par: continue
                        if v in disc: low[u] = min(low[u], disc[v])
                        else:
                            disc[v] = low[v] = t[0]; t[0] += 1
                            if u == root: kids += 1
                            st.append((v, u, iter(adj[v])))
                    if kids > 1: cut.add(root)
                return cut
            removed = []
            npair = len(Tl) - 1
            # integer graph built once: region blocks + lock pieces; removed blocks are marked dead
            nodes = sorted(C) + sorted({p for b in C for p in ext.get(b, ())})
            ix = {v: k for k, v in enumerate(nodes)}
            N = len(nodes)
            alive = [True] * N
            nb = [[] for _ in range(N)]
            for b in C:
                k = ix[b]
                for dx, dy in D4:
                    n = (b[0] + dx, b[1] + dy)
                    if n in ix: nb[k].append(ix[n])
                for p in ext.get(b, ()): nb[k].append(ix[p]); nb[ix[p]].append(k)
            Ti = [ix[t] for t in Tl]
            Tset = {ix[t] for t in T}
            def bfs(src, skip=-1, dst=-1):
                dist = [-1] * N; dist[src] = 0; q = deque([src])
                while q:
                    u = q.popleft()
                    if u == dst: break
                    du = dist[u] + 1
                    for v in nb[u]:
                        if dist[v] < 0 and alive[v] and v != skip: dist[v] = du; q.append(v)
                return dist
            def cut_vertices():
                """articulation points of the alive graph (Tarjan, iterative)."""
                disc = [-1] * N; low = [0] * N; cut = set(); t = 0
                for root in range(N):
                    if not alive[root] or disc[root] >= 0: continue
                    disc[root] = low[root] = t; t += 1
                    st = [(root, -1, iter(nb[root]))]; kids = 0
                    while st:
                        u, par, it = st[-1]
                        v = next(it, None)
                        if v is None:
                            st.pop()
                            if st:
                                pu = st[-1][0]
                                if low[u] < low[pu]: low[pu] = low[u]
                                if st[-1][1] != -1 and low[u] >= disc[pu]: cut.add(pu)
                            continue
                        if v == par or not alive[v]: continue
                        if disc[v] >= 0:
                            if disc[v] < low[u]: low[u] = disc[v]
                        else:
                            disc[v] = low[v] = t; t += 1
                            if u == root: kids += 1
                            st.append((v, u, iter(nb[v])))
                    if kids > 1: cut.add(root)
                return cut
            # per pair of consecutive terminals: current walk length and the blocks on a shortest
            # walk. Removing a block off every shortest walk changes nothing, so only the pairs a
            # candidate lies on are recomputed (and only those after a removal).
            Lp, onp = [None] * npair, [set() for _ in range(npair)]
            nblocks = len(C)
            def refresh(i):
                da = bfs(Ti[i]); db = bfs(Ti[i + 1])
                L = da[Ti[i + 1]]
                if L < 0: Lp[i] = None; onp[i] = set(); return
                Lp[i] = L
                onp[i] = {k for k in range(nblocks) if alive[k] and da[k] >= 0 and db[k] >= 0 and da[k] + db[k] == L}
            directed = bool(plink)
            dirty = set(range(npair))
            while True:
                cut = cut_vertices()
                cand = [k for k in range(nblocks) if alive[k] and k not in Tset and k not in cut]
                if not cand: break
                for i in dirty: refresh(i)
                dirty = set()
                hot = [k for k in cand if any(k in o for o in onp)]
                if hot:
                    rng.shuffle(hot)
                    best, bs = None, -1.0
                    for k in hot[:samples]:
                        sc, ok = 0, True
                        for i in range(npair):
                            if Lp[i] is None: continue
                            if k in onp[i]:
                                L = bfs(Ti[i], skip=k, dst=Ti[i + 1])[Ti[i + 1]]
                                if L < 0: ok = False; break
                                sc += L
                            else: sc += Lp[i]
                        if not ok: continue
                        sc += rng.random()
                        if sc > bs: best, bs = k, sc
                    if best is None: best = rng.choice(cand)
                else:
                    best = rng.choice(cand)     # distances unchanged: just prune
                b = nodes[best]
                alive[best] = False; C.discard(b)
                if directed and not strong(C):
                    alive[best] = True; C.add(b); Tset.add(best); T.add(b); continue   # needed for a way back
                removed.append(b)
                dirty = {i for i in range(npair) if best in onp[i]}
            for b in removed:
                self.G[b[1]][b[0]] = None
            self.region[z] = [b for b in self.region[z] if b in C]
            if len(self.components(z)) != 1:
                # the lock-piece shortcut was too optimistic for this zone: undo
                for b in removed: self.G[b[1]][b[0]] = ('Z', z)
                self.region[z] = list(R)
                self.pockets -= R

    def touches(self, a, b):
        def nodes(z):
            out = {n: None for n in self.region[z]}
            for L in self.lock:
                for dd in D4:
                    if z in self.lock_side_zones(L[0], L[1], dd): out.setdefault(L, set()).add(dd) if out.get(L) is not None else out.__setitem__(L, {dd})
            return out
        na, nb = nodes(a), nodes(b)
        for n, sides in na.items():
            for dx, dy in D4:
                if sides is not None and (dx, dy) not in sides: continue
                m = (n[0] + dx, n[1] + dy)
                if m in nb and (nb[m] is None or (-dx, -dy) in nb[m]): return True
        return False

    def components(self, z):
        """memoised per (zone, region) within one try (locks do not change during a try)."""
        memo = self.__dict__.setdefault('_comp_memo', {})
        key = (z, frozenset(self.region[z]))
        r = memo.get(key)
        if r is None: r = memo[key] = self._components(z)
        return [set(c) for c in r]

    def _components(self, z):
        """pieces of zone z (as sets of blocks), connected at cell level: open cells of z inside
        locked blocks use vanilla moves (including ledge jumps), region blocks are open floor."""
        mm = self.mm
        W = mm.w
        def blk(c): return ((c % W) // 2, (c // W) // 2)
        cells = set()
        for c, zz in self.zg.zone_of.items():
            if zz == z and blk(c) in self.lock: cells.add(c)
        for (bx, by) in self.region[z]:
            for q in range(4): cells.add(mm.idx(bx*2 + (q & 1), by*2 + (q >> 1)))
        # terminal warp cells inside locks that lead into z count as part of z
        for c in self.terminal:
            if blk(c) in self.lock and any(self.zg.zone_of.get(d) == z for d, r in self.cell_edges.get(c, ())):
                cells.add(c)
        # directed: a locked ledge joins its two sides one way only, so a zone held together by
        # a ledge plus a path back is one piece, but a ledge alone leaves two
        adj = {c: set() for c in cells}
        for c in cells:
            x, y = c % W, c // W
            for dx, dy in D4:
                if 0 <= x+dx < W and 0 <= y+dy < mm.h:
                    d = c + dx + dy * W
                    if d in cells and (blk(c) not in self.lock or blk(d) not in self.lock):
                        adj[c].add(d); adj[d].add(c)
            if blk(c) in self.lock:
                for d, r in self.cell_edges.get(c, ()):
                    if d in cells and blk(d) in self.lock: adj[c].add(d)
        radj = {c: set() for c in cells}
        for c, ds in adj.items():
            for d in ds: radj[d].add(c)
        order, seen = [], set()
        for c0 in cells:                       # Kosaraju, iterative
            if c0 in seen: continue
            seen.add(c0); st = [(c0, iter(adj[c0]))]
            while st:
                c, it = st[-1]
                nxt = next((d for d in it if d not in seen), None)
                if nxt is None: order.append(c); st.pop()
                else: seen.add(nxt); st.append((nxt, iter(adj[nxt])))
        comps, seen = [], set()
        for c0 in reversed(order):
            if c0 in seen: continue
            comp, st = {c0}, [c0]; seen.add(c0)
            while st:
                for d in radj[st.pop()]:
                    if d not in seen: seen.add(d); comp.add(d); st.append(d)
            comps.append({blk(x) for x in comp})
        return comps

    def unify(self, z, rng, avoid=None):
        if avoid:
            # first try to keep clear of the doorways other zones still need
            av = {b for b, zz in avoid.items() if zz != z}
            saved = (list(self.region[z]), [row[:] for row in self.G])
            try:
                return self._unify(z, rng, av)
            except UnifyError:
                self.region[z], self.G = saved[0], saved[1]
        return self._unify(z, rng, set())

    def _unify(self, z, rng, av):
        for _ in range(20):
            comps = self.components(z)
            if len(comps) <= 1: return
            comps.sort(key=len, reverse=True)
            src = [c for c in comps[0] if c in self.region[z]] or list(comps[0])
            others = set().union(*comps[1:])
            prev = {c: None for c in src}
            q = deque(src); hit = None
            while q and hit is None:
                cur = q.popleft()
                dirs = D4[:]; rng.shuffle(dirs)
                if cur in self.lock:   # leave a locked block only through a side where z is open
                    dirs = [dd for dd in dirs if z in self.lock_side_zones(cur[0], cur[1], dd)]
                for dx, dy in dirs:
                    n = (cur[0] + dx, cur[1] + dy)
                    if n in prev: continue
                    if n in others and (n not in self.lock or z in self.lock_side_zones(n[0], n[1], (-dx, -dy))):
                        prev[n] = cur; hit = n; break
                    if n in av or not self.can_claim(n[0], n[1], z): continue
                    prev[n] = cur; q.append(n)
            if hit is None: raise UnifyError(z)
            cur = prev[hit]
            while cur is not None and cur not in comps[0]:
                self.claim(cur[0], cur[1], z); cur = prev[cur]
        raise UnifyError(z)

    def connect(self, p, rng):
        """join two already-placed zones with a corridor (surf/gate/ledge)."""
        t, a, b, bits = p
        if t == 'waterfall': return False
        start = list(self.region[a]); rng.shuffle(start)
        goal_adj = set()
        for (bx, by) in self.region[b]:
            for dx, dy in D4: goal_adj.add((bx + dx, by + dy))
        prev = {s: None for s in start}
        q = deque(start)
        while q:
            cur = q.popleft()
            if cur in goal_adj and cur not in self.region[a] or (cur in self.region[a] and cur in goal_adj and t == 'surf'):
                path = []
                while cur is not None: path.append(cur); cur = prev[cur]
                path.reverse()
                return self.realize_corridor(p, path)
            dirs = D4[:]; rng.shuffle(dirs)
            for dx, dy in dirs:
                n = (cur[0] + dx, cur[1] + dy)
                if n in prev or not self.inb(*n): continue
                if self.G[n[1]][n[0]] is not None or n in self.reserved: continue
                prev[n] = cur; q.append(n)
        return False

    def realize_corridor(self, p, path):
        t, a, b, bits = p
        inner = [c for c in path if self.G[c[1]][c[0]] is None]
        if t == 'surf':
            for c in inner:
                if not self.can_claim(*c, a): return False
                self.claim(*c, a)
            return True
        # need a straight interior point for the device
        for i in range(1, len(path) - 1):
            c = path[i]
            if self.G[c[1]][c[0]] is not None: continue
            d_in = (c[0] - path[i-1][0], c[1] - path[i-1][1])
            nxt = path[i+1] if i + 1 < len(path) else None
            if nxt is None: continue
            d_out = (nxt[0] - c[0], nxt[1] - c[1])
            if d_in != d_out: continue
            if t == 'ledge' and d_in != (0, 1): continue
            before, after = path[:i], path[i+1:]
            ok = all(self.G[y][x] is not None or self.can_claim(x, y, a) for x, y in before)
            ok = ok and all(self.G[y][x] is not None or self.can_claim(x, y, b) for x, y in after)
            if not ok: continue
            if t == 'gate' and not self.take_gate_object(bits, dry=True): return False
            for x, y in before:
                if self.G[y][x] is None: self.claim(x, y, a)
            for x, y in after:
                if self.G[y][x] is None: self.claim(x, y, b)
            if not self.goal_touch(after[-1] if after else path[-1], b): return False
            self.make_device(t, c, d_in, path[i-1], nxt, a, b, a, b, bits)
            return True
        return False

    def goal_touch(self, blk, z):
        return True
