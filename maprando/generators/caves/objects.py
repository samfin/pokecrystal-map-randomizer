"""Placing people, items and hidden items on the finished plan."""
import copy
from maprando.generators.caves.common import D4, GenError, NookError
from maprando.model.movement import ROCK_SMASH, classify


def _person(o):
    """someone who stands in the way: not an item ball, boulder or rock."""
    return o.obj_type != 'OBJECTTYPE_ITEMBALL' and 'BOULDER' not in o.sprite and 'ROCK' not in o.sprite


def _pieces(cells):
    """the number of 4-connected pieces of a set of cells."""
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


class _Spots:
    """The plan's tiles while objects are placed: what is taken (used), who stands in the way
    (blocked) and which terrain the object being placed stands on."""
    def __init__(self, gen, used, keep_clear):
        self.P, self.Zc, self.fixed = gen.P, gen.Zc, gen.fixed
        self.W, self.H = len(self.P[0]), len(self.P)
        self.used, self.keep_clear = used, keep_clear
        self.blocked = set()
        self.terrain = '.'
        self._base = {}

    def floor(self, x, y):
        """a free tile of the current terrain."""
        return (0 <= x < self.W and 0 <= y < self.H and self.P[y][x] == self.terrain
                and (x, y) not in self.used and (x, y) not in self.keep_clear)

    def interior(self, x, y, z):
        """can someone stand still on (x, y)? Blocking it must not split zone z any further (a zone
        may already be in pieces joined by ledges or locked moves) nor touch fixed contact cells."""
        if (x, y) in self.fixed: return False
        key = (z, len(self.blocked))
        if key not in self._base:
            cells = {(i, j) for j in range(self.H) for i in range(self.W)
                     if self.Zc[j][i] == z and self.P[j][i] != '#' and (i, j) not in self.blocked}
            self._base[key] = (cells, _pieces(cells))
        cells, base = self._base[key]
        if (x, y) not in cells or len(cells) < 2: return False
        return _pieces(cells - {(x, y)}) <= base

    def touch(self, x, y):
        """the zones of (x, y) and its open neighbours."""
        zs = set()
        for dx, dy in [(0, 0)] + D4:
            if 0 <= x+dx < self.W and 0 <= y+dy < self.H and self.Zc[y+dy][x+dx] is not None \
                    and self.P[y+dy][x+dx] != '#':
                zs.add(self.Zc[y+dy][x+dx])
        return zs


