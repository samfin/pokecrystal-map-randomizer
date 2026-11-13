"""Movement model for Gen 2 overworld maps, at collision-cell (16x16 px) granularity.

Mirrors engine/overworld/player_movement.asm and home/map.asm (GetMovementPermissions):
  * TileCollisionTable gives LAND / WATER / WALL per collision byte.
  * Side walls ($b0-$b7): standing tile blocks exits on its walled sides; the
    destination tile blocks entry from the opposite side.
  * Ledges ($a0-$a7): a step that would be blocked becomes a 2-cell jump when the
    *standing* tile is a ledge facing that direction.
  * Waterfall/current tiles force movement; climbing COLL_WATERFALL needs Waterfall.
  * Cut trees / whirlpools are block swaps (data/events/field_move_blocks.asm).
"""
import re
from dataclasses import dataclass, field

# requirement bits
SURF, CUT, STRENGTH, ROCK_SMASH, WATERFALL, WHIRLPOOL = 1, 2, 4, 8, 16, 32
ABILITY_NAMES = {SURF: 'Surf', CUT: 'Cut', STRENGTH: 'Strength', ROCK_SMASH: 'Rock Smash',
                 WATERFALL: 'Waterfall', WHIRLPOOL: 'Whirlpool'}
FIRST_OBJ_BIT = 64

DOWN, UP, LEFT, RIGHT = 'D', 'U', 'L', 'R'
DIRS = {DOWN: (0, 1), UP: (0, -1), LEFT: (-1, 0), RIGHT: (1, 0)}
OPP = {DOWN: UP, UP: DOWN, LEFT: RIGHT, RIGHT: LEFT}

LAND, WATER, WALL = 0, 1, 0xF
FORCED = {0x30: RIGHT, 0x31: LEFT, 0x32: UP, 0x33: DOWN, 0x38: RIGHT, 0x39: LEFT, 0x3a: UP, 0x3b: DOWN}
COLL_WATERFALL = 0x33
COLL_WHIRLPOOL = 0x24
COLL_ICE = 0x23
COLL_PIT = 0x60
LEDGE_DIRS = {0: {RIGHT}, 1: {LEFT}, 2: {UP}, 3: {DOWN}, 4: {DOWN, RIGHT}, 5: {DOWN, LEFT}, 6: {UP, RIGHT}, 7: {UP, LEFT}}
SIDE_WALLS = {0: {RIGHT}, 1: {LEFT}, 2: {UP}, 3: {DOWN}, 4: {DOWN, RIGHT}, 5: {DOWN, LEFT}, 6: {UP, RIGHT}, 7: {UP, LEFT}}
# warp tiles that trigger on step (player cannot walk through them)
CARPETS = {0x70, 0x76, 0x78, 0x7e}
COUNTERS = {0x90, 0x98}   # COLL_COUNTER: talk to whoever stands behind it

MOVING_NPC = {'SPRITEMOVEDATA_WANDER', 'SPRITEMOVEDATA_WALK_UP_DOWN', 'SPRITEMOVEDATA_WALK_LEFT_RIGHT',
              'SPRITEMOVEDATA_SWIM_WANDER'}

def load_permissions(disasm):
    perms = []
    for line in disasm.read('data', 'collision_permissions.asm'):
        l = line.split(';')[0].strip()
        if l.startswith('db'):
            v = l[2:].strip()
            base = LAND if 'LAND_TILE' in v else WATER if 'WATER_TILE' in v else WALL
            perms.append(base)
    while len(perms) < 256: perms.append(WALL)
    return perms

def side_walls(c):
    return SIDE_WALLS[c & 7] if (c & 0xF0) in (0xB0, 0xC0) else set()

@dataclass
class Anchor:
    id: str            # stable identifier, e.g. warp:0, obj:ROUTE32_POKE_BALL1, edge:north:0
    kind: str          # warp | edge | coord | bg | obj
    cells: list        # cells that count as reaching it
    mode: str          # 'on' (stand on a cell), 'adj' (stand on/next to it), 'face' (next to it only)
    is_entry: bool = False
    detail: str = ''
    via: list = field(default_factory=list)   # extra cells it is used from (talking across a counter)

