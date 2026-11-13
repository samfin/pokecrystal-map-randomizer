"""Cave layout generator.

1. Condense vanilla into a zone graph (zones + labelled gates).
2. Lock warp/coord-event blocks; seed their zones there.
3. Place every other zone next to an already placed neighbour, building the gate device
   between them (Surf: direct land/water contact; Rock/Boulder/NPC: 1-cell passage holding
   the object; ledge: one-way ledge block; waterfall: waterfall block).
4. Grow zones towards their vanilla size while keeping a wall between zones that must not touch.
5. Roughen edges, place objects/hidden items, pick concrete blocks, then verify that the
   requirement matrix equals vanilla. Any failure raises GenError and the caller retries."""
import copy, random
from collections import defaultdict, deque
from .model import build_model, SURF, WATERFALL, CUT, WHIRLPOOL, ROCK_SMASH, classify, side_walls, LEDGE_DIRS, DIRS, open_tile

NORM = {',': '.', 'H': '#', 'T': '#'}   # plan view of grass / headbutt / cut-tree cells
from .zones import ZoneGraph, medium
from .reach import requirement_matrix

D4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]

LEDGE_PATTERNS = {(0, 1): ('LD', 'LD', '#', '#'), (1, 0): ('LR', '#', 'LR', '#'), (-1, 0): ('#', 'LL', '#', 'LL')}
LEDGE_CELLS = {(0, 1): (0, 1), (1, 0): (0, 2), (-1, 0): (1, 3)}   # quadrant indices holding ledge cells

class GenError(Exception):
    pass

class UnifyError(GenError):
    def __init__(self, z):
        super().__init__('cannot unify zone'); self.z = z

class SignError(GenError):
    """moving the signs found nothing in its share of the tries: generate with them in place."""

class MergeError(GenError):
    """merging ledge steps found nothing in its share of the tries: generate without it."""

class SplitError(GenError):
    """the ledge-loop split found nothing in its share of the tries: generate without it."""

def trigger_matrix(mm, m, src):
    """the requirement matrix with every gate trigger (a coord event whose scene moves the player)
    as a wall: what you reach without stepping on one. None if the map has none."""
    from .scenes import gate_triggers
    from .reach import requirement_matrix
    trig = [e for e in gate_triggers(m, src) if mm.inb(e.x, e.y)]
    if not trig: return None
    saved = dict(mm.objects)
    try:
        for e in trig: mm.objects[mm.idx(e.x, e.y)] = ('wall', 0, None)
        # fixed places only (warps, edges, Fly spots, coord events): items and people are placed
        # anew on purpose, and the usual matrix already covers what each needs
        fixed = lambda a: not (a.startswith('bg:') or a.startswith('obj:'))
        return {e: {a: v for a, v in row.items() if fixed(a)}
                for e, row in requirement_matrix(mm, boulders=False).items() if fixed(e)}
    finally:
        mm.objects.clear(); mm.objects.update(saved)

def guarded_actors(m):
    """cutscene actors with no talk script (a trainer-type object whose script is ObjectEvent:
    Burned Tower 1F's rival): talked to, the game crashes, so only a coord event may reach them"""
    return [o for o in m.objects if o.obj_type == 'OBJECTTYPE_TRAINER' and o.script == 'ObjectEvent']

def guard_leaks(mm2, m2, m=None):
    """guarded actors someone can walk up to from the map's entrances without stepping on a coord
    event (Cut and Rock Smash allowed, other people out of the way; warps that lead to a floor below
    do not count as ways in: you come up them only after the scene)"""
    from .model import open_tile
    m = m or m2
    acts = guarded_actors(m)
    if not acts or not m2.coord_events: return []
    W = mm2.w
    trig = {mm2.idx(e.x, e.y) for e in m2.coord_events}
    # warps fire only on warp tiles (a hole, a door): leftover warps on plain floor are just floor
    from .model import WARP_TILES
    warps = {mm2.idx(w.x, w.y) for w in m2.warps if mm2.inb(w.x, w.y) and mm2.coll[mm2.idx(w.x, w.y)] in WARP_TILES}
    pos = {o.index: mm2.idx(o.x, o.y) for o in m2.objects}
    act_cells = {pos[o.index] for o in acts}
    def ok(c): return 0 <= c < W * mm2.h and open_tile(mm2, c) and c not in trig and c not in warps and c not in act_cells
    def nb(c):
        x = c % W
        return [n for n in (c - 1 if x > 0 else -1, c + 1 if x < W - 1 else -1, c - W, c + W) if 0 <= n < W * mm2.h]
    starts = [n for w in m2.warps if w.dest_map != m2.const and not ('B1F' in w.dest_map or 'BASEMENT' in w.dest_map)
              and mm2.inb(w.x, w.y) and mm2.coll[mm2.idx(w.x, w.y)] in WARP_TILES
              for n in nb(mm2.idx(w.x, w.y)) if ok(n)]
    seen, st = set(starts), list(starts)
    while st:
        c = st.pop()
        for n in nb(c):
            if n not in seen and ok(n): seen.add(n); st.append(n)
    return [o.const for o in acts if any(n in seen for n in nb(pos[o.index]))]

def guard_pocket(mm, m):
    """the vanilla pocket round each guarded actor: the tiles reached from beside it without a coord
    event or a firing warp, and every tile touching them (its walls)"""
    from .model import open_tile, WARP_TILES
    W = mm.w
    trig = {mm.idx(e.x, e.y) for e in m.coord_events}
    warps = {mm.idx(w.x, w.y) for w in m.warps if mm.inb(w.x, w.y) and mm.coll[mm.idx(w.x, w.y)] in WARP_TILES}
    def nb(c):
        x = c % W
        return [n for n in (c - 1 if x > 0 else -1, c + 1 if x < W - 1 else -1, c - W, c + W) if 0 <= n < W * mm.h]
    out = set()
    for o in guarded_actors(m):
        a = mm.idx(o.x, o.y); seen = {a}; st = [a]
        while st:
            c = st.pop()
            for n in nb(c):
                if n in seen: continue
                seen.add(n)
                if open_tile(mm, n) and n not in trig and n not in warps: st.append(n)
        out |= seen            # the pocket and what bounds it (walls, the trigger, the hole)
    return out