class ObjectMixin:
    """People, item balls, rocks, boulders and hidden items placed on the plan."""
    def place_objects(self, rng):
        """a copy of the map with every object, sign, hidden item and moving warp at a new spot in the
        zone it belongs to (gate objects in their gates). Raises GenError if something does
        not fit."""
        m2 = copy.deepcopy(self.m)
        locked_cells = {(x, y) for (bx, by) in self.lock for x in (bx*2, bx*2+1) for y in (by*2, by*2+1)}
        used = set(c for c, _ in self.gate_cells)
        keep_clear = set(locked_cells) | self.boulder_clear
        for w in m2.warps:
            if w.index in self.warp_place: w.x, w.y = self.warp_place[w.index]
        self._place_arrivals(m2, rng, used, locked_cells)
        for w in m2.warps:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1): keep_clear.add((w.x + dx, w.y + dy))
        self.keep_clear = keep_clear
        sp = _Spots(self, used, keep_clear)
        gate_objs = {id(o) for _, o in self.gate_cells if o is not None}
        self.pocket_used = set()
        missing_touch = [o for o in self.touch_objs.values() if id(o) not in gate_objs
                         and (o.x // 2, o.y // 2) not in self.lock]
        if missing_touch: raise GenError('touch NPC not placed')
        for (cx, cy), o in self.gate_cells:
            if o is None: continue
            mo = m2.bg_events[o.index] if hasattr(o, 'kind') else m2.objects[o.index]
            mo.x, mo.y = cx, cy
        # everyone who stands somewhere already counts as in the way: people kept at their vanilla
        # spot and people standing in gate gaps
        for o in self.m.objects:
            if _person(o) and (o.x, o.y) in locked_cells: sp.blocked.add((o.x, o.y))
        for (cx, cy), o in self.gate_cells:
            if o is not None and not hasattr(o, 'kind') and _person(o): sp.blocked.add((cx, cy))
        self._place_people(m2, rng, sp, locked_cells, gate_objs)
        sp.terrain = '.'
        self._place_bg_events(m2, rng, sp, locked_cells, gate_objs)
        self.m2 = m2
        self.fill_stray_nooks(m2, locked_cells)

    def _place_arrivals(self, m2, rng, used, locked_all):
        """arrival spots on plain floor (landing from a hole above): somewhere far along their zone."""
        P, Zc = self.P, self.Zc
        W, H = len(P[0]), len(P)
        pk = getattr(self, 'pockets', set())
        for w in m2.warps:
            z = self.warp_spot_zone.get(w.index) if self.warp_spots else None
            if z is None or not any(v.index == w.index for v in self.warp_spots): continue
            cs = [(x, y) for y in range(H) for x in range(W) if P[y][x] == '.' and Zc[y][x] == z
                  and (x, y) not in locked_all and (x, y) not in used]
            inpk = [c for c in cs if (c[0] // 2, c[1] // 2) in pk]
            if not cs: continue          # nowhere else to go: it stays where it is
            w.x, w.y = rng.choice(sorted(inpk or cs)); used.add((w.x, w.y))

    def _place_people(self, m2, rng, sp, locked_cells, gate_objs):
        """people, item balls, rocks and boulders: each at a spot reached from the same zones as in
        vanilla (trainers where they trap nobody, item balls in the pockets made for them)."""
        P, Zc = self.P, self.Zc
        W, H = len(P[0]), len(P)
        mm, used, blocked, keep_clear = self.mm, sp.used, sp.blocked, sp.keep_clear
        for o in self.m.objects:
            if id(o) in gate_objs: continue
            mo = m2.objects[o.index]
            if (o.x, o.y) in locked_cells: used.add((o.x, o.y)); continue
            aid = f'obj:{o.const or o.index}'
            S = self.anchor_zones.get(aid)
            sp.terrain = '~' if classify(mm, mm.coll[mm.idx(o.x, o.y)]) in ('water', 'forced') else '.'
            is_wall = mm.objects.get(mm.idx(o.x, o.y), ('',))[0] in ('wall', 'bits')
            # a walking NPC is passable in the model, but it can be boxed in (a wall on one side, a
            # trainer on the other) and then never moves: place it only where standing still cuts
            # nothing, and count it as in the way for everyone placed after it
            walker = not is_wall and _person(o)
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
                        if not sp.floor(x, y): continue
                        if is_wall:
                            if len(S) == 1:
                                if Zc[y][x] not in S: continue
                                lazy.append((x, y)); continue
                            elif sp.touch(x, y) != S: continue
                        else:
                            if sp.touch(x, y) != S and not (len(S) == 1 and Zc[y][x] in S): continue
                            if walker and (Zc[y][x] is None or not sp.interior(x, y, Zc[y][x])): continue
                        cands.append((x, y))
                if lazy:
                    # the connectivity test is the slow part: test random cells until enough pass
                    rng.shuffle(lazy); zz = next(iter(S))
                    for c in lazy:
                        if sp.interior(c[0], c[1], zz):
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

    def _place_bg_events(self, m2, rng, sp, locked_cells, gate_objs):
        """signs and hidden items: hidden items at the tip of a one-tile dead end where possible."""
        P, Zc = self.P, self.Zc
        W, H = len(P[0]), len(P)
        used, keep_clear = sp.used, sp.keep_clear
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
                            if (x // 2, y // 2) in pk and sp.floor(x, y) and Zc[y][x] == z]
                    if not tips:   # no branch made for it: the most enclosed floor tile of its zone
                        tips = [(x, y) for y in range(H) for x in range(W) if sp.floor(x, y) and Zc[y][x] == z]
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
                cands = [(x, y) for y in range(H) for x in range(W) if sp.floor(x, y) and sp.touch(x, y) >= S and Zc[y][x] in S]
            if not cands: raise GenError('no spot for bg event')
            x, y = rng.choice(cands); mb.x, mb.y = x, y; used.add((x, y))

    def trainer_safe(self, origin, o, blocked):
        """grid version of trainers.unsafe_trainers for one candidate spot: walking up to the
        player from origin must neither split an area nor open the gap it stood in."""
        from maprando.checks.trainers import facings
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