@dataclass
class MapModel:
    name: str
    w: int                       # in cells
    h: int
    coll: list                   # base collision byte per cell, row-major
    alt: dict                    # cell -> list of (bits, collision byte) from block swaps
    objects: dict                # cell -> ('wall'|'bits', bits, obj)
    warp_cells: dict             # cell -> warp index
    anchors: list = field(default_factory=list)
    obj_bits: dict = field(default_factory=dict)   # bit -> object const
    features: set = field(default_factory=set)
    perms: list = None

    def idx(self, x, y): return y * self.w + x
    def inb(self, x, y): return 0 <= x < self.w and 0 <= y < self.h

import functools

@functools.lru_cache(maxsize=None)
def _read_src(path):
    return open(path, encoding='utf-8', errors='replace').read()

# doormats: you leave by pressing in their direction, and the game only warps you if that step
# is blocked (a wall or the map's edge)
CARPET_DIR = {0x70: (0, 1), 0x76: (-1, 0), 0x78: (0, -1), 0x7e: (1, 0)}

def dead_carpets(mm):
    """warp indices of doormats whose exit step is walkable, so the warp never fires."""
    out = []
    for c, wi in mm.warp_cells.items():
        d = CARPET_DIR.get(mm.coll[c])
        if d is None: continue
        x, y = c % mm.w + d[0], c // mm.w + d[1]
        if mm.inb(x, y) and classify(mm, mm.coll[mm.idx(x, y)]) is not None: out.append(wi)
    return out

