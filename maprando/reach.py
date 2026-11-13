"""Pareto-minimal requirement search: for each entry anchor, the minimal ability sets
needed to reach every other anchor."""
from collections import deque
from .model import (ROCK_SMASH, DIRS, OPP, UP, DOWN, SURF, WATERFALL, FORCED, COLL_WATERFALL, CARPETS, COLL_PIT, COLL_ICE,
                    LEDGE_DIRS, ABILITY_NAMES, classify, side_walls)

def _add(sets, cell, m):
    cur = sets.get(cell)
    if cur is None:
        sets[cell] = [m]; return True
    for e in cur:
        if e & ~m == 0: return False
    sets[cell] = [e for e in cur if m & ~e != 0] + [m]
    return True

def minimize(masks):
    out = []
    for m in sorted(set(masks), key=lambda v: bin(v).count('1')):
        if not any(e & ~m == 0 for e in out): out.append(m)
    return sorted(out)

EJECT_DOWN = {0x71, 0x79, 0x7a, 0x7b}    # COLL_DOOR, COLL_DOOR_79, COLL_STAIRCASE, COLL_CAVE

class Searcher:
    def __init__(self, mm):
        self.mm = mm
        W = mm.w
        self.step = {d: (dx + dy * W) for d, (dx, dy) in DIRS.items()}
        self._ncache = {}
        self.terminal = set()
        for cell in mm.warp_cells:
            c = mm.coll[cell]
            if (0x71 <= c <= 0x7f and c not in CARPETS) or c == COLL_PIT:
                self.terminal.add(cell)

    def eff(self, cell, mask):
        c = self.mm.coll[cell]
        for bit, c2 in self.mm.alt.get(cell, ()):
            if mask & bit: c = c2
        return c

    def move(self, cell, d):
        mm = self.mm
        x, y = cell % mm.w, cell // mm.w
        dx, dy = DIRS[d]
        if not mm.inb(x + dx, y + dy): return None
        return cell + self.step[d]

    def slide(self, cell):
        seen = set()
        while cell not in seen:
            seen.add(cell)
            c = self.mm.coll[cell]
            if c not in FORCED: return cell
            n = self.move(cell, FORCED[c])
            if n is None or classify(self.mm, self.mm.coll[n]) is None: return cell
            cell = n
        return cell

    def ice_slide(self, cell, d):
        """stepping onto ice keeps you moving in direction d until the next tile blocks you (you
        stop on the ice) or you reach a tile that is not ice (you stop there) or a warp."""
        mm = self.mm
        while mm.coll[cell] == COLL_ICE and cell not in self.terminal:
            n = self.move(cell, d)
            if n is None or d in side_walls(mm.coll[cell]): return cell
            if mm.objects.get(n) is not None: return cell          # NPCs, boulders, rocks stop you
            cn = mm.coll[n]
            if classify(mm, cn) != 'land' or OPP[d] in side_walls(cn): return cell
            cell = n
        return cell

    def climb(self, cell):
        while self.mm.coll[cell] == COLL_WATERFALL:
            n = self.move(cell, UP)
            if n is None: return None
            cell = n
        return cell if classify(self.mm, self.mm.coll[cell]) else None

    def options(self, cell):
        c = self.mm.coll[cell]
        yield 0, c
        for bit, c2 in self.mm.alt.get(cell, ()):
            yield bit, c2

    def neighbors(self, cell, mask):
        """moves from cell: [(dest, extra requirement)]. Depends on the mask only through the
        cell's own alternative tiles (cut trees, whirlpools), so results are cached per Searcher."""
        alt = self.mm.alt.get(cell)
        key = (cell, 0) if not alt else (cell, mask & sum(b for b, _ in alt))
        r = self._ncache.get(key)
        if r is None: r = self._ncache[key] = list(self._neighbors(cell, mask))
        return r

    def _neighbors(self, cell, mask):
        mm = self.mm
        cA = self.eff(cell, mask)
        wallsA = side_walls(cA)
        for d in DIRS:
            if d in wallsA: continue
            # arriving on a door, staircase or cave tile, the game walks you one step down
            # (engine/overworld/player_movement.asm: .warps): you never leave it any other way
            if cA in EJECT_DOWN and d != DOWN: continue
            B = self.move(cell, d)
            if B is None: continue
            obj = mm.objects.get(B)
            if obj and obj[0] == 'wall':
                blocked_land = True
            else:
                blocked_land = classify(mm, mm.coll[B]) != 'land' or OPP[d] in side_walls(mm.coll[B])
                for bits, cB in self.options(B):
                    if OPP[d] in side_walls(cB): continue
                    cls = classify(mm, cB)
                    if cls is None: continue
                    req = bits | (obj[1] if obj else 0)
                    dest = B
                    if cls == 'land' and cB == COLL_ICE: dest = self.ice_slide(B, d)
                    if cls == 'water':
                        req |= SURF
                    elif cls == 'forced':
                        if cB == COLL_WATERFALL and d == UP:
                            req |= SURF | WATERFALL
                            dest = self.climb(B)
                            if dest is None: continue
                        else:
                            req |= SURF
                            dest = self.slide(B)
                    yield dest, req
            # ledge jump: only when a normal step is impossible
            if (cA & 0xF0) == 0xA0 and d in LEDGE_DIRS[cA & 7] and blocked_land:
                C = self.move(B, d)
                if C is not None and classify(mm, mm.coll[C]) == 'land' and not (mm.objects.get(C, ('',))[0] == 'wall'):
                    yield C, 0

    def search(self, starts):
        """starts: list of (cell, mask). Returns {cell: [minimal masks]} (standing states)."""
        sets = {}
        q = deque()
        for cell, m in starts:
            if _add(sets, cell, m): q.append((cell, m, True))
        while q:
            cell, m, is_start = q.popleft()
            if m not in sets.get(cell, ()): continue  # superseded
            if cell in self.terminal and not is_start: continue
            for dest, req in self.neighbors(cell, m):
                nm = m | req
                if _add(sets, dest, nm): q.append((dest, nm, False))
        return sets

