"""Reading the vanilla map: zones, locked blocks, scripted paths, which warps may move."""
from collections import defaultdict, deque
import copy
from maprando.generators.caves.common import D4, GenError, WarpRoomError
from maprando.generators.caves.guards import guard_pocket
from maprando.model.movement import CUT, DIRS, LEDGE_DIRS, ROCK_SMASH, SURF, WATERFALL, WHIRLPOOL, classify
from maprando.model.reach import requirement_matrix
from maprando.model.zones import medium


class AnalysisMixin:
    def _analyze(self):
        mm, zg = self.mm, self.zg
        self.route = any(a.kind == 'edge' for a in mm.anchors)
        if self.m.tileset == 'TILESET_FOREST': self.route = True   # forest: tree walls, block-aligned like routes
        if mm.features & {'ice', 'current'}:
            raise GenError(f'unsupported features {mm.features}')
        from maprando.model.reach import Searcher
        s = Searcher(mm)
        self.terminal = s.terminal
        entry_zones = {zg.zone_of[c] for a in mm.anchors if a.is_entry for c in a.cells if c in zg.zone_of}
        succ = defaultdict(set)
        for a, b, r in zg.edges: succ[a].add(b)
        R, q = set(entry_zones), deque(entry_zones)
        while q:
            z = q.popleft()
            for n in succ[z]:
                if n not in R: R.add(n); q.append(n)
        self.T = {z for z, cs in zg.zones.items() if all(c in self.terminal for c in cs)}
        # void: anchor-less land touching the map frame (the black filler around caves)
        def on_frame(c):
            x, y = c % mm.w, c // mm.w
            return x in (0, mm.w - 1) or y in (0, mm.h - 1)
        cand = {z for z in R - self.T if not zg.anchors.get(z) and medium(mm, zg.zones[z][0]) == 0
                and any(on_frame(c) for c in zg.zones[z])}
        def harmless(zs):
            if not zs: return True
            mm2 = copy.copy(mm); mm2.coll = list(mm.coll)
            for z in zs:
                for c in zg.zones[z]: mm2.coll[c] = 0x07
            return requirement_matrix(mm2, boulders=False) == self.matrix
        if harmless(cand): self.void = cand
        else: self.void = {z for z in cand if harmless({z})}
        R = R - self.void
        self.Z = sorted(R - self.T)
        self.kind = {z: ('water' if medium(mm, zg.zones[z][0]) else 'land') for z in self.Z}
        self._movable_warps()
        moving = {mm.idx(w.x, w.y) for blk, (ws, _b, _z) in self.warp_units.items() for w in ws} | \
                 {mm.idx(w.x, w.y) for w in self.warp_spots} | \
                 {mm.idx(w.x, w.y) for blk, (ws, _b, _z) in self.door_units.items() for w in ws}
        # locked blocks: warps (those that do not move) and coord events
        lock = set()
        for c in [c for c in mm.warp_cells if c not in moving] + [mm.idx(e.x, e.y) for e in self.m.coord_events]:
            lock.add(((c % mm.w) // 2, (c // mm.w) // 2))
        # cutscene actors only a coord event may reach (talked to, they crash the game): their
        # surroundings stay as in vanilla (verify checks no other way reaches them)
        for c in guard_pocket(mm, self.m):
            lock.add(((c % mm.w) // 2, (c // mm.w) // 2))
        # doormats only fire when the step past them is blocked: keep that tile's block as well
        from maprando.model.movement import CARPET_DIR
        for c in mm.warp_cells:
            dd = CARPET_DIR.get(mm.coll[c])
            if dd is None or c in moving: continue
            x, y = c % mm.w + dd[0], c // mm.w + dd[1]
            if mm.inb(x, y): lock.add((x // 2, y // 2))
        # route edges that connect to a neighbouring map: the openings (edge blocks you can walk out
        # of) stay exactly as they are; the rest of the edge is free, but its outermost tiles must
        # stay wall (checked in verify: edges_ok), so no new way out leads into the neighbour's wall
        self.edge_walls = set()
        for (dname, _o, _c, _off) in self.m.connections:
            if dname in ('north', 'south'):
                y = 0 if dname == 'north' else mm.h - 1
                outer = [(x, y) for x in range(mm.w)]
            else:
                x = 0 if dname == 'west' else mm.w - 1
                outer = [(x, y) for y in range(mm.h)]
            # openings: edge tiles the model says you can really walk in from (the tile across is
            # reached in the neighbouring map); walkable scenery along the edge (mountain tops,
            # borders) is not an opening and stays wall, so no layout opens a way into the
            # neighbour's scenery
            real = {c for a in mm.anchors if a.kind == 'edge' and a.id.startswith(f'edge:{dname}:') for c in a.cells}
            for (x, y) in outer:
                c = mm.idx(x, y)
                if c in real or c in mm.warp_cells: lock.add((x // 2, y // 2))
                else: self.edge_walls.add(c)
        # buildings: flood from each warp block over blocks that only ever appear near warps
        stack = [((c % mm.w) // 2, (c // mm.w) // 2) for c in mm.warp_cells if c not in moving]
        seen_s = set(stack)
        while stack:
            bx, by = stack.pop(); lock.add((bx, by))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (bx + dx, by + dy)
                    if n in seen_s or not (0 <= n[0] < self.Wb and 0 <= n[1] < self.Hb): continue
                    if n in self.door_units: continue          # a door that moves is not part of it
                    if self.m.blocks[n[1] * self.Wb + n[0]] in self.cat.structure:
                        seen_s.add(n); stack.append(n)
        # signposts are drawn into the block: keep them where they are (not a plain signpost
        # standing on floor: it does not matter, and moves with its block; signs set into a wall -
        # Rocket Base posters, a Pokemon Center sign - stay with the wall)
        from maprando.model.movement import plain_signs
        _signs = plain_signs(self.m)
        for b in self.m.bg_events:
            if b.index in _signs and 0 <= b.x < mm.w and 0 <= b.y < mm.h and \
                    '.' in self.cat.pattern[self.m.blocks[(b.y // 2) * self.Wb + b.x // 2]]: continue
            if b.kind != 'BGEVENT_ITEM' and 0 <= b.x < mm.w and 0 <= b.y < mm.h:
                lock.add((b.x // 2, b.y // 2))
        # whirlpools stay put (with the water around them)
        for c in range(mm.w * mm.h):
            if mm.coll[c] == 0x24:
                bx, by = (c % mm.w) // 2, (c // mm.w) // 2
                for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
                    n = (bx + dx, by + dy)
                    if 0 <= n[0] < self.Wb and 0 <= n[1] < self.Hb: lock.add(n)
        # scripted scenes that move the player: keep the area around their trigger tiles
        if 'scripted_player_move' in mm.features:
            for e in self.m.coord_events:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        n = (e.x // 2 + dx, e.y // 2 + dy)
                        if 0 <= n[0] < self.Wb and 0 <= n[1] < self.Hb: lock.add(n)
        # people talked to across a counter: the counter, the person and the talking spot stay as in
        # vanilla only when the counter gives a second way to the object (see below)
        from maprando.model.movement import COUNTERS
        for a in mm.anchors:
            if a.kind == 'obj' and getattr(a, 'via', None):
                # zones beside the object (not across a counter) and zones across one
                plain = {self.zg.zone_of.get(c) for c in a.cells} | \
                        {self.zg.zone_of.get(c + o_) for c in a.cells for o_ in (1, -1, mm.w, -mm.w)
                         if 0 <= c + o_ < mm.w * mm.h and mm.coll[c + o_] not in COUNTERS}
                across = {self.zg.zone_of.get(v) for v in a.via} - {None}
                # a shopkeeper in a closed pocket behind the counter (the Goldenrod Underground's
                # merchants): only the person matters, placed anew like anyone else in the area he
                # is talked to from; someone the counter gives nothing extra (Teru) likewise. Only
                # an object a counter gives a second way to (Rocket Base B3F's item ball) keeps it.
                if not (plain & set(self.Z)) or across <= plain: continue
            for v in getattr(a, 'via', ()):
                for c in a.cells:
                    if abs(c % mm.w - v % mm.w) + abs(c // mm.w - v // mm.w) != 2: continue
                    mid = (c + v) // 2
                    for q in (c, mid, v):
                        lock.add(((q % mm.w) // 2, (q // mm.w) // 2))
        # hidden items under a smashable rock (Mt. Moon Square's Moon Stone): rock and item stay
        # together where they are, so the item still needs Rock Smash
        for b in self.m.bg_events:
            o = mm.objects.get(mm.idx(b.x, b.y)) if mm.inb(b.x, b.y) else None
            if o and o[0] == 'bits' and o[1] == ROCK_SMASH: lock.add((b.x // 2, b.y // 2))
        # ledges jump without looking where they land: the tiles in front of and beyond each ledge
        # keep their vanilla blocks, so no new wall turns a step into a jump into a wall
        # (only as a fallback: verify's jump check rejects bad jumps in any design, so these are
        # locked only once half of a map's tries found nothing; see generate)
        from maprando.checks.jumps import ledge_cells
        self.ledge_lock = {((q % mm.w) // 2, (q // mm.w) // 2) for q in ledge_cells(mm)}
        # Fly destinations stay as in vanilla (with their surroundings)
        for sx, sy in getattr(self.d, 'spawns', {}).get(self.m.name, []):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (sx // 2 + dx, sy // 2 + dy)
                    if 0 <= n[0] < self.Wb and 0 <= n[1] < self.Hb: lock.add(n)
        # objects moved by scripts keep their place and surroundings; so do moveobject targets
        import re as _re
        src = self.m.script
        from maprando.checks.scenes import scripted_paths as _sp
        # an object that only turns on the spot (Fuchsia Gym's ninja spin) is not staged anywhere
        spins = {n for n, p, _ in _sp(self.m, src) if len(set(p)) == 1} - \
                {n for n, p, _ in _sp(self.m, src) if len(set(p)) > 1}
        scripted = (set(_re.findall(r'applymovement\s+(\w+)', src)) - spins
                    | set(_re.findall(r'(?:moveobject|follow)\s+(\w+)', src))) - {'PLAYER'}
        def lock_area(cx, cy):
            for dx in (0,):
                for dy in (0,):
                    b = (cx // 2 + dx, cy // 2 + dy)
                    if 0 <= b[0] < self.Wb and 0 <= b[1] < self.Hb: lock.add(b)
        self.scripted_objs = set()
        for o in self.m.objects:
            if o.const in scripted:
                lock_area(o.x, o.y); self.scripted_objs.add(o.const)
        self._lock_scripted_paths(src, lock)
        targets = defaultdict(list)
        for mo in _re.finditer(r'moveobject\s+(\w+),\s*(\d+),\s*(\d+)', src):
            lock_area(int(mo.group(2)), int(mo.group(3)))
            targets[mo.group(1)].append((int(mo.group(2)), int(mo.group(3))))
        # an object a script walks around a set of spots (Ilex Forest's Farfetch'd) is a puzzle laid
        # out on the vanilla map: its whole area keeps the vanilla layout
        for name, pts in targets.items():
            if len(set(pts)) < 3: continue
            x0, x1 = min(p[0] for p in pts) - 2, max(p[0] for p in pts) + 2
            y0, y1 = min(p[1] for p in pts) - 2, max(p[1] for p in pts) + 2
            for by in range(max(0, y0 // 2), min(self.Hb, y1 // 2 + 1)):
                for bx in range(max(0, x0 // 2), min(self.Wb, x1 // 2 + 1)): lock.add((bx, by))
        # blocks that scripts swap at run time (holes opening, stairs appearing): keep them and
        # their neighbours exactly as in vanilla so whatever the swap opens connects the same way
        self.changeblocks = set()
        self.swaps = []      # (block x, block y, new block id) for every changeblock in the scripts
        for mo in _re.finditer(r'^\s*changeblock\s+(\d+),\s*(\d+),\s*\$([0-9a-fA-F]+)', src, _re.M):
            sw = (int(mo.group(1)) // 2, int(mo.group(2)) // 2, int(mo.group(3), 16))
            if sw not in self.swaps: self.swaps.append(sw)
        for mo in _re.finditer(r'^\s*changeblock\s+(\d+),\s*(\d+)', src, _re.M):
            bx, by = int(mo.group(1)) // 2, int(mo.group(2)) // 2
            self.changeblocks.add((bx, by))
            for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
                b = (bx + dx, by + dy)
                if 0 <= b[0] < self.Wb and 0 <= b[1] < self.Hb: lock.add(b)
        # grow the locked area over neighbours that hold gate structure or several zones
        def blk_cells(bx, by): return [mm.idx(bx*2 + (q & 1), by*2 + (q >> 1)) for q in range(4)]
        def interesting(bx, by):
            cs = blk_cells(bx, by)
            zs = {zg.zone_of[c] for c in cs if c in zg.zone_of and zg.zone_of[c] in R and zg.zone_of[c] not in self.T}
            if len(zs) > 1: return True
            for c in cs:
                if (mm.coll[c] & 0xF0) == 0xA0 and getattr(self, 'grow_ledges', False): return True
                if mm.objects.get(c, ('',))[0] == 'bits': return True
                if mm.objects.get(c, ('',))[0] == 'wall':
                    x, y = c % mm.w, c // mm.w
                    nz = {zg.zone_of.get(mm.idx(x+dx, y+dy)) for dx, dy in D4 if mm.inb(x+dx, y+dy)}
                    nz = {z for z in nz if z is not None and z in R and z not in self.T}
                    if len(nz) > 1: return True
                if mm.coll[c] in (0x30, 0x31, 0x32, 0x33): return True
            return False
        from maprando.model.zones import cell_edges as _ce
        E = _ce(mm, s)
        changed = True
        while changed:
            changed = False
            for (bx, by) in list(lock):
                for c in blk_cells(bx, by):
                    # a ledge tile that stays: the tiles you jump from and land on stay with it
                    # (the rest of a ledge run is free: the generator builds its own ledges)
                    if (mm.coll[c] & 0xF0) == 0xA0:
                        x0, y0 = c % mm.w, c // mm.w
                        for d in LEDGE_DIRS[mm.coll[c] & 7]:
                            ddx, ddy = DIRS[d]
                            for k in (-1, 1, 2):          # from, over, and the landing two tiles on
                                if mm.inb(x0 + k * ddx, y0 + k * ddy):
                                    n = ((x0 + k * ddx) // 2, (y0 + k * ddy) // 2)
                                    if n not in lock: lock.add(n); changed = True
                    for dcell, r in E.get(c, ()):
                        dx_, dy_ = abs(dcell % mm.w - c % mm.w), abs(dcell // mm.w - c // mm.w)
                        if dx_ + dy_ > 1:   # ledge jump / waterfall: lock the landing block too
                            n = ((dcell % mm.w) // 2, (dcell // mm.w) // 2)
                            if n not in lock: lock.add(n); changed = True
                for dx, dy in D4:
                    n = (bx + dx, by + dy)
                    if n in lock or not (0 <= n[0] < self.Wb and 0 <= n[1] < self.Hb): continue
                    if interesting(*n): lock.add(n); changed = True
        # exits are drawn across two blocks (warp block + doorway/cave-mouth block): keep any
        # neighbour of a warp block whose vanilla block only ever appears next to warps
        warp_blocks = {((c % mm.w) // 2, (c // mm.w) // 2) for c in mm.warp_cells if c not in moving}
        for (bx, by) in warp_blocks:
            for dx, dy in D4:
                n = (bx + dx, by + dy)
                if 0 <= n[0] < self.Wb and 0 <= n[1] < self.Hb and self.m.blocks[n[1] * self.Wb + n[0]] in self.cat.warp_only:
                    lock.add(n)
        # objects/signs kept in place: the cells around them must stay vanilla too, otherwise
        # something unreachable in vanilla could become talk-able from a new floor cell
        for ex, ey in [(o.x, o.y) for o in self.m.objects] + [(b.x, b.y) for b in self.m.bg_events]:
            if (ex // 2, ey // 2) not in lock: continue
            for dx, dy in D4:
                b = ((ex + dx) // 2, (ey + dy) // 2)
                # (a door block that moves away leaves wall or floor the verifier checks)
                if b in self.door_units: continue
                if 0 <= ex + dx < mm.w and 0 <= ey + dy < mm.h: lock.add(b)
        # (again, after every rule: a ledge tile in any locked block keeps its jump with it)
        self.ledge_close(lock)
        self.lock = lock
        # vanilla's decorative ledges that jump into a wall sit in pockets you cannot reach: those
        # pockets stay sealed (their free neighbours stay wall: seal_edges / edges_ok), so a new
        # layout never opens a way to a broken jump
        from maprando.checks.jumps import bad_jumps
        from maprando.model.reach import Searcher, entry_starts
        sr = Searcher(mm)
        R0 = set(sr.search([st for a in mm.anchors if a.is_entry for st in entry_starts(mm, a)]))
        def locked_cell(c): return ((c % mm.w) // 2, (c // mm.w) // 2) in lock
        def walkc(c): return classify(mm, mm.coll[c]) is not None or (mm.coll[c] & 0xF0) == 0xA0
        for c, dd, _ in bad_jumps(mm, everywhere=True):
            a = c                                   # the ledge tile the jump starts on
            if a in R0: continue
            pocket, st = {a}, [a]
            while st:
                q = st.pop(); x, y = q % mm.w, q // mm.w
                for ex, ey in D4:
                    if not mm.inb(x + ex, y + ey): continue
                    n = mm.idx(x + ex, y + ey)
                    if n in pocket or n in R0 or not walkc(n): continue
                    pocket.add(n); st.append(n)
            for q in pocket:
                x, y = q % mm.w, q // mm.w
                if not locked_cell(q): self.edge_walls.add(q)
                for ex, ey in D4:
                    if mm.inb(x + ex, y + ey):
                        n = mm.idx(x + ex, y + ey)
                        if n not in pocket and not locked_cell(n): self.edge_walls.add(n)
        for blk in [b for b in self.warp_units if b in lock]: del self.warp_units[blk]
        for blk in [b for b in self.door_units if b in lock]: del self.door_units[blk]
        for blk in [b for b in getattr(self, 'sign_units', {}) if b in lock]: del self.sign_units[blk]
        self.warp_spots = [w for w in self.warp_spots if (w.x // 2, w.y // 2) not in lock or True]
        from maprando.model.zones import cell_edges
        self.cell_edges = cell_edges(mm, s)
        # cells holding a gate object (rock/boulder/conditional NPC) form 1-cell zones:
        # collapse them into gates between the zones on either side
        objz = {}
        def gate_bits(c):
            o = mm.objects.get(c)
            if o and o[0] == 'bits': return o[1]
            for bit, c2 in mm.alt.get(c, ()):
                if bit in (CUT, WHIRLPOOL) and classify(mm, mm.coll[c]) is None: return bit
            return None
        for z in list(R):
            cs = zg.zones[z]
            bs = {gate_bits(c) for c in cs}
            if None not in bs and len(bs) == 1:
                objz[z] = bs.pop()
        edges = set()
        for a, b, r in zg.edges:
            if a in objz or b in objz: continue
            edges.add((a, b, r))
        self.objz_pairs = defaultdict(set)   # (a, b) -> {(gate zone, requirement a->b through it)}
        for g, bits in objz.items():
            ins = {(a, r) for a, b, r in zg.edges if b == g}
            outs = {(b, r) for a, b, r in zg.edges if a == g}
            for a, r in ins:
                for b, r2 in outs:
                    if a != b:
                        edges.add((a, b, r | r2)); self.objz_pairs[(a, b)].add((g, r | r2))
        self.objz = objz
        self.Z = [z for z in self.Z if z not in objz]
        # gates between placeable zones
        best = {}
        multi = defaultdict(set)     # (a, b) -> other gates that are not comparable with best
        for a, b, r in edges:
            if a not in self.Z or b not in self.Z: continue
            k = (a, b)
            if k not in best: best[k] = r
            elif r & ~best[k] == 0: best[k] = r
            elif best[k] & ~r != 0: multi[k].add(r)
        # two different gates between the same zones (e.g. two story NPCs in two gaps): keep every
        # gate between them as in vanilla, with its surroundings, and generate the rest around it
        for (a, b), rs in multi.items():
            rs = {best[(a, b)]} | rs
            best[(a, b)] = min(rs, key=lambda r: bin(r).count('1'))
            zo = zg.zone_of
            gates = {g for g, r in self.objz_pairs.get((a, b), ())} | {g for g, r in self.objz_pairs.get((b, a), ())}
            cells = [c for g in gates for c in zg.zones[g]]
            for c, es in self.cell_edges.items():
                for d, r in es:
                    if {zo.get(c), zo.get(d)} == {a, b}: cells += [c, d]
            for c in cells:
                x, y = c % mm.w, c // mm.w
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        bx, by = (x + dx) // 2, (y + dy) // 2
                        if 0 <= x + dx < mm.w and 0 <= y + dy < mm.h: self.lock.add((bx, by))
            self.multi_gates = getattr(self, 'multi_gates', [])
            self.multi_gates += [('gate', a, b, r) for r in rs if r != best[(a, b)]]
        self.pairs = []  # (type, a, b, bits)
        seen = set()
        kept = {k for k in multi} | {(b, a) for a, b in multi}
        for (a, b), r in best.items():
            if (a, b) in seen: continue
            if (a, b) in kept: continue      # kept as in vanilla (locked above)
            ka, kb = self.kind[a], self.kind[b]
            rb = best.get((b, a))
            seen.add((a, b)); seen.add((b, a))
            if ka != kb:
                land, water = (a, b) if ka == 'land' else (b, a)
                if all(x is None or x & ~SURF for x in (r, rb)):
                    # every way between them also needs a gate (e.g. a cut tree at the shore):
                    # only that gate, kept in place, can realise it
                    self.pairs.append(('xgate', land, water, (r if r is not None else 0) | (rb or 0)))
                else:
                    self.pairs.append(('surf', land, water, 0))
            elif ka == 'water':
                if r & WATERFALL: self.pairs.append(('waterfall', a, b, 0))      # a lower, b upper
                elif rb is not None and rb & WATERFALL: self.pairs.append(('waterfall', b, a, 0))
                elif r & WHIRLPOOL or (rb is not None and rb & WHIRLPOOL): self.pairs.append(('whirl', a, b, r))
                else: raise GenError(f'water-water edge without waterfall {a}->{b}')
            else:
                if rb is not None and rb == r and r != 0:
                    self.pairs.append(('gate', a, b, r))
                else:
                    for x, y, rr in ((a, b, r), (b, a, rb)):
                        if rr is None: continue
                        if rr == 0: self.pairs.append(('ledge', x, y, 0))
                        else:
                            other = best.get((y, x))
                            if other == 0: self.pairs.append(('gate', x, y, rr))
                            else: raise GenError(f'one-way gated edge {x}->{y}')
        # zone cell counts and anchors per zone
        self.size = {z: len(zg.zones[z]) for z in self.Z}
        self.anchor_zones = {}
        for z, ids in zg.anchors.items():
            for i in ids:
                if z in self.Z: self.anchor_zones.setdefault(i, set()).add(z)
        # the zone a gate NPC stands in is the one its talk-cells border, not its own cell
        
        # wall NPCs talked to from two land zones: a 1-cell gap between the zones holds the NPC
        self.touch_objs = {}
        for o in self.m.objects:
            c = mm.idx(o.x, o.y)
            if mm.objects.get(c, ('',))[0] not in ('wall', 'bits'): continue
            if c in zg.zone_of and zg.zone_of[c] in objz and zg.zone_of[c] in R: continue
            S = self.anchor_zones.get(f'obj:{o.const or o.index}', set())
            lands = sorted(z for z in S if self.kind.get(z) == 'land')
            if len(lands) == 2 and len(S) == 2:
                self.pairs.append(('touch', lands[0], lands[1], 0)); self.touch_objs[(lands[0], lands[1])] = o
            elif len(lands) > 2:
                raise GenError('NPC touches more than two zones')
        # hidden items whose own area has no room for a dead end (a tiny pocket inside a part kept
        # as in vanilla: Tin Tower 5F) or that are reached from two areas (an item in the wall
        # between them: Route 9, Dragon's Den B1F) may sit at a dead end in any land area that
        # needs exactly the same, from every entrance, as the item does
        self._retarget_items()
        # hidden items picked up from two separate land zones: if one zone is never harder to reach
        # than the other (from every entrance), the item can sit at a dead end in that zone alone
        # and still needs the same; otherwise it sits in the wall between them
        for b in self.m.bg_events:
            if b.kind != 'BGEVENT_ITEM': continue
            S = self.anchor_zones.get(f'bg:{b.index}', set())
            lands = sorted(z for z in S if self.kind.get(z) == 'land')
            if len(lands) == 2 and len(S) == 2 and (b.x // 2, b.y // 2) not in self.lock:
                z = self._easier_zone(*lands)
                if z is not None: self.anchor_zones[f'bg:{b.index}'] = {z}; continue
            lands = sorted(z for z in S if self.kind.get(z) == 'land')
            if len(lands) == 2 and len(S) == 2 and tuple(lands) not in self.touch_objs:
                self.pairs.append(('touch', lands[0], lands[1], 0)); self.touch_objs[tuple(lands)] = b
        # gate objects pooled by requirement bits
        self.gate_pool = defaultdict(list)
        for cell, (kind, bits, o) in mm.objects.items():
            if kind == 'bits': self.gate_pool[bits].append(o)

    def _lock_scripted_paths(self, src, lock):
        """Scripted scenes are laid out on the vanilla map (maprando/scenes.py). Every path an
        object or the player walks in a script keeps its vanilla blocks (a scripted walk into a
        wall never finishes and the game hangs: Slowpoke Well B1F's Kurt after the last grunt),
        and an object whose own script stages a scene keeps its spot and surroundings, so the
        player stands where the scene expects."""
        from maprando.checks.scenes import scripted_paths, scene_starters
        def add_cell(x, y, margin=0):
            for dx in range(-margin, margin + 1):
                for dy in range(-margin, margin + 1):
                    bx, by = (x + dx * 2) // 2, (y + dy * 2) // 2
                    if 0 <= bx < self.Wb and 0 <= by < self.Hb: lock.add((bx, by))
        self.scripted_paths = scripted_paths(self.m, src)
        for name, path, margin in self.scripted_paths:
            # turning on the spot (Fuchsia Gym's ninja spin) works wherever the object stands
            if name != 'PLAYER' and len(set(path)) == 1: continue
            for (px, py) in path:
                if 0 <= px < self.mm.w and 0 <= py < self.mm.h: add_cell(px, py, margin)
        for o in scene_starters(self.m, src): add_cell(o.x, o.y, margin=1)

    def _zone_reqs(self):
        """{zone: (per entrance: minimal masks to reach any cell of the zone)} (cached)."""
        if getattr(self, '_zreq', None) is not None: return self._zreq
        from maprando.model.reach import Searcher, entry_starts, minimize
        mm, zg = self.mm, self.zg
        s = Searcher(mm)
        per = []
        for e in mm.anchors:
            if not e.is_entry: continue
            per.append(s.search(entry_starts(mm, e)))
        self._zreq = {z: tuple(tuple(sorted(minimize([mk for c in zg.zones[z] for mk in sets.get(c, ())])))
                               for sets in per) for z in self.Z}
        return self._zreq

    def _retarget_items(self):
        from maprando.model.reach import minimize
        mm = self.mm
        ent = [e.id for e in mm.anchors if e.is_entry]
        def free_cells(z):
            return sum(1 for c in self.zg.zones[z] if ((c % mm.w) // 2, (c // mm.w) // 2) not in self.lock)
        for b in self.m.bg_events:
            if b.kind != 'BGEVENT_ITEM' or not mm.inb(b.x, b.y): continue
            if mm.objects.get(mm.idx(b.x, b.y), ('', 0))[:2] == ('bits', ROCK_SMASH): continue   # under a rock
            aid = f'bg:{b.index}'
            S = self.anchor_zones.get(aid, set())
            lands = [z for z in S if self.kind.get(z) == 'land']
            roomy = [z for z in lands if free_cells(z) >= 6]
            if not lands: continue                              # reached by surfing: a water inlet, as before
            if len(S) == 1 and roomy: continue                  # its own area has room: as before
            want = tuple(tuple(sorted(minimize(self.matrix.get(e, {}).get(aid, []) or []))) for e in ent)
            if not any(want): continue
            zr = self._zone_reqs()
            cands = [z for z in self.Z if self.kind.get(z) == 'land' and zr.get(z) == want and free_cells(z) >= 6]
            if not cands: continue
            # its own areas first, then the roomiest
            cands.sort(key=lambda z: (z not in S, -free_cells(z)))
            self.anchor_zones[aid] = {cands[0]}

    def _easier_zone(self, a, b):
        """a zone of the two that, from every entrance, is reachable with no more than the other
        needs (so an item reached from both can move into it alone), or None."""
        from maprando.model.reach import Searcher, entry_starts, minimize
        mm, zg = self.mm, self.zg
        s = Searcher(mm)
        req = {a: [], b: []}
        for e in mm.anchors:
            if not e.is_entry: continue
            sets = s.search(entry_starts(mm, e))
            r = {z: minimize([mk for c in zg.zones[z] for mk in sets.get(c, ())]) for z in (a, b)}
            for z in (a, b): req[z].append(r[z])
        def no_harder(x, y):     # every way into y has a way into x needing a subset of it
            return all(all(any(mx & ~my == 0 for mx in rx) for my in ry) for rx, ry in zip(req[x], req[y]))
        for z, o in ((a, b), (b, a)):
            if no_harder(z, o): return z
        return None

    def _movable_warps(self):
        """warps that may move: self.warp_units {block: [warps]} are self-contained warp blocks
        (ladder, stairs, hole, warp panel: the block holds only floor, wall and the warp tiles)
        that move as a whole block; self.warp_spots are arrival spots on plain floor (landing
        from a hole above) that only change coordinates. Doors, cave mouths, map-edge exits and
        anything near a script, sign, object or Fly destination stay where they are."""
        mm, zg, m = self.mm, self.zg, self.m
        self.warp_units, self.warp_spots = {}, []
        # doors (a house's door block: door tile, walls around it): only the door matters for the
        # logic, so the block moves on its own, into the wall right above a floor block of the area
        # you step out into; the rest of the house is just wall the generator may change
        self.door_units = {}
        # map scripts that read the player's coordinates on entry (Route 16: on Cycling Road you
        # are kept on the bike) decide by where the warps put you, so those warps stay put
        import re as _re
        if _re.search(r'VAR_[XY]COORD', m.script):
            self.warp_spot_zone = {}; return
        near = set()
        for x, y in ([(e.x, e.y) for e in m.coord_events] + [(b.x, b.y) for b in m.bg_events] +
                     [(o.x, o.y) for o in m.objects] + list(getattr(self.d, 'spawns', {}).get(m.name, []))):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1): near.add((x // 2 + dx, y // 2 + dy))
        # a door has nothing to do with the house sign or the people standing by it (the sign keeps
        # its own block, people are placed anew): only scripts that fire by the door (coord events)
        # and Fly landing spots keep it
        near_door = set()
        for x, y in [(e.x, e.y) for e in m.coord_events] + list(getattr(self.d, 'spawns', {}).get(m.name, [])):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1): near_door.add((x // 2 + dx, y // 2 + dy))
        edge_rows = set()
        for (dname, _o, _c, _off) in m.connections:
            if dname in ('north', 'south'):
                edge_rows |= {(bx, 0 if dname == 'north' else self.Hb - 1) for bx in range(self.Wb)}
            else:
                edge_rows |= {(0 if dname == 'west' else self.Wb - 1, by) for by in range(self.Hb)}
        by_blk = defaultdict(list)
        for w in m.warps:
            if mm.inb(w.x, w.y): by_blk[(w.x // 2, w.y // 2)].append(w)
        for blk, ws in by_blk.items():
            bid = m.blocks[blk[1] * self.Wb + blk[0]]
            pat = self.cat.pattern[bid]
            colls = {mm.coll[mm.idx(w.x, w.y)] for w in ws}
            # on the map's edge row only a door moves (Route 29's gatehouse to Route 46): the edge
            # it leaves is generated like any other (verify checks the crossings stay as in vanilla)
            if blk in edge_rows and colls != {0x71}: continue
            # a cave mouth on a route (Mt. Mortar, Union Cave, Dark Cave entrances): the cave tile
            # in a row of wall, entered walking up and left stepping down, like a door; the tiles
            # above it in the block are the cliff top (floor nobody reaches)
            cave = colls == {0x7b} and all(w.y % 2 == 1 for w in ws) and set(pat[2:]) <= {'W', '#'} \
                and pat.count('W') == len(ws)
            if blk in (near_door if colls == {0x71} or cave else near): continue
            cells = [mm.idx(blk[0] * 2 + (q & 1), blk[1] * 2 + (q >> 1)) for q in range(4)]
            if (colls == {0x71} and set(pat) <= {'W', '#'} and all(w.y % 2 == 1 for w in ws)) or cave:
                if cave: self.cave_bids = getattr(self, 'cave_bids', set()) | {bid}
                # a door: entered walking up, left stepping down onto the tile below it
                below = {zg.zone_of.get(mm.idx(w.x, w.y + 1)) for w in ws if mm.inb(w.x, w.y + 1)}
                if len(below) == 1 and next(iter(below)) in self.kind and self.kind[next(iter(below))] == 'land':
                    self.door_units[blk] = (ws, bid, next(iter(below)))
                continue
            if colls <= {0x72, 0x60, 0x7c} and set(pat) <= {'.', 'W', '#'} and 'W' in pat and '.' in pat:
                # every warp tile in the block must be one of these warps
                if sum(1 for c in cells if self.cat.pattern[bid][cells.index(c)] == 'W') != len(ws): continue
                zs = {zg.zone_of.get(c) for c in cells if pat[cells.index(c)] == '.'}
                if len(zs) == 1 and next(iter(zs)) in self.kind and self.kind[next(iter(zs))] == 'land':
                    self.warp_units[blk] = (ws, bid, next(iter(zs)))
            elif all(mm.coll[mm.idx(w.x, w.y)] == 0x00 for w in ws):     # arrival spots on plain floor
                for w in ws:
                    z = zg.zone_of.get(mm.idx(w.x, w.y))
                    if z in self.kind and self.kind[z] == 'land': self.warp_spots.append((w, z))
        self.warp_spots = [w for w, z in self.warp_spots]
        self.warp_spot_zone = {}
        for w in self.warp_spots: self.warp_spot_zone[w.index] = zg.zone_of.get(mm.idx(w.x, w.y))
        # plain signs (model.plain_signs: they only show text) move with their block: a sign
        # block (signpost tile, floor around it) goes to a floor block of the area it is read from
        from maprando.model.movement import plain_signs
        self.sign_units = {}
        for b in m.bg_events:
            if b.index not in plain_signs(m) or not mm.inb(b.x, b.y): continue
            blk = (b.x // 2, b.y // 2)
            bid = m.blocks[blk[1] * self.Wb + blk[0]]
            pat = self.cat.pattern[bid]
            q = (b.x & 1) + 2 * (b.y & 1)
            if pat[q] != '#' or '.' not in pat or blk in self.sign_units: continue
            if any(blk in by_blk for _ in (0,)): continue          # (a warp in the same block)
            # read from below (else any open side): that tile's area
            front = [(b.x + dx, b.y + dy) for dx, dy in ((0, 1), (0, -1), (-1, 0), (1, 0))
                     if mm.inb(b.x + dx, b.y + dy) and zg.zone_of.get(mm.idx(b.x + dx, b.y + dy)) in self.kind]
            if not front: continue
            z = zg.zone_of.get(mm.idx(*front[0]))
            if self.kind.get(z) != 'land': continue
            self.sign_units[blk] = (b, bid, z, (front[0][0] - b.x, front[0][1] - b.y))

    def place_warps(self, rng):
        """put every movable warp block at the end of a branch of its zone."""
        self.warp_place = {}         # warp index -> new (x, y)
        self.warp_blocks = {}        # block -> vanilla warp block id
        pk = getattr(self, 'pockets', set())
        for blk, (ws, bid, z) in sorted(self.warp_units.items()):
            if blk in self.lock: continue        # kept at its vanilla spot (see WarpRoomError)
            reg = [b for b in self.region[z] if self.G[b[1]][b[0]] == ('Z', z)]
            if not reg: raise WarpRoomError(blk, 'no room for a warp')
            def deg(b):
                return sum(1 for dx, dy in D4 if self.inb(b[0] + dx, b[1] + dy) and
                           self.G[b[1] + dy][b[0] + dx] not in (None,))
            cand = [b for b in reg if b in pk] or [b for b in reg if deg(b) == 1] or reg
            # not next to another warp block
            cand = [b for b in cand if not any((b[0] + dx, b[1] + dy) in self.warp_blocks for dx, dy in D4)] or cand
            nb = rng.choice(sorted(cand))
            self.G[nb[1]][nb[0]] = ('WB', z, bid)
            self.region[z] = [b for b in self.region[z] if b != nb]
            self.warp_blocks[nb] = bid
            for w in ws:
                self.warp_place[w.index] = (nb[0] * 2 + (w.x % 2), nb[1] * 2 + (w.y % 2))
        self.door_below = set()      # floor cells right below a moved door: they stay floor
        for blk, (ws, bid, z) in sorted(self.door_units.items()):
            if blk in self.lock: continue
            # the door block goes into unclaimed wall right above a floor block of its area
            cand = [b for b in self.region[z] if self.G[b[1]][b[0]] == ('Z', z) and b[1] > 0
                    and self.G[b[1] - 1][b[0]] is None and (b[0], b[1] - 1) not in self.lock
                    and (b[0], b[1] - 1) not in self.warp_blocks]
            if not cand: raise WarpRoomError(blk, 'no wall for a door')
            fb = rng.choice(sorted(cand))
            nb = (fb[0], fb[1] - 1)
            self.G[nb[1]][nb[0]] = ('WB', z, bid)
            self.warp_blocks[nb] = bid
            for w in ws:
                x, y = nb[0] * 2 + (w.x % 2), nb[1] * 2 + (w.y % 2)
                self.warp_place[w.index] = (x, y)
                self.door_below.add((x, y + 1))
        self.sign_place = {}         # bg event index -> new (x, y)
        for blk, (b, bid, z, fr) in sorted(getattr(self, 'sign_units', {}).items() if getattr(self, 'allow_signs', True) else []):
            def front_ok(r):
                # the tile it is read from: inside the block, or in a floor block of the same area
                fx, fy = r[0] * 2 + (b.x % 2) + fr[0], r[1] * 2 + (b.y % 2) + fr[1]
                fb = (fx // 2, fy // 2)
                inner = lambda q: 1 <= q[0] < self.Wb - 1 and 1 <= q[1] < self.Hb - 1   # (the border is wall)
                if not inner(r): return False
                if fb == r: return True
                return inner(fb) and self.G[fb[1]][fb[0]] == ('Z', z) and fb not in self.warp_blocks
            reg = [r for r in self.region.get(z, []) if self.G[r[1]][r[0]] == ('Z', z) and front_ok(r)
                   and not any((r[0] + dx, r[1] + dy) in self.warp_blocks for dx, dy in D4)]
            # no room (or a small area people need, like Dragon's Den's shrine island): the sign
            # stays where it was (unlocked)
            if not reg or len(self.region.get(z, [])) < 6: continue
            def deg(r):
                return sum(1 for dx, dy in D4 if self.inb(r[0] + dx, r[1] + dy) and
                           self.G[r[1] + dy][r[0] + dx] not in (None,))
            # a block with room around it (the sign's open sides stay part of the area)
            cand = [r for r in reg if deg(r) >= 2] or reg
            nb = rng.choice(sorted(cand))
            self.G[nb[1]][nb[0]] = ('WB', z, bid)
            self.region[z] = [r for r in self.region[z] if r != nb]
            self.warp_blocks[nb] = bid
            x, y = nb[0] * 2 + (b.x % 2), nb[1] * 2 + (b.y % 2)
            self.sign_place[b.index] = (x, y)
            self.door_below.add((x + fr[0], y + fr[1]))      # the tile it is read from stays floor