def build_model(disasm, m, perms=None, _raw=False):
    perms = perms or load_permissions(disasm)
    coll_tab = disasm.tileset_coll[m.tileset]
    W, H = m.width * 2, m.height * 2
    coll = [0] * (W * H)
    alt = {}
    cut = disasm.cut_blocks.get(m.tileset, {})
    whirl = disasm.whirlpool_blocks.get(m.tileset, {})
    feats = set()
    for by in range(m.height):
        for bx in range(m.width):
            b = m.blocks[by * m.width + bx]
            for q in range(4):
                x, y = bx * 2 + (q & 1), by * 2 + (q >> 1)
                c = coll_tab[b][q] if b < len(coll_tab) else 0x07
                coll[y * W + x] = c
                for table, bit in ((cut, CUT), (whirl, WHIRLPOOL)):
                    if b in table:
                        r = table[b]
                        c2 = coll_tab[r][q]
                        if c2 != c:
                            alt.setdefault(y * W + x, []).append((bit, c2))
    for c in coll:
        if c == COLL_ICE: feats.add('ice')
        if c == COLL_PIT: feats.add('pit')
        if c in FORCED: feats.add('waterfall' if c == COLL_WATERFALL else 'current')
        if c == COLL_WHIRLPOOL: feats.add('whirlpool')
        if (c & 0xF0) == 0xA0: feats.add('ledge')
    mm = MapModel(m.name, W, H, coll, alt, {}, {}, features=feats, perms=perms)
    mm.itemballs = set()     # walkable once picked up, but a boulder cannot be pushed onto one
    if m.asm_path:
        src = _read_src(m.asm_path)
        if re.search(r'^\s*changeblock', src, re.M): feats.add('changeblock')
        if re.search(r'applymovement\s+PLAYER', src): feats.add('scripted_player_move')
        if re.search(r'callback\s+MAPCALLBACK_TILES', src): feats.add('tiles_callback')
    if m.coord_events: feats.add('coord_events')
    if any(c == 0x7c for c in coll): feats.add('warp_panel')

    # objects
    next_bit = FIRST_OBJ_BIT
    for o in m.objects:
        if not mm.inb(o.x, o.y): continue
        cell = mm.idx(o.x, o.y)
        if o.movement in MOVING_NPC and (o.rx or o.ry or 'WALK' in o.movement):
            continue  # moves around; not a fixed obstacle
        if o.movement == 'SPRITEMOVEDATA_SMASHABLE_ROCK' or o.sprite == 'SPRITE_ROCK':
            mm.objects[cell] = ('bits', ROCK_SMASH, o); feats.add('rock'); continue
        if o.movement == 'SPRITEMOVEDATA_STRENGTH_BOULDER' or o.sprite == 'SPRITE_BOULDER':
            mm.objects[cell] = ('bits', STRENGTH, o); feats.add('boulder'); continue
        if o.obj_type == 'OBJECTTYPE_ITEMBALL':
            mm.itemballs.add(cell)
            continue  # picked up from an adjacent cell, then walkable
        if o.event_flag not in ('-1', '') and o.obj_type != 'OBJECTTYPE_TRAINER':
            # conditionally present NPC (e.g. Sudowoodo, guards, Rockets): own requirement bit
            mm.objects[cell] = ('bits', next_bit, o); mm.obj_bits[next_bit] = o.const or o.script
            next_bit <<= 1; continue
        mm.objects[cell] = ('wall', 0, o)

    # anchors
    for k, (sx, sy) in enumerate(getattr(disasm, 'spawns', {}).get(m.name, [])):
        if mm.inb(sx, sy):   # Fly lands you here: an entry like a warp
            mm.anchors.append(Anchor(f'spawn:{k}', 'spawn', [mm.idx(sx, sy)], 'on', True, 'fly'))
    for wp in m.warps:
        if mm.inb(wp.x, wp.y):
            cell = mm.idx(wp.x, wp.y)
            mm.warp_cells[cell] = wp.index
            mm.anchors.append(Anchor(f'warp:{wp.index}', 'warp', [cell], 'on', arrivable(disasm, m, wp.index),
                                     f'{wp.dest_map} #{wp.dest_idx}'))
    for ce in m.coord_events:
        if mm.inb(ce.x, ce.y):
            mm.anchors.append(Anchor(f'coord:{ce.index}', 'coord', [mm.idx(ce.x, ce.y)], 'on', False, ce.script))
    signs = plain_signs(m)
    for bg in m.bg_events:
        if bg.index in signs: continue      # a plain sign: only text, nothing to reach it for
        if mm.inb(bg.x, bg.y):
            # signs and hidden items are used by facing them: stand next to the tile, not on it
            mm.anchors.append(Anchor(f'bg:{bg.index}', 'bg', [mm.idx(bg.x, bg.y)], 'face', False, f'{bg.kind} {bg.script}'))
    for o in m.objects:
        if mm.inb(o.x, o.y):
            moving = o.movement in MOVING_NPC and (o.rx or o.ry or 'WALK' in o.movement)
            cells = [mm.idx(o.x, o.y)]
            if moving:  # can be talked to anywhere in its wander box
                cells = [mm.idx(x, y) for x in range(o.x - o.rx, o.x + o.rx + 1)
                         for y in range(o.y - o.ry, o.y + o.ry + 1) if mm.inb(x, y)]
            # facing a counter tile, the game looks for a person two tiles away (CheckFacingObject)
            via = []
            for c in cells:
                x, y = c % W, c // W
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    if mm.inb(x + 2 * dx, y + 2 * dy) and mm.coll[mm.idx(x + dx, y + dy)] in COUNTERS:
                        via.append(mm.idx(x + 2 * dx, y + 2 * dy))
            mm.anchors.append(Anchor(f'obj:{o.const or o.index}', 'obj', cells, 'adj', False,
                                     f'{o.obj_type} {o.script}', via))
    for (d, other, oconst, off) in m.connections:
        runs = edge_runs(mm, d, None if _raw else _edge_ok(disasm, perms, m.name, d, other, off, mm))
        for k, run in enumerate(runs):
            mm.anchors.append(Anchor(f'edge:{d}:{k}', 'edge', run, 'on', True, other))
    return mm

WARP_TILES = set(range(0x70, 0x80)) | {COLL_PIT}