def standing_in_walls(mm, m, mm2, m2, anchor_zones=None):
    """objects on floor in vanilla (and reached there) that start on a tile nobody can stand on"""
    out = []
    for o0, o in zip(m.objects, m2.objects):
        if not mm.inb(o0.x, o0.y) or not mm2.inb(o.x, o.y): continue
        if classify(mm, mm.coll[mm.idx(o0.x, o0.y)]) is None: continue           # in a wall in vanilla too
        if anchor_zones is not None and not anchor_zones.get(f'obj:{o0.const or o0.index}'): continue  # parked
        if anchor_zones is None and not any(mm2.inb(o.x + dx, o.y + dy) and classify(mm2, mm2.coll[mm2.idx(o.x + dx, o.y + dy)])
                                            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
            continue          # buried out of reach (no open tile beside it): parked on purpose
        if classify(mm2, mm2.coll[mm2.idx(o.x, o.y)]) is None: out.append(o0.const or f'object {o0.index}')
    return out

class NookError(GenError):
    def __init__(self, b, msg):
        super().__init__(msg); self.b = b

class WarpRoomError(GenError):
    def __init__(self, blk, msg):
        super().__init__(msg); self.blk = blk

class PairError(GenError):
    def __init__(self, p, msg):
        super().__init__(msg); self.p = p

class PortConflict(GenError):
    def __init__(self, blk):
        super().__init__(f'port conflict {blk}'); self.blk = blk

def generate_cave(d, m, perms, cat, seed, tries):
    """CaveGenerator.generate, with the ledge-loop split when the map has one, and without it
    if the split finds nothing."""
    g = CaveGenerator(d, m, perms, cat)
    merge = True
    while True:
        try:
            return g.generate(seed, tries) + (g,)
        except SignError:
            flags = {k_: getattr(g, k_) for k_ in ('allow_merge', 'split_cap', 'allow_split') if hasattr(g, k_)}
            g = CaveGenerator(d, m, perms, cat); g.allow_signs = False
            for k_, v_ in flags.items(): setattr(g, k_, v_)
        except MergeError:
            # without merging the ledge steps (and with the loop as it was)
            merge = False
            cap, split = getattr(g, 'split_cap', None), getattr(g, 'allow_split', True)
            signs = getattr(g, 'allow_signs', True)
            g = CaveGenerator(d, m, perms, cat); g.allow_merge = False; g.allow_signs = signs
            g.split_cap, g.allow_split = cap, split
        except SplitError:
            # one part fewer (down to 2), then no loop at all
            k = (getattr(g, 'split_info', None) or {}).get('parts', 2)
            signs = getattr(g, 'allow_signs', True)
            g = CaveGenerator(d, m, perms, cat); g.allow_merge = merge; g.allow_signs = signs
            if k > 2: g.split_cap = k - 1
            else: g.allow_split = False

class CaveGenerator:
    use_corridors = True
    move_warps = True
    best_of = 3
    far_links = True
    @property
    def freq_w(self): return 2.0 if self.route else 0.7
    room = 3.0
    alternate = False    # True: every other try skips the free-space share (best-of keeps the longer walk)
    fill = 0.8      # share of the free blocks land zones may claim before thinning

    def __init__(self, disasm, m, perms, catalog):
        self.d, self.m, self.perms, self.cat = disasm, m, perms, catalog
        self.mm = build_model(disasm, m, perms)
        self.zg = ZoneGraph(self.mm)
        self.matrix = requirement_matrix(self.mm, boulders=False)
        self.Wb, self.Hb = m.width, m.height
        self._analyze()

    # ------------------------------------------------------------------ analysis
    def _analyze(self):
        mm, zg = self.mm, self.zg
        self.route = any(a.kind == 'edge' for a in mm.anchors)
        if self.m.tileset == 'TILESET_FOREST': self.route = True   # forest: tree walls, block-aligned like routes
        if mm.features & {'ice', 'current'}:
            raise GenError(f'unsupported features {mm.features}')
        from .reach import Searcher
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
        from .model import CARPET_DIR
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
        from .model import plain_signs
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
        from .model import COUNTERS
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
        from .jumps import ledge_cells
        self.ledge_lock = {((q % mm.w) // 2, (q // mm.w) // 2) for q in ledge_cells(mm)}
        # Fly destinations stay as in vanilla (with their surroundings)
        for sx, sy in getattr(self.d, 'spawns', {}).get(self.m.name, []):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (sx // 2 + dx, sy // 2 + dy)
                    if 0 <= n[0] < self.Wb and 0 <= n[1] < self.Hb: lock.add(n)
        # objects moved by scripts keep their place and surroundings; so do moveobject targets
        import re as _re
        src = open(self.m.asm_path, encoding='utf-8', errors='replace').read() if self.m.asm_path else ''
        from .scenes import scripted_paths as _sp
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
        from .zones import cell_edges as _ce
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
        from .jumps import bad_jumps
        from .reach import Searcher, entry_starts
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
        from .zones import cell_edges
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
        from .scenes import scripted_paths, scene_starters
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
        from .reach import Searcher, entry_starts, minimize
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
        from .reach import minimize
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
        from .reach import Searcher, entry_starts, minimize
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
        from .model import _read_src
        if m.asm_path and _re.search(r'VAR_[XY]COORD', _read_src(m.asm_path)):
            self.warp_spot_zone = {}; return
        if not self.move_warps: return
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
        from .model import plain_signs
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

    # ------------------------------------------------------------------ helpers
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

    # ------------------------------------------------------------------ placement
    def place(self, rng):
        self.G = [[None] * self.Wb for _ in range(self.Hb)]
        self.reserved = set()
        self.region = defaultdict(list)
        self._front = {}
        self._comp_memo = {}
        self.devices = []   # (type, (bx,by), dir, a, b, bits)
        for (bx, by) in self.lock: self.G[by][bx] = 'LOCK'
        placed = set()
        # ports next to locked blocks
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
        # zones only inside locked blocks count as placed
        for z in self.Z:
            if all(((c % self.mm.w) // 2, (c // self.mm.w) // 2) in self.lock for c in self.zg.zones[z]):
                placed.add(z)
        def seed(z):
            free = [(bx, by) for by in range(self.Hb) for bx in range(self.Wb) if self.can_claim(bx, by, z)]
            if not free: return False
            # away from what is already placed
            def far(b):
                return min([abs(b[0] - r[0]) + abs(b[1] - r[1]) for zz in placed for r in self.region[zz]] or [99])
            top = max(far(b) for b in free)
            self.claim(*rng.choice([b for b in free if far(b) >= top - 2]), z)
            placed.add(z); return True
        if not placed:
            # every doorway moves: start the zone of the first warp somewhere
            zs = [z for _, (ws, bid, z) in sorted(self.warp_units.items())] or [self.Z[0]]
            if not seed(zs[0]): raise GenError('no seeded zone')
        def in_lock(c): return ((c % self.mm.w) // 2, (c // self.mm.w) // 2) in self.lock
        def pre(p):
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
        pending = [p for p in self.pairs if not pre(p)]
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
        progress = True
        while progress:
            progress = False
            for p in list(pending):
                t, a, b, bits = p
                if (a in placed) == (b in placed): continue
                src, dst = (a, b) if a in placed else (b, a)
                ok = self.link(p, src, dst, False, rng)
                if not ok:
                    self.attach_fail = fails + 1
                    raise PairError(p, f'could not attach {p}')
                placed.add(a); placed.add(b); pending.remove(p); progress = True
                self.grow_zone(dst, rng, 0.7)
        missing = [z for z in self.Z if z not in placed]
        while missing:
            # zones joined to the rest only through warps that now move: seed them on free ground
            if not seed(missing[0]): raise GenError(f'unplaced zones {missing}')
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
            missing = [z for z in self.Z if z not in placed]
        self.cycle_pairs = pending   # both ends were placed independently
        # link them while there is still room between the two ends; retry the rest after growth
        late = []
        for p in self.cycle_pairs:
            if p[0] == 'surf' and self.touches(p[1], p[2]): continue
            if not self.link2(p, rng): late.append(p)
        if self.use_corridors:
            # land will be thinned into corridors: give it room to wind first
            for z in self.Z:
                if self.kind[z] == 'land': self.target[z] = round(self.target[z] * self.room_now)
            if self.room_now > 1 and self.fill_now:
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
        far = self.use_corridors and self.far_links
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

    # ------------------------------------------------------------------ corridors
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

    # ------------------------------------------------------------------ cell plan
    def build_plan(self, rng):
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
        P, Zc = self.P, self.Zc
        W, H = len(P[0]), len(P)
        def legal(bx, by):
            pat = tuple(P[by*2 + (q >> 1)][bx*2 + (q & 1)] for q in range(4))
            return self.cat.has(pat)
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
                    if any(c in self.fixed or P[c[1]][c[0]] != '.' for c in cells): continue
                    # only erode towards walls
                    outward = all(any(not (0 <= c[0]+dx < W and 0 <= c[1]+dy < H) or P[c[1]+dy][c[0]+dx] == '#'
                                      for dx, dy in D4) for c in cells)
                    if not outward: continue
                    old = [(c, P[c[1]][c[0]]) for c in cells]
                    for x, y in cells: P[y][x] = '#'
                    floor_left = sum(1 for q in range(4) if P[by*2+(q>>1)][bx*2+(q&1)] != '#')
                    if floor_left < 1 or not legal(bx, by) or not connected(z):
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
            return self.cat.has(pat)
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

    # ------------------------------------------------------------------ objects
    def place_objects(self, rng):
        m2 = copy.deepcopy(self.m)
        P, Zc = self.P, self.Zc
        W, H = len(P[0]), len(P)
        mm = self.mm
        used = set(c for c, _ in self.gate_cells)
        blocked = set()
        terrain = '.'
        locked_all = {(x, y) for (bx, by) in self.lock for x in (bx*2, bx*2+1) for y in (by*2, by*2+1)}
        keep_clear = set(locked_all) | self.boulder_clear
        for w in m2.warps:
            if w.index in self.warp_place: w.x, w.y = self.warp_place[w.index]
        # arrival spots on plain floor (landing from a hole above): somewhere far along their zone
        pk = getattr(self, 'pockets', set())
        for w in m2.warps:
            z = self.warp_spot_zone.get(w.index) if self.warp_spots else None
            if z is None or not any(v.index == w.index for v in self.warp_spots): continue
            cs = [(x, y) for y in range(H) for x in range(W) if P[y][x] == '.' and Zc[y][x] == z
                  and (x, y) not in locked_all and (x, y) not in used]
            inpk = [c for c in cs if (c[0] // 2, c[1] // 2) in pk]
            if not cs: continue          # nowhere else to go: it stays where it is
            w.x, w.y = rng.choice(sorted(inpk or cs)); used.add((w.x, w.y))
        for w in m2.warps:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1): keep_clear.add((w.x + dx, w.y + dy))
        self.keep_clear = keep_clear
        def floor(x, y): return (0 <= x < W and 0 <= y < H and P[y][x] == terrain and (x, y) not in used
                                 and (x, y) not in keep_clear)
        def pieces(cells):
            k, seen = 0, set()
            for c0 in cells:
                if c0 in seen: continue
                k += 1; st = [c0]; seen.add(c0)
                while st:
                    i, j = st.pop()
                    for dx, dy in D4:
                        n = (i+dx, j+dy)
                        if n in cells and n not in seen: seen.add(n); st.append(n)
            return k
        base_cache = {}
        def interior(x, y, z):
            # blocking (x,y) must not split zone z any further (a zone may already be in pieces
            # joined by ledges or locked moves) and must not touch fixed contact cells
            if (x, y) in self.fixed: return False
            key = (z, len(blocked))
            if key not in base_cache:
                cells = {(i, j) for j in range(H) for i in range(W)
                         if Zc[j][i] == z and P[j][i] != '#' and (i, j) not in blocked}
                base_cache[key] = (cells, pieces(cells))
            cells, base = base_cache[key]
            if (x, y) not in cells or len(cells) < 2: return False
            return pieces(cells - {(x, y)}) <= base
        def touch(x, y):
            zs = set()
            for dx, dy in [(0, 0)] + D4:
                if 0 <= x+dx < W and 0 <= y+dy < H and Zc[y+dy][x+dx] is not None and P[y+dy][x+dx] != '#':
                    zs.add(Zc[y+dy][x+dx])
            return zs
        gate_objs = {id(o) for _, o in self.gate_cells if o is not None}
        self.pocket_used = set()
        missing_touch = [o for o in self.touch_objs.values() if id(o) not in gate_objs
                         and (o.x // 2, o.y // 2) not in self.lock]
        if missing_touch: raise GenError('touch NPC not placed')
        for (cx, cy), o in self.gate_cells:
            if o is None: continue
            mo = m2.bg_events[o.index] if hasattr(o, 'kind') else m2.objects[o.index]
            mo.x, mo.y = cx, cy
        locked_cells = {(x, y) for (bx, by) in self.lock for x in (bx*2, bx*2+1) for y in (by*2, by*2+1)}
        def person(o):
            return o.obj_type != 'OBJECTTYPE_ITEMBALL' and 'BOULDER' not in o.sprite and 'ROCK' not in o.sprite
        # everyone who stands somewhere already counts as in the way: people kept at their vanilla
        # spot and people standing in gate gaps
        for o in self.m.objects:
            if person(o) and (o.x, o.y) in locked_cells: blocked.add((o.x, o.y))
        for (cx, cy), o in self.gate_cells:
            if o is not None and not hasattr(o, 'kind') and person(o): blocked.add((cx, cy))
        for o in self.m.objects:
            if id(o) in gate_objs: continue
            mo = m2.objects[o.index]
            if (o.x, o.y) in locked_cells: used.add((o.x, o.y)); continue
            aid = f'obj:{o.const or o.index}'
            S = self.anchor_zones.get(aid)
            terrain = '~' if classify(mm, mm.coll[mm.idx(o.x, o.y)]) in ('water', 'forced') else '.'
            is_wall = mm.objects.get(mm.idx(o.x, o.y), ('',))[0] in ('wall', 'bits')
            # a walking NPC is passable in the model, but it can be boxed in (a wall on one side, a
            # trainer on the other) and then never moves: place it only where standing still cuts
            # nothing, and count it as in the way for everyone placed after it
            walker = not is_wall and person(o)
            if not S:
                # unreachable in vanilla: park it in solid wall with no open cell beside it
                def buried(x, y):
                    return all(not (0 <= x+dx < W and 0 <= y+dy < H) or P[y+dy][x+dx] in ('#', 'V')
                               for dx, dy in D4)
                cands = [(x, y) for y in range(H) for x in range(W)
                         if P[y][x] in ('#', 'V') and (x, y) not in used and buried(x, y) and (x, y) not in locked_cells]
            else:
                cands, lazy = [], []
                for y in range(H):
                    for x in range(W):
                        if not floor(x, y): continue
                        if is_wall:
                            if len(S) == 1:
                                if Zc[y][x] not in S: continue
                                lazy.append((x, y)); continue
                            elif touch(x, y) != S: continue
                        else:
                            if touch(x, y) != S and not (len(S) == 1 and Zc[y][x] in S): continue
                            if walker and (Zc[y][x] is None or not interior(x, y, Zc[y][x])): continue
                        cands.append((x, y))
                if lazy:
                    # the connectivity test is the slow part: test random cells until enough pass
                    rng.shuffle(lazy); zz = next(iter(S))
                    for c in lazy:
                        if interior(c[0], c[1], zz):
                            cands.append(c)
                            if len(cands) >= 25: break
            if not cands and is_wall and S and classify(mm, mm.coll[mm.idx(o.x, o.y)]) is None:
                # no room on the floor: a wall object that stands in a wall in vanilla (fruit tree,
                # NPC behind a counter) can stand in the wall itself, if every open cell beside it
                # belongs to the zones it is reached from (never a trainer or anyone else on floor)
                def sides(x, y):
                    return {Zc[y+dy][x+dx] for dx, dy in D4 if 0 <= x+dx < W and 0 <= y+dy < H
                            and P[y+dy][x+dx] not in ('#', 'V')}
                cands = [(x, y) for y in range(H) for x in range(W)
                         if P[y][x] == '#' and (x, y) not in used and (x, y) not in locked_cells
                         and (x, y) not in keep_clear and sides(x, y) == S]
            if not cands: raise GenError(f'no spot for {aid}')
            if is_wall and S and o.obj_type == 'OBJECTTYPE_TRAINER' and int(o.sight or 0) > 0:
                rng.shuffle(cands)
                safe = next((c for c in cands[:60] if self.trainer_safe(c, o, blocked)), None)
                if safe is None: raise GenError(f'no safe spot for trainer {aid}')
                x, y = safe
            else:
                # item balls go to the end of the side branches made for them
                pk = getattr(self, 'pockets', set())
                inpk = [c for c in cands if (c[0] // 2, c[1] // 2) in pk and (c[0] // 2, c[1] // 2) not in self.pocket_used]
                if o.obj_type == 'OBJECTTYPE_ITEMBALL' and inpk:
                    x, y = rng.choice(inpk); self.pocket_used.add((x // 2, y // 2))
                else:
                    x, y = rng.choice(cands)
            mo.x, mo.y = x, y; used.add((x, y))
            if is_wall or walker: blocked.add((x, y))
        terrain = '.'
        for b in self.m.bg_events:
            mb = m2.bg_events[b.index]
            if id(b) in gate_objs: continue
            if b.index in getattr(self, 'sign_place', {}):     # a sign moved with its block
                mb.x, mb.y = self.sign_place[b.index]; continue
            S = self.anchor_zones.get(f'bg:{b.index}')
            if (b.x, b.y) in locked_cells and not (b.kind == 'BGEVENT_ITEM' and S and
                                                   (len(S) == 1 or len([z for z in S if self.kind.get(z) == 'land']) == 1)):
                continue
            if self.mm.inb(b.x, b.y) and self.mm.objects.get(self.mm.idx(b.x, b.y), ('', 0))[:2] == ('bits', ROCK_SMASH): continue   # under a rock
            if not S: continue
            lands = [z for z in S if self.kind.get(z) == 'land']
            if b.kind == 'BGEVENT_ITEM' and len(lands) == 1 and len(S) == 1:
                # every hidden item sits at the tip of a one-tile dead end
                spot = (self.carve_nook(lands[0], used, locked_cells, rng)
                        or self.make_nook(lands[0], used, locked_cells, rng)
                        # no land dead end: a water tile with land in front of it
                        or self.carve_nook(lands[0], used, locked_cells, rng, tip='~')
                        or self.water_nook(lands[0], used, locked_cells, rng))
                if spot is not None:
                    mb.x, mb.y = spot; used.add(spot); continue
                if (b.x, b.y) in locked_cells: continue     # no room anywhere: stays at its kept vanilla spot
                raise NookError(b, 'no nook for a hidden item')
            if b.kind == 'BGEVENT_ITEM' and not lands and len(S) == 1:
                # reached only by surfing: a one-tile inlet of water, item at its tip
                spot = (self.carve_nook(next(iter(S)), used, locked_cells, rng, terrain='~')
                        or self.make_nook(next(iter(S)), used, locked_cells, rng, terrain='~'))
                if spot is not None:
                    mb.x, mb.y = spot; used.add(spot); continue
            if (b.x, b.y) in locked_cells: continue
            if b.kind == 'BGEVENT_ITEM' and len(lands) == 1:
                # reached from one land area and from the water: a dead end in the land area
                spot = (self.carve_nook(lands[0], used, locked_cells, rng)
                        or self.make_nook(lands[0], used, locked_cells, rng)
                        or self.carve_nook(lands[0], used, locked_cells, rng, tip='~')
                        or self.water_nook(lands[0], used, locked_cells, rng))
                if spot is None:
                    # no nook fits: the most enclosed floor tile at the end of a dead-end branch
                    pk = getattr(self, 'pockets', set())
                    z = lands[0]
                    def opens(x, y): return sum(1 for dx, dy in D4 if 0 <= x+dx < W and 0 <= y+dy < H
                                                and P[y+dy][x+dx] not in ('#', 'V', 'H'))
                    tips = [(x, y) for y in range(H) for x in range(W)
                            if (x // 2, y // 2) in pk and floor(x, y) and Zc[y][x] == z]
                    if not tips:   # no branch made for it: the most enclosed floor tile of its zone
                        tips = [(x, y) for y in range(H) for x in range(W) if floor(x, y) and Zc[y][x] == z]
                    if tips:
                        least = min(opens(*c) for c in tips)
                        spot = rng.choice(sorted(c for c in tips if opens(*c) == least))
                if spot is None and (b.x, b.y) in locked_cells:
                    continue        # its vanilla spot is in a part kept as in vanilla: leave it there
                if spot is not None:
                    mb.x, mb.y = spot; used.add(spot); continue
            if not lands:
                # reached only while surfing: a rock at the water's edge, as in vanilla
                def wet(x, y):
                    zs = set()
                    for dx, dy in D4:
                        i, j = x + dx, y + dy
                        if 0 <= i < W and 0 <= j < H and P[j][i] not in ('#', 'V'): zs.add(Zc[j][i])
                    return zs
                cands = [(x, y) for y in range(H) for x in range(W)
                         if P[y][x] == '#' and (x, y) not in used and (x, y) not in locked_cells
                         and (x, y) not in keep_clear and wet(x, y) == S]
            else:
                cands = [(x, y) for y in range(H) for x in range(W) if floor(x, y) and touch(x, y) >= S and Zc[y][x] in S]
            if not cands: raise GenError('no spot for bg event')
            x, y = rng.choice(cands); mb.x, mb.y = x, y; used.add((x, y))
        self.m2 = m2
        self.fill_stray_nooks(m2, locked_cells)

    def trainer_safe(self, origin, o, blocked):
        """grid version of trainers.unsafe_trainers for one candidate spot: walking up to the
        player from origin must neither split an area nor open the gap it stood in."""
        from .trainers import facings
        P = self.P; W, H = len(P[0]), len(P)
        free = {(x, y) for y in range(H) for x in range(W)
                if P[y][x] not in ('#', 'V') and (x, y) not in blocked and (x, y) != origin}
        def label(cells):
            lab, k = {}, 0
            for c0 in cells:
                if c0 in lab: continue
                k += 1; lab[c0] = k; st = [c0]
                while st:
                    cx, cy = st.pop()
                    for dx, dy in D4:
                        n = (cx + dx, cy + dy)
                        if n in cells and n not in lab: lab[n] = k; st.append(n)
            return lab
        L0 = label(free)
        x, y = origin
        stops = []
        for dx, dy in facings(o):
            for k in range(1, int(o.sight)):
                s = (x + dx * k, y + dy * k); nxt = (s[0] + dx, s[1] + dy)
                if s not in free or P[s[1]][s[0]] != '.' or nxt not in free: break
                stops.append(s)
        if not stops: return True
        if len({L0[(x + dx, y + dy)] for dx, dy in D4 if (x + dx, y + dy) in free}) > 1: return False
        for s in stops:
            F = (free | {origin}) - {s}
            L1 = label(F); seen = {}
            for c in F:
                if c == origin: continue
                if seen.setdefault(L0[c], L1[c]) != L1[c]: return False
        return True

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

    def _vanilla_nook(self, b):
        """is a hidden item's vanilla spot already the tip of a one-tile dead end?"""
        mm = self.mm
        if not mm.inb(b.x, b.y) or classify(mm, mm.coll[mm.idx(b.x, b.y)]) is None: return False
        return sum(1 for dx, dy in D4 if mm.inb(b.x + dx, b.y + dy)
                   and open_tile(mm, mm.idx(b.x + dx, b.y + dy))) == 1

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

    def tables(self):
        """encounter tables for this map: Surf / grass / headbutt (cached on the disassembly)."""
        d = self.d
        if not hasattr(d, '_enc_tables'):
            import re as _re
            def ids(files, macro='map_id'):
                out = set()
                for f in files:
                    for line in d.read('data', 'wild', f):
                        mo = _re.match(r'\s*' + macro + r'\s+(\w+)', line)
                        if mo: out.add(mo.group(1))
                return out
            d._enc_tables = {
                'water': ids(('johto_water.asm', 'kanto_water.asm')),
                'grass': ids(('johto_grass.asm', 'kanto_grass.asm')),
                'headbutt': ids(('treemon_maps.asm',), 'treemon_map'),
            }
        c = self.m.const
        return {k: c in v for k, v in d._enc_tables.items()}

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

    # ------------------------------------------------------------------ blocks
    def pick_blocks(self, rng):
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
        self.refine_blocks(out, rng)
        self.m2.blocks = bytes(out)

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

    # ------------------------------------------------------------------ main
    def generate(self, seed, tries=200):
        last = None
        from collections import Counter
        self.errors = Counter()
        self.attach_fail = 0
        # locks added while retrying (port conflicts, frozen zones) are per call: same seed, same map
        if not hasattr(self, 'lock0'): self.lock0 = set(self.lock)
        self.lock = set(self.lock0)
        if not getattr(self, '_merge_done', False) and getattr(self, 'allow_merge', True):
            self._merge_done = True
            self.merged = self.merge_ledge_steps()
        if not getattr(self, '_split_done', False) and getattr(self, 'allow_split', True):
            self._split_done = True
            self.split_info = self.split_zone(random.Random(f'{seed}-{self.m.name}-split'))
        unify_fail = {}
        k = conflicts = 0
        best, found, budget = None, 0, None
        pair_fails = 0
        ledge_added = False
        while k < tries and (budget is None or k < budget):
            if getattr(self, 'sign_units', None) and getattr(self, 'allow_signs', True) and k >= tries // 4 and best is None:
                raise SignError(f'{self.m.name}: no layout with the signs moved')
            if getattr(self, 'merged', None) and k >= tries // 4 and best is None:
                raise MergeError(f'{self.m.name}: no layout with the ledge steps merged')
            if getattr(self, 'split_info', None) and k >= tries // 4 and best is None:
                raise SplitError(f'{self.m.name}: no layout with the ledge loop')
            if k >= tries // 2 and best is None and not ledge_added:
                # nothing found without them: keep the ledges' surroundings as in vanilla
                ledge_added = True; self.lock |= self.ledge_lock
            self.ledge_close(self.lock)
            rng = random.Random(f'{seed}-{self.m.name}-{k}-{conflicts}')
            k += 1
            # long connections first; nearest ones once they keep failing (or half the tries are gone)
            self.far_links = k <= tries // 2 and pair_fails < 20
            self.room_now = self.room if k <= tries * 3 // 4 else 1.0   # crowded maps: vanilla-sized areas last
            # alternate tries with and without the free-space share: best-of keeps whichever walks longer
            self.fill_now = self.fill if (k % 2 or not self.alternate) else 0.0
            self.gate_pool = defaultdict(list)
            for cell, (kind, bits, o) in self.mm.objects.items():
                if kind == 'bits' and ((o.x // 2, o.y // 2) not in self.lock): self.gate_pool[bits].append(o)
            self.gate_cells = []
            try:
                try:
                    self.place(rng)
                except PortConflict as pc:
                    # not a real attempt: lock the block and retry (bounded)
                    self.lock.add(pc.blk)
                    conflicts += 1
                    if conflicts <= 400: k -= 1
                    raise
                except PairError as pe:
                    pair_fails += 1
                    # a small zone that keeps failing to join its neighbours keeps its vanilla blocks
                    t, a, b, _ = pe.p
                    z = min((a, b), key=lambda q: self.size.get(q, 0))
                    unify_fail[('pair', z)] = unify_fail.get(('pair', z), 0) + 1
                    # small zones give up sooner: freezing a 4-cell islet costs almost nothing
                    if unify_fail[('pair', z)] == min(25, 8 + self.size.get(z, 50) // 2):
                        mm = self.mm
                        for c in self.zg.zones[z]:
                            self.lock.add(((c % mm.w) // 2, (c // mm.w) // 2))
                        # and the vanilla crossing between the two zones, so the pair is kept as is
                        zo = self.zg.zone_of
                        for c, es in self.cell_edges.items():
                            if zo.get(c) not in (a, b): continue
                            for dcell, r in es:
                                if zo.get(dcell) in (a, b) and zo.get(dcell) != zo.get(c):
                                    # the whole straight run (a waterfall climb skips the tiles between)
                                    x0, y0, x1, y1 = c % mm.w, c // mm.w, dcell % mm.w, dcell // mm.w
                                    for xx in range(min(x0, x1), max(x0, x1) + 1):
                                        for yy in range(min(y0, y1), max(y0, y1) + 1):
                                            self.lock.add((xx // 2, yy // 2))
                    raise
                except UnifyError as ue:
                    # a zone that keeps failing to join up (e.g. a corridor ringing a small floor)
                    # keeps its vanilla blocks; everything else is still generated around it
                    unify_fail[ue.z] = unify_fail.get(ue.z, 0) + 1
                    if unify_fail[ue.z] == 25:
                        mm = self.mm
                        for c in self.zg.zones[ue.z]:
                            self.lock.add(((c % mm.w) // 2, (c // mm.w) // 2))
                    raise
                if self.use_corridors: self.corridors(rng)
                self.place_warps(rng)
                self.widen_ledges(rng)
                self.build_plan(rng)
                self.fixed |= getattr(self, 'door_below', set())
                self.erode(rng, check_only=True)
                if not (self.route and self.use_corridors):
                    # routes keep block-aligned corridors: Johto has hardly any half-tree blocks,
                    # so ragged edges would be drawn as cliffs and fences
                    self.erode(rng)
                    self.smooth(rng)
                    self.erode(rng, check_only=True)
                if not self.route: self.voidify()
                self.seal_edges()
                self.place_objects(rng)
                self.decorate(rng)
                self.pick_blocks(rng)
                ok, diff = self.verify()
                if ok:
                    # keep the layout with the longest walks among a few valid ones
                    from .metrics import steps
                    # most hidden items at a dead end first, then the longest walks
                    sc = (self.nook_count(self.last_mm2), sum(steps(self.last_mm2)))
                    if best is None or sc > best[0]: best = (sc, copy.deepcopy(self.m2), k)
                    found += 1
                    if found >= self.best_of: break
                    if budget is None: budget = k + max(k // 2, 10)   # a few more tries for alternatives
                    continue
                last = f'verify: {diff}'
                self.errors['verify'] += 1
            except GenError as e:
                if isinstance(e, NookError):
                    # a hidden item that never finds room for a nook keeps its vanilla spot
                    key = ('nook', e.b.index)
                    unify_fail[key] = unify_fail.get(key, 0) + 1
                    if unify_fail[key] == 15:
                        bx, by = e.b.x // 2, e.b.y // 2
                        for dx in (-1, 0, 1):
                            for dy in (-1, 0, 1):
                                if self.inb(bx + dx, by + dy): self.lock.add((bx + dx, by + dy))
                if isinstance(e, WarpRoomError):
                    # a warp whose zone keeps leaving no spare block for it stays where it is
                    key = ('warp', e.blk)
                    unify_fail[key] = unify_fail.get(key, 0) + 1
                    if unify_fail[key] == 15:
                        self.lock.add(e.blk); self.warp_units.pop(e.blk, None); self.door_units.pop(e.blk, None)
                last = str(e)
                self.errors[str(e).split(' (')[0].split(' {')[0][:40]] += 1
        if best is not None:
            self.m2 = best[1]; self.walk_score = best[0][1]
            return self.m2, best[2]
        raise GenError(f'{self.m.name}: gave up after {tries} tries; last: {last}')

    def terrain_stats(self, mm2):
        """reachable / present water, grass and headbutt trees outside locked blocks."""
        from .reach import Searcher, entry_starts
        sr = Searcher(mm2)
        reach = set(sr.search([st for a in mm2.anchors if a.is_entry for st in entry_starts(mm2, a)]))
        locked = {mm2.idx(x, y) for (bx, by) in self.lock for x in (bx*2, bx*2+1) for y in (by*2, by*2+1)
                  if x < mm2.w and y < mm2.h}
        def near_reach(c):
            x, y = c % mm2.w, c // mm2.w
            return any(mm2.inb(x+dx, y+dy) and mm2.idx(x+dx, y+dy) in reach for dx, dy in D4)
        st = {}
        water = [c for c in range(mm2.w * mm2.h) if classify(mm2, mm2.coll[c]) == 'water']
        st['water_reach'] = any(c in reach for c in water)
        st['water_free'] = [c for c in water if c not in locked]
        occupied = set(mm2.objects)   # an NPC standing on water (swimmers) is not stray water
        st['water_stray'] = [c for c in water if c not in reach and c not in locked and c not in occupied]
        st['forced_free'] = [c for c in range(mm2.w * mm2.h) if classify(mm2, mm2.coll[c]) == 'forced' and c not in locked]
        grass = [c for c in range(mm2.w * mm2.h) if mm2.coll[c] in (0x18, 0x14)]
        st['grass_reach'] = any(c in reach for c in grass)
        st['grass_free'] = [c for c in grass if c not in locked]
        hb = [c for c in range(mm2.w * mm2.h) if mm2.coll[c] == 0x15]
        st['hb_reach'] = any(near_reach(c) for c in hb)
        st['hb_free'] = [c for c in hb if c not in locked]
        return st

    def encounter_rules(self, mm2):
        """encounter table <=> reachable terrain, for Surf (water), grass and headbutt trees.
        A table only demands terrain if vanilla actually has it reachable."""
        tab = self.tables()
        if not hasattr(self, '_van_stats'): self._van_stats = self.terrain_stats(self.mm)
        v, g = self._van_stats, self.terrain_stats(mm2)
        if tab['water']:
            if v['water_reach'] and not g['water_reach']: return 'Surf table but no reachable water'
        elif not v['water_reach'] and (g['water_free'] or g['forced_free']): return 'no Surf table but water'
        if g['water_stray']: return f"{len(g['water_stray'])} unreachable water cells"
        if tab['grass']:
            if v['grass_reach'] and not g['grass_reach']: return 'grass table but no reachable grass'
        elif not v['grass_reach'] and g['grass_free']: return 'no grass table but grass'
        if tab['headbutt']:
            if v['hb_reach'] and not g['hb_reach']: return 'headbutt table but no reachable headbutt tree'
        elif g['hb_free']: return 'no headbutt table but headbutt trees'
        return None

    def zones_reachable(self, mm2):
        """every zone (walkable area) vanilla can reach must be reachable here too, even if it
        holds no check or warp (the requirement table alone would not notice it missing)."""
        from .reach import Searcher, entry_starts
        sr = Searcher(mm2)
        reach = set(sr.search([st for a in mm2.anchors if a.is_entry for st in entry_starts(mm2, a)]))
        seen = set()
        for y, row in enumerate(self.Zc):
            for x, z in enumerate(row):
                if z is not None and mm2.idx(x, y) in reach: seen.add(z)
        missing = [z for z in self.Z if z not in seen and z not in self.objz and z not in self.T]
        return f'zones not reachable: {missing}' if missing else None

    def trainers_ok(self, mm2):
        from .trainers import unsafe_trainers
        if not hasattr(self, 'vanilla_unsafe'):
            self.vanilla_unsafe = {(o.index, o.x, o.y) for o in unsafe_trainers(self.mm)}
        bad = [o for o in unsafe_trainers(mm2) if (o.index, o.x, o.y) not in self.vanilla_unsafe]
        return f'trainer can trap the player: {bad[0].const}' if bad else None

    def boulders_ok(self, mm2):
        # a Strength puzzle whose solution and traps were checked push by push (Cianwood Gym)
        if getattr(self, 'puzzle_checked', False): return None
        from .boulders import bad_boulders
        if not hasattr(self, 'vanilla_bad_boulders'):
            self.vanilla_bad_boulders = {(o.index, o.x, o.y) for o in bad_boulders(self.mm)}
        bad = [o for o in bad_boulders(mm2) if (o.index, o.x, o.y) not in self.vanilla_bad_boulders]
        return f'boulder cannot be pushed out of the way: {bad[0].const}' if bad else None

    def people_ok(self, mm2):
        """with everyone standing on their tile (walking NPCs too), and with any trainer walked up
        to the player, no area may fall apart more than in vanilla."""
        from .blocking import split_pairs, trainer_people_traps
        from .metrics import reachable
        if not hasattr(self, 'vanilla_people'):
            r = reachable(self.mm)
            self.vanilla_people = (split_pairs(self.mm, self.m, r),
                                   {t for t, _ in trainer_people_traps(self.mm, self.m, r)})
        r2 = reachable(mm2)
        if split_pairs(mm2, self.m2, r2) > self.vanilla_people[0]: return 'people block a path'
        bad = [t for t, _ in trainer_people_traps(mm2, self.m2, r2) if t not in self.vanilla_people[1]]
        return f'{bad[0]} can block a path together with someone else' if bad else None

    def nook_count(self, mm2):
        """hidden items standing at the tip of a one-tile dead end."""
        def walk(x, y): return mm2.inb(x, y) and open_tile(mm2, mm2.idx(x, y))   # (cut trees count as open)
        return sum(1 for b in self.m2.bg_events if b.kind == 'BGEVENT_ITEM' and walk(b.x, b.y)
                   and sum(1 for dx, dy in D4 if walk(b.x + dx, b.y + dy)) == 1)

    def hidden_ok(self, mm2):
        """every hidden item reached from one zone and not left at its vanilla spot must be the tip
        of a one-tile dead end in the finished map (no block choice may reopen it)."""
        mm = mm2
        # a cut tree beside the nook counts as open: cut, it would make the nook a way on
        def walk(x, y): return mm.inb(x, y) and open_tile(mm, mm.idx(x, y))
        for b0, b in zip(self.m.bg_events, self.m2.bg_events):
            if b0.kind != 'BGEVENT_ITEM' or (b.x, b.y) == (b0.x, b0.y): continue
            S = self.anchor_zones.get(f'bg:{b0.index}', ())
            if len([z for z in S if self.kind.get(z) == 'land']) > 1: continue   # in the wall between two areas
            if len(S) != 1 and not any(self.kind.get(z) == 'land' for z in S): continue
            if not walk(b.x, b.y): continue          # a rock at the water's edge
            if sum(1 for dx, dy in D4 if walk(b.x + dx, b.y + dy)) != 1:
                return 'hidden item is not at the tip of a dead end'
        return None

    def signs_ok(self, mm2):
        """a sign moved with its block can still be read: the tile it is read from is floor"""
        for i, (x, y) in getattr(self, 'sign_place', {}).items():
            u = next((u for u in self.sign_units.values() if u[0].index == i), None)
            if u is None: continue
            fx, fy = x + u[3][0], y + u[3][1]
            if not mm2.inb(fx, fy) or classify(mm2, mm2.coll[mm2.idx(fx, fy)]) != 'land':
                return f'sign {u[0].script} cannot be read'
        return None

    def triggers_ok(self, mm2):
        """no way round a gate trigger that vanilla does not have (Route 32: a second way south past
        the Cooltrainer who walks you back without the Zephyr Badge)"""
        if not self.m.asm_path: return None
        from .model import _read_src
        if not hasattr(self, '_trig_v'):
            self._trig_src = _read_src(self.m.asm_path)
            self._trig_v = trigger_matrix(self.mm, self.m, self._trig_src)
        if self._trig_v is None: return None
        return None if trigger_matrix(mm2, self.m2, self._trig_src) == self._trig_v else 'a way round a scene trigger'

    def guard_ok(self, mm2):
        bad = guard_leaks(mm2, self.m2, self.m)
        return f'{bad[0]} can be talked to without its scene (crash)' if bad else None

    def standing_ok(self, mm2):
        """nobody who stands on floor in vanilla starts in a wall (objects parked out of reach, and
        fruit trees / counter NPCs that stand in walls in vanilla, are fine)"""
        bad = standing_in_walls(self.mm, self.m, mm2, self.m2, self.anchor_zones)
        return f'{bad[0]} starts in a wall' if bad else None

    def carpets_ok(self, mm2):
        from .model import dead_carpets
        if not hasattr(self, 'vanilla_dead_carpets'): self.vanilla_dead_carpets = set(dead_carpets(self.mm))
        bad = [w for w in dead_carpets(mm2) if w not in self.vanilla_dead_carpets]
        return f'doormat warp {bad[0]} has floor beyond it' if bad else None

    def swap_variants(self, m):
        """the map as scripts can leave it: each changeblock on its own, then all of them at once."""
        out = []
        sets = [[sw] for sw in self.swaps] + ([self.swaps] if len(self.swaps) > 1 else [])
        for group in sets:
            mv = copy.copy(m); b = bytearray(m.blocks)
            for bx, by, blk in group: b[by * self.Wb + bx] = blk
            mv.blocks = bytes(b); out.append((group, mv))
        return out

    def swaps_ok(self):
        """a door opened, a hole revealed or rocks dropped by a script must connect the same way as
        in vanilla (the room behind Team Rocket Base B3F's password door, Burned Tower's hole)."""
        if not self.swaps: return True, None
        if not hasattr(self, '_swap_mats'):
            self._swap_mats = [requirement_matrix(build_model(self.d, mv, self.perms)) for _, mv in self.swap_variants(self.m)]
        for (group, mv), want in zip(self.swap_variants(self.m2), self._swap_mats):
            if requirement_matrix(build_model(self.d, mv, self.perms)) != want:
                return False, f'after changeblock {group}'
        return True, None

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

    def add_ledges(self, rng):
        """one-way shortcuts, as vanilla's ledge routes have: where a wall one block thick has
        walkable ground on both sides, and both sides are already joined the long way round, part
        of the wall becomes a ledge (drop down easily, walk all the way round to get back). Both
        sides joined means the ledge adds no new way anywhere, so the requirements stay the same
        (verify checks; on failure the layout is kept without them). As many ledge runs as vanilla
        has (roughly), longest detours first. Returns the sum of the detours."""
        mm = self.mm
        n_v = sum(1 for c in mm.coll if (c & 0xF0) == 0xA0)
        if not n_v: return 0
        target = min(8, max(2, n_v // 8))
        pats = {d: p for d, p in LEDGE_PATTERNS.items() if self.cat.has(p)}
        if not pats: return 0
        Wb, Hb = self.Wb, self.Hb
        W, H = Wb * 2, Hb * 2
        blocks = bytearray(self.m2.blocks)
        def cls(x, y):
            if not (0 <= x < W and 0 <= y < H): return '#'
            return self.cat.pattern[blocks[(y // 2) * Wb + x // 2]][(y % 2) * 2 + x % 2]
        def walk(x, y): return cls(x, y) in ('.', ',')
        busy = {(o.x // 2, o.y // 2) for o in self.m2.objects} | {(b.x // 2, b.y // 2) for b in self.m2.bg_events} | \
               {(w.x // 2, w.y // 2) for w in self.m2.warps}
        def free_wall(bx, by):
            if not (0 < bx < Wb - 1 and 0 < by < Hb - 1): return False
            if (bx, by) in self.lock or (bx, by) in busy or (bx, by) in getattr(self, 'cut_ids', {}): return False
            if isinstance(self.G[by][bx], tuple) and self.G[by][bx][0] == 'WB': return False
            return all(c == '#' for c in self.cat.pattern[blocks[by * Wb + bx]])
        def bfs(a, b, avoid):
            if not (walk(*a) and walk(*b)): return None
            dist = {a: 0}; q = deque([a])
            while q:
                c = q.popleft()
                if c == b: return dist[c]
                for dx, dy in D4:
                    n = (c[0] + dx, c[1] + dy)
                    if n not in dist and n not in avoid and walk(*n): dist[n] = dist[c] + 1; q.append(n)
            return None
        # candidates: a ledge right across a corridor (a run of floor blocks with wall at both
        # ends), with ground beyond it on both sides; the corridor must be part of a loop, so
        # the way back round exists without it
        def floor_blk(bx, by):
            if not (0 < bx < Wb - 1 and 0 < by < Hb - 1): return False
            if (bx, by) in self.lock or (bx, by) in busy or (bx, by) in getattr(self, 'cut_ids', {}): return False
            if isinstance(self.G[by][bx], tuple) and self.G[by][bx][0] == 'WB': return False
            return all(c == '.' for c in self.cat.pattern[blocks[by * Wb + bx]])
        def wallish(x, y): return not walk(x, y)
        cands = []
        if (0, 1) in pats:
            for by in range(1, Hb - 1):
                bx = 1
                while bx < Wb - 1:
                    run = []
                    while bx < Wb - 1 and floor_blk(bx, by): run.append((bx, by)); bx += 1
                    if 1 <= len(run) <= 5:
                        x0, x1 = run[0][0] * 2, run[-1][0] * 2 + 1
                        ends = all(wallish(x0 - 1, y) and wallish(x1 + 1, y) for y in (by * 2, by * 2 + 1))
                        above = all(walk(x, by * 2 - 1) for x in range(x0, x1 + 1))
                        below = all(walk(x, by * 2 + 2) for x in range(x0, x1 + 1))
                        if ends and above and below: cands.append(((0, 1), run))
                    bx += 1
        for d in ((1, 0), (-1, 0)):
            if d not in pats: continue
            for bx in range(1, Wb - 1):
                by = 1
                while by < Hb - 1:
                    run = []
                    while by < Hb - 1 and floor_blk(bx, by): run.append((bx, by)); by += 1
                    if 1 <= len(run) <= 4:
                        y0, y1 = run[0][1] * 2, run[-1][1] * 2 + 1
                        ends = all(wallish(x, y0 - 1) and wallish(x, y1 + 1) for x in (bx * 2, bx * 2 + 1))
                        xa, xb = (bx * 2 - 1, bx * 2 + 2) if d == (1, 0) else (bx * 2 + 2, bx * 2 - 1)
                        side = all(walk(xa, y) and walk(xb, y) for y in range(y0, y1 + 1))
                        if ends and side: cands.append((d, run))
                    by += 1
        scored = []
        for d, run in cands:
            bx, by = run[0]
            if d == (0, 1): a, b = (bx * 2, by * 2 - 1), (bx * 2, by * 2 + 2)
            elif d == (1, 0): a, b = (bx * 2 - 1, by * 2), (bx * 2 + 2, by * 2)
            else: a, b = (bx * 2 + 2, by * 2), (bx * 2 - 1, by * 2)
            cut = {(x * 2 + q % 2, y * 2 + q // 2) for (x, y) in run for q in range(4)}
            det = bfs(b, a, cut)            # the way back, round the loop, without the ledge's corridor
            if det is None or det < 12: continue
            scored.append((det + rng.random(), d, run, det))
        scored.sort(key=lambda t: -t[0])
        used, total, made = set(), 0, 0
        for _, d, run, det in scored:
            if made >= target: break
            if any((x + dx, y + dy) in used for (x, y) in run for dx in (-1, 0, 1) for dy in (-1, 0, 1)): continue
            pat = pats[d]
            for (bx, by) in run:
                i = by * Wb + bx
                left = blocks[i - 1]; up = blocks[i - Wb]
                b = self.cat.choose(pat, left, up, rng)
                if b is None: break
                blocks[i] = b
            used |= set(run); total += det; made += 1
        self.m2.blocks = bytes(blocks)
        return total

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

    def edges_ok(self, mm2):
        # what matters is that you cannot get there (a tile kept walkable inside a locked block is
        # fine while nothing reaches it): reachable from some way in
        ew = getattr(self, 'edge_walls', ())
        if not ew: return None
        from .reach import Searcher, entry_starts
        sr = Searcher(mm2)
        R = set(sr.search([st for a in mm2.anchors if a.is_entry for st in entry_starts(mm2, a)]))
        if not hasattr(self, '_edge_R0'):
            s0 = Searcher(self.mm)
            self._edge_R0 = set(s0.search([st for a in self.mm.anchors if a.is_entry for st in entry_starts(self.mm, a)]))
        bad = [c for c in ew if c in R and c not in self._edge_R0]
        return f'new way out at the map edge {(bad[0] % mm2.w, bad[0] // mm2.w)}' if bad else None

    def jumps_ok(self, mm2):
        """no ledge jump may land in a wall, on water, on a person or off the map (the game never
        checks where a jump lands: the player is stuck there), beyond what vanilla already has."""
        from .jumps import bad_jumps
        if not hasattr(self, '_van_jumps'): self._van_jumps = {(c, d) for c, d, _ in bad_jumps(self.mm)}
        bad = [(c % mm2.w, c // mm2.w, d) for c, d, _ in bad_jumps(mm2) if (c, d) not in self._van_jumps]
        return f'ledge jump into a wall at {bad[0]}' if bad else None

    def verify(self):
        mm2 = build_model(self.d, self.m2, self.perms)
        self.last_mm2 = mm2
        w = (self.zones_reachable(mm2) or self.encounter_rules(mm2) or self.trainers_ok(mm2)
             or self.boulders_ok(mm2) or self.people_ok(mm2) or self.carpets_ok(mm2) or self.hidden_ok(mm2)
             or self.standing_ok(mm2) or self.guard_ok(mm2) or self.triggers_ok(mm2) or self.signs_ok(mm2)
             or self.edges_ok(mm2)
             or self.jumps_ok(mm2) or self.loop_ok(mm2))
        if w: return False, w
        mat2 = requirement_matrix(mm2, boulders=False)
        if mat2 == self.matrix: return self.swaps_ok()
        for e in self.matrix:
            r1, r2 = self.matrix[e], mat2.get(e, {})
            for a in set(r1) | set(r2):
                if r1.get(a) != r2.get(a):
                    return False, (e, a, r1.get(a), r2.get(a))
        return False, 'entries differ'