def entry_starts(mm, anchor):
    starts = []
    for cell in anchor.cells:
        cls = classify(mm, mm.coll[cell])
        starts.append((cell, SURF if cls in ('water', 'forced') else 0))
    return starts

def anchor_masks(mm, sets, anchor):
    masks = []
    cells = set(anchor.cells)
    if anchor.mode in ('adj', 'face'):
        for c in list(cells):
            x, y = c % mm.w, c // mm.w
            for dx, dy in DIRS.values():
                if mm.inb(x + dx, y + dy): cells.add(c + dx + dy * mm.w)
        if anchor.mode == 'face': cells -= set(anchor.cells)
    cells |= set(getattr(anchor, 'via', ()))
    for c in cells:
        masks.extend(sets.get(c, ()))
    if anchor.kind == 'bg':
        # a hidden item under a smashable rock: facing the tile talks to the rock until it is smashed
        # (only rocks: people standing on a hidden item do not change its requirement)
        for c in anchor.cells:
            obj = mm.objects.get(c)
            if obj and obj[0] == 'bits' and obj[1] == ROCK_SMASH: masks = [mk | ROCK_SMASH for mk in masks]
    return minimize(masks)

def boulder_ids(mm):
    """anchor ids of Strength boulders: nothing to collect there, and every way past one is already
    an entry or anchor of its own, so what it takes to reach the boulder itself does not matter."""
    from .model import STRENGTH
    return {f'obj:{o.const or o.index}' for (kind, bits, o) in mm.objects.values()
            if kind == 'bits' and bits == STRENGTH and 'BOULDER' in getattr(o, 'sprite', '')}

def requirement_matrix(mm, boulders=True):
    """{entry_id: {anchor_id: [masks]}}; unreachable anchors are omitted. boulders=False leaves the
    Strength boulders out (as anchors; they still gate everything else)."""
    s = Searcher(mm)
    out = {}
    skip = set() if boulders else boulder_ids(mm)
    for e in mm.anchors:
        if not e.is_entry: continue
        sets = s.search(entry_starts(mm, e))
        row = {}
        for a in mm.anchors:
            if a.id == e.id or a.id in skip: continue
            ms = anchor_masks(mm, sets, a)
            if ms: row[a.id] = ms
        out[e.id] = row
    return out

def mask_names(mm, m):
    names = [n for b, n in ABILITY_NAMES.items() if m & b]
    names += [f'obj:{n}' for b, n in mm.obj_bits.items() if m & b]
    return names