def _coll_at(disasm, m, x, y):
    coll = disasm.tileset_coll[m.tileset]
    if not (0 <= x < m.width * 2 and 0 <= y < m.height * 2): return None
    return coll[m.blocks[(y // 2) * m.width + x // 2]][(x & 1) + 2 * (y & 1)]

def arrivable(disasm, m, index):
    """can the player arrive at warp `index` of map m? Only through a warp elsewhere that leads
    here and stands on a warp tile (stairs, door, ladder, hole...): a warp event on plain floor or
    ice only marks where someone falling through a hole lands, and never takes anyone back up.
    So the holes Ice Path B1F's boulders fall through are one-way, as are holes in Burned Tower,
    the Lighthouse and Victory Road. A warp nothing leads to (or whose sources are unknown)
    counts as an entry, as before."""
    cache = disasm.__dict__.setdefault('_incoming', {})
    if not cache:
        for n2, m2 in disasm.maps.items():
            for w in m2.warps:
                try: cache.setdefault((w.dest_map, int(w.dest_idx) - 1), []).append((m2, w))
                except (TypeError, ValueError): pass
    src = cache.get((m.const, index))
    if not src:
        # a pit that drops you somewhere on its own map and that no warp leads to (Ecruteak Gym's
        # invisible floor): nobody arrives on a pit
        w = m.warps[index]
        if w.dest_map == m.const and _coll_at(disasm, m, w.x, w.y) == 0x60: return False
        return True
    def swapped(m2, x, y):
        # the tile as scripts can change it (changeblock: a floor that collapses into a hole)
        if not m2.asm_path: return []
        coll = disasm.tileset_coll[m2.tileset]
        out = []
        for mo in re.finditer(r'^\s*changeblock\s+(\d+),\s*(\d+),\s*\$([0-9a-fA-F]+)', _read_src(m2.asm_path), re.M):
            if int(mo.group(1)) // 2 == x // 2 and int(mo.group(2)) // 2 == y // 2:
                out.append(coll[int(mo.group(3), 16)][(x & 1) + 2 * (y & 1)])
        return out
    return any(_coll_at(disasm, m2, w.x, w.y) in WARP_TILES or any(c in WARP_TILES for c in swapped(m2, w.x, w.y))
               for m2, w in src)

def classify(mm, c):
    """terrain class of a collision byte: 'land' | 'water' | 'forced' | None (impassable)"""
    if c == COLL_WHIRLPOOL: return None
    if c in FORCED: return 'forced'
    p = mm.perms[c]
    if p == LAND: return 'land'
    if p == WATER: return 'water'
    return None

def plain_signs(m):
    """indices of bg events that are plain signs: a READ event whose script is one jumptext or
    jumpstd and nothing else (a sign's text; Route 32's signs). Scripts that do anything else (the
    Goldenrod Underground's basement door) are not signs."""
    if not m.asm_path: return set()
    lines = _read_src(m.asm_path).splitlines()
    body = {}
    for i, l in enumerate(lines):
        mo = re.match(r'^(\w+):+', l)
        if not mo: continue
        out = []
        for l2 in lines[i + 1:]:
            if re.match(r'^\w+:', l2): break
            t = l2.split(';')[0].strip()
            if t: out.append(t)
        body[mo.group(1)] = out
    return {b.index for b in m.bg_events
            if b.kind.startswith('BGEVENT_') and b.kind != 'BGEVENT_ITEM' and b.kind != 'BGEVENT_IFSET'
            and b.kind != 'BGEVENT_IFNOTSET' and len(body.get(b.script, [])) == 1
            and re.match(r'(jumptext|jumpstd)\b', body[b.script][0])}

def open_tile(mm, cell):
    """can the tile be walked or surfed on, now or once cleared (a cut tree cut, a whirlpool
    crossed)? For dead ends: a hidden item's nook must stay one tile deep whatever is cleared."""
    if classify(mm, mm.coll[cell]) is not None: return True
    return any(classify(mm, c2) is not None for _bit, c2 in mm.alt.get(cell, ()))

def _across_cell(d, x, y, off, w2, h2, w, h):
    """the tile across a connected edge from (x, y), in the connected map's coordinates."""
    if d == 'north' and y == 0: return x - 2 * off, h2 - 1
    if d == 'south' and y == h - 1: return x - 2 * off, 0
    if d == 'west' and x == 0: return w2 - 1, y - 2 * off
    if d == 'east' and x == w - 1: return 0, y - 2 * off
    return None

def _world_reach(disasm, perms):
    """{map name: tiles you can stand on}, over every map joined by edges, from where you can
    really arrive (warps, Fly spots) and across edges only into tiles reached that way. An area
    walkable on both sides of an edge but reached from nowhere else (Route 9's bottom strip and
    Route 10 North's left strip: each counts the other as its way in) is not reached. Cached."""
    key = '_world_reach'
    if key in disasm.__dict__: return disasm.__dict__[key]
    from collections import deque
    from .reach import Searcher, entry_starts
    FULL = (1 << 40) - 1
    over = [n for n, mp in disasm.maps.items() if mp.connections and mp.blocks]
    mms = {n: build_model(disasm, disasm.maps[n], perms, _raw=True) for n in over}
    S = {n: Searcher(mm) for n, mm in mms.items()}
    reached = {n: set() for n in over}
    starts = {n: set() for n in over}
    for n, mm in mms.items():
        for a in mm.anchors:
            if a.is_entry and a.kind != 'edge': starts[n] |= {c for c, _ in entry_starts(mm, a)}
    q = deque(over); inq = set(over)
    while q:
        n = q.popleft(); inq.discard(n)
        new = set(S[n].search([(c, FULL) for c in starts[n]])) - reached[n]
        if not new: continue
        reached[n] |= new
        mm = mms[n]
        for c in new:
            x, y = c % mm.w, c // mm.w
            for (dd, other, oc, off) in disasm.maps[n].connections:
                if other not in mms: continue
                mm2 = mms[other]
                t = _across_cell(dd, x, y, off, mm2.w, mm2.h, mm.w, mm.h)
                if t is None or not mm2.inb(*t): continue
                c2 = mm2.idx(*t)
                if classify(mm2, mm2.coll[c2]) is None or c2 in starts[other]: continue
                starts[other].add(c2)
                if other not in inq: q.append(other); inq.add(other)
    disasm.__dict__[key] = reached
    return reached

def _edge_ok(disasm, perms, name, d, other, off, mm):
    """edge tiles of map `name` you can really walk in from: the tile across, in the connected
    map, is reached from somewhere you can arrive (see _world_reach). None: no check possible."""
    m2 = disasm.maps.get(other) if disasm is not None else None
    if m2 is None or not m2.blocks: return None
    reach2 = _world_reach(disasm, perms).get(other)
    if reach2 is None: return None
    w2, h2 = m2.width * 2, m2.height * 2
    ok = set()
    for c in range(mm.w * mm.h):
        x, y = c % mm.w, c // mm.w
        t = _across_cell(d, x, y, off, w2, h2, mm.w, mm.h)
        if t is None or not (0 <= t[0] < w2 and 0 <= t[1] < h2): continue
        if t[1] * w2 + t[0] in reach2: ok.add(c)
    return ok


def edge_runs(mm, d, across=None):
    if d == 'north': cells = [mm.idx(x, 0) for x in range(mm.w)]
    elif d == 'south': cells = [mm.idx(x, mm.h - 1) for x in range(mm.w)]
    elif d == 'west': cells = [mm.idx(0, y) for y in range(mm.h)]
    else: cells = [mm.idx(mm.w - 1, y) for y in range(mm.h)]
    runs, cur, cur_cls = [], [], None
    for c in cells:
        cls = classify(mm, mm.coll[c])
        cls = 'water' if cls == 'forced' else cls
        if across is not None and c not in across: cls = None      # nothing to walk into beyond it
        if cls is None or c in mm.objects and mm.objects[c][0] == 'wall':
            if cur: runs.append(cur)
            cur, cur_cls = [], None
            continue
        if cur and cls != cur_cls:
            runs.append(cur); cur = []
        cur.append(c); cur_cls = cls
    if cur: runs.append(cur)
    return runs
