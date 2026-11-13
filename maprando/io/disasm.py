"""Parsers for the pokecrystal disassembly: map sizes, tilesets, collision, blocks, events."""
from __future__ import annotations
import os, re
from dataclasses import dataclass, field

def _strip(line):
    return line.split(';', 1)[0].strip()

def _args(rest):
    return [a.strip() for a in rest.split(',')]

def camel_to_const(name):
    """Route32 -> ROUTE_32, IcePathB1F -> ICE_PATH_B1F: a map label as its MAP constant."""
    s = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', name)
    s = re.sub(r'(?<=[A-Za-z])(?=[0-9])', '_', s)
    return s.upper()

@dataclass
class Warp:
    """warp_event: a warp tile at (x, y) leading to warp dest_idx of dest_map."""
    x: int; y: int; dest_map: str; dest_idx: int; index: int

@dataclass
class CoordEvent:
    """coord_event: a tile that runs script when stepped on while the map is in scene."""
    x: int; y: int; scene: str; script: str; index: int

@dataclass
class BgEvent:
    """bg_event: a sign, hidden item or other script read by facing (x, y)."""
    x: int; y: int; kind: str; script: str; index: int

@dataclass
class ObjectEvent:
    """object_event: a person, item ball, boulder or rock; const is its object constant."""
    x: int; y: int; sprite: str; movement: str; rx: int; ry: int
    obj_type: str; sight: int; script: str; event_flag: str; index: int
    const: str = ''

@dataclass
class MapInfo:
    """One map as the disassembly defines it: size, tileset, blocks, connections and events. Generators
    return a changed copy."""
    name: str            # CamelCase label, e.g. Route32
    const: str           # MAP constant, e.g. ROUTE_32
    width: int = 0       # in blocks
    height: int = 0
    tileset: str = ''
    environment: str = ''
    blk_path: str = ''
    border_block: int = 0
    connections: list = field(default_factory=list)  # (dir, map_name, map_const, offset)
    warps: list = field(default_factory=list)
    coord_events: list = field(default_factory=list)
    bg_events: list = field(default_factory=list)
    objects: list = field(default_factory=list)
    blocks: bytes = b''
    asm_path: str = ''
    script: str = ''     # the map's .asm source (scripts and events)

def _int(s):
    s = s.strip()
    if s.startswith('$'): return int(s[1:], 16)
    if s.startswith('%'): return int(s[1:], 2)
    return int(s, 0) if s.startswith('0x') else int(s)

class Disasm:
    """The parsed pokecrystal disassembly at root: every map (self.maps), tileset collision and block
    data, and the few data tables the randomizer needs. All files are read here, once; nothing
    else in maprando reads them."""
    def __init__(self, root):
        self.root = root
        self.maps = {}          # name -> MapInfo
        self.const_to_name = {}
        self._parse_map_constants()
        self._parse_maps_asm()
        self._parse_attributes()
        self._parse_blocks()
        self._parse_tilesets()
        self._parse_field_move_blocks()
        for m in self.maps.values():
            self._parse_events(m)
        # collision permission of each tile type: the db line's value (LAND_TILE, WATER_TILE, ...)
        self.collision_permissions = [_strip(l)[2:].strip() for l in self.read('data', 'collision_permissions.asm')
                                      if _strip(l).startswith('db')]
        # maps with wild encounters: Surf, grass, headbutt trees -> {MAP constant}
        self.wild_maps = {kind: self._wild_maps(files, macro) for kind, files, macro in (
            ('water', ('johto_water.asm', 'kanto_water.asm'), 'map_id'),
            ('grass', ('johto_grass.asm', 'kanto_grass.asm'), 'map_id'),
            ('headbutt', ('treemon_maps.asm',), 'treemon_map'))}
        # Fly / Escape spawn points: name -> [(x, y)]
        self.spawns = {}
        import os, re
        sp = os.path.join(root, 'data', 'maps', 'spawn_points.asm')
        if os.path.exists(sp):
            for line in open(sp, encoding='utf-8', errors='replace'):
                mo = re.match(r'\s*spawn\s+(\w+),\s*(\d+),\s*(\d+)', line)
                if mo and mo.group(1) in self.const_to_name:
                    self.spawns.setdefault(self.const_to_name[mo.group(1)], []).append((int(mo.group(2)), int(mo.group(3))))

    def path(self, *p):
        return os.path.join(self.root, *p)

    def read(self, *p):
        with open(self.path(*p), encoding='utf-8', errors='replace') as f:
            return f.read().splitlines()

    def _wild_maps(self, files, macro):
        out = set()
        for f in files:
            for line in self.read('data', 'wild', f):
                mo = re.match(r'\s*' + macro + r'\s+(\w+)', line)
                if mo: out.add(mo.group(1))
        return out

    # --- maps ---
    def _parse_map_constants(self):
        self.map_sizes = {}
        for line in self.read('constants', 'map_constants.asm'):
            l = _strip(line)
            if l.startswith('map_const ') or l.startswith('map_const\t'):
                a = _args(l[len('map_const'):])
                self.map_sizes[a[0]] = (int(a[1]), int(a[2]))

    def _parse_maps_asm(self):
        for line in self.read('data', 'maps', 'maps.asm'):
            l = _strip(line)
            if l.startswith('map '):
                a = _args(l[4:])
                name = a[0]
                m = MapInfo(name=name, const=camel_to_const(name))
                m.tileset = a[1]; m.environment = a[2]
                self.maps[name] = m

    def _parse_attributes(self):
        cur = None
        for line in self.read('data', 'maps', 'attributes.asm'):
            l = _strip(line)
            if l.startswith('map_attributes'):
                a = _args(l[len('map_attributes'):])
                cur = self.maps.get(a[0])
                if cur is None: continue
                cur.const = a[1]
                cur.border_block = _int(a[2])
                if a[1] in self.map_sizes:
                    cur.width, cur.height = self.map_sizes[a[1]]
                self.const_to_name[a[1]] = a[0]
            elif l.startswith('connection') and cur is not None:
                a = _args(l[len('connection'):])
                cur.connections.append((a[0], a[1], a[2], _int(a[3])))
        for m in self.maps.values():
            if not m.width and m.const in self.map_sizes:
                m.width, m.height = self.map_sizes[m.const]
                self.const_to_name.setdefault(m.const, m.name)

    def _parse_blocks(self):
        pending = []
        for line in self.read('data', 'maps', 'blocks.asm'):
            l = _strip(line)
            mm = re.match(r'^(\w+)_Blocks:', l)
            if mm:
                pending.append(mm.group(1)); continue
            mm = re.match(r'INCBIN\s+"([^"]+)"', l)
            if mm:
                for n in pending:
                    if n in self.maps:
                        self.maps[n].blk_path = mm.group(1)
                pending = []
        for m in self.maps.values():
            if m.blk_path and os.path.exists(self.path(m.blk_path)):
                with open(self.path(m.blk_path), 'rb') as f:
                    m.blocks = f.read()

    # --- tilesets ---
    def _parse_tilesets(self):
        # tileset constant order
        consts = []
        for line in self.read('constants', 'tileset_constants.asm'):
            l = _strip(line)
            mm = re.match(r'const\s+(TILESET_\w+)', l)
            if mm: consts.append(mm.group(1))
        # Tilesets:: table order (index 0 is Tileset0)
        names = []
        started = False
        for line in self.read('data', 'tilesets.asm'):
            l = _strip(line)
            if l.startswith('Tilesets::'): started = True; continue
            if started:
                mm = re.match(r'tileset\s+(\w+)', l)
                if mm: names.append(mm.group(1))
        const_by_label = {}
        for i, n in enumerate(names):
            if i >= 1 and i - 1 < len(consts):
                const_by_label[n] = consts[i - 1]
        # gfx/tilesets.asm: labels -> files
        meta_file, coll_file, gfx_file = {}, {}, {}
        labels = []
        for line in self.read('gfx', 'tilesets.asm'):
            l = _strip(line)
            mm = re.match(r'^(\w+)::?$', l)
            if mm: labels.append(mm.group(1)); continue
            mm = re.match(r'(INCBIN|INCLUDE)\s+"([^"]+)"', l)
            if mm:
                fn = mm.group(2)
                for lab in labels:
                    if lab.endswith('Meta'): meta_file[lab[:-4]] = fn
                    if lab.endswith('Coll'): coll_file[lab[:-4]] = fn
                    if lab.endswith('GFX'): gfx_file[lab[:-3]] = fn
                labels = []
        self.tileset_gfx = {}
        for label, const in const_by_label.items():
            if label in gfx_file:
                fn = gfx_file[label].replace('.lz', '')
                self.tileset_gfx[const] = fn
        self.tileset_palmap = {const: f'gfx/tilesets/{os.path.basename(f).split(".")[0]}_palette_map.asm'
                               for const, f in self.tileset_gfx.items()}
        self.tileset_coll = {}   # TILESET_X -> list of (tl,tr,bl,br) collision bytes per block
        self.tileset_meta = {}   # TILESET_X -> bytes (16 per block)
        coll_consts = self._collision_constants()
        for label, const in const_by_label.items():
            if label in coll_file:
                self.tileset_coll[const] = self._parse_collision(coll_file[label], coll_consts)
            if label in meta_file and os.path.exists(self.path(meta_file[label])):
                with open(self.path(meta_file[label]), 'rb') as f:
                    self.tileset_meta[const] = f.read()

    def _collision_constants(self):
        d = {}
        for line in self.read('constants', 'collision_constants.asm'):
            l = _strip(line)
            mm = re.match(r'(COLL_\w+|\w+_TILE|TALK)\s+EQU\s+(\$[0-9a-fA-F]+)', l)
            if mm: d[mm.group(1)] = _int(mm.group(2))
        self.coll_consts = d
        return d

    def _parse_collision(self, fn, cc):
        out = []
        for line in self.read(fn):
            l = _strip(line)
            if l.startswith('tilecoll'):
                vals = []
                for a in _args(l[len('tilecoll'):]):
                    if ('COLL_' + a) in cc: vals.append(cc['COLL_' + a])
                    else: vals.append(int(a, 16))
                out.append(tuple(vals))
        return out

    def _parse_field_move_blocks(self):
        """CutTreeBlockPointers / WhirlpoolBlockPointers -> {tileset: {block: replacement}}"""
        self.cut_blocks, self.whirlpool_blocks = {}, {}
        target = None; tables = {}; ptrs = {}
        cur_label = None
        for line in self.read('data', 'events', 'field_move_blocks.asm'):
            l = _strip(line)
            if l.startswith('CutTreeBlockPointers'): target = 'cut'; continue
            if l.startswith('WhirlpoolBlockPointers'): target = 'whirl'; continue
            mm = re.match(r'dbw\s+(TILESET_\w+),\s*(\.\w+)', l)
            if mm: ptrs[(target, mm.group(2))] = mm.group(1); continue
            mm = re.match(r'^(\.\w+):', l)
            if mm: cur_label = (target, mm.group(1)); tables[cur_label] = {}; continue
            mm = re.match(r'db\s+(\$?\w+),\s*(\$?\w+),\s*(\$?\w+)', l)
            if mm and cur_label:
                tables[cur_label][_int(mm.group(1))] = _int(mm.group(2))
        for (t, lab), ts in ptrs.items():
            d = self.cut_blocks if t == 'cut' else self.whirlpool_blocks
            d[ts] = tables.get((t, lab), {})

    # --- events ---
    def _parse_events(self, m):
        p = self.path('maps', m.name + '.asm')
        if not os.path.exists(p): return
        m.asm_path = p
        with open(p, encoding='utf-8', errors='replace') as f: m.script = f.read()
        lines = m.script.splitlines()
        consts = []
        in_events = False
        counters = {'warp': 0, 'coord': 0, 'bg': 0, 'obj': 0}
        for line in lines:
            l = _strip(line)
            mm = re.match(r'const\s+(\w+)', l)
            if mm and not in_events: consts.append(mm.group(1))
            if l.startswith(m.name + '_MapEvents'): in_events = True; continue
            if not in_events: continue
            if l.startswith('warp_event'):
                a = _args(l[len('warp_event'):])
                m.warps.append(Warp(_int(a[0]), _int(a[1]), a[2], _int(a[3]), counters['warp'])); counters['warp'] += 1
            elif l.startswith('coord_event'):
                a = _args(l[len('coord_event'):])
                m.coord_events.append(CoordEvent(_int(a[0]), _int(a[1]), a[2], a[3], counters['coord'])); counters['coord'] += 1
            elif l.startswith('bg_event'):
                a = _args(l[len('bg_event'):])
                m.bg_events.append(BgEvent(_int(a[0]), _int(a[1]), a[2], a[3], counters['bg'])); counters['bg'] += 1
            elif l.startswith('object_event'):
                a = _args(l[len('object_event'):])
                i = counters['obj']
                o = ObjectEvent(_int(a[0]), _int(a[1]), a[2], a[3], _int(a[4]), _int(a[5]),
                                a[9], _int(a[10]) if a[10].lstrip('-').isdigit() else 0, a[11], a[12], i)
                # const list: first entry is usually object_const_def-related; match by order
                obj_consts = [c for c in consts]
                o.const = obj_consts[i] if i < len(obj_consts) else ''
                m.objects.append(o); counters['obj'] += 1


def load_disasm(root: str, cache_dir: str | None = None) -> Disasm:
    """Disasm(root), cached on disk: parsing takes seconds and every worker process needs it.
    The cache key covers every source file's path, size and modification time, so any edit to
    the romhack invalidates it."""
    import hashlib, os, pickle
    root = os.path.abspath(root)
    h = hashlib.sha1(root.encode())        # per location: the cache holds absolute paths
    h.update(b'format 2')                  # bump when Disasm gains or changes fields
    # only what Disasm reads: map scripts and blocks, map/tileset/event data, constants
    for sub in ('maps', 'constants', os.path.join('data', 'maps'), os.path.join('data', 'tilesets'),
                os.path.join('data', 'events'), os.path.join('data', 'wild')):
        d0 = os.path.join(root, sub)
        if not os.path.isdir(d0): continue
        for e in sorted(os.scandir(d0), key=lambda e: e.name):
            if e.is_file() and e.name.endswith(('.asm', '.blk', '.bin')):
                st = e.stat()
                h.update(f'{sub}/{e.name}|{st.st_size}|{st.st_mtime_ns}'.encode())
    for f in (os.path.join('data', 'tilesets.asm'), os.path.join('gfx', 'tilesets.asm'),
              os.path.join('data', 'collision_permissions.asm')):
        if os.path.exists(os.path.join(root, f)):
            st = os.stat(os.path.join(root, f)); h.update(f'{f}|{st.st_size}|{st.st_mtime_ns}'.encode())
    cache_dir = cache_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'out', '.cache')
    path = os.path.join(cache_dir, f'disasm-{h.hexdigest()[:16]}.pkl')
    try:
        with open(path, 'rb') as f: d = pickle.load(f)
        _reroot(d, root); d.cache_path = path; return d
    except Exception:
        pass
    d = Disasm(root)
    d.cache_path = path
    try:
        os.makedirs(cache_dir, exist_ok=True)
        olds = sorted((os.path.getmtime(os.path.join(cache_dir, f)), f) for f in os.listdir(cache_dir)
                      if f.startswith('disasm-') and f.endswith('.pkl'))
        for _, f in olds[:-3]: os.remove(os.path.join(cache_dir, f))     # keep the newest few
        tmp = path + f'.{os.getpid()}.tmp'
        with open(tmp, 'wb') as f: pickle.dump(d, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except Exception:
        pass
    return d

def _reroot(d, root):
    """point a loaded Disasm at root (the folder may have moved, or be seen from another OS)."""
    import os
    d.root = root
    for m in d.maps.values():
        if getattr(m, 'asm_path', ''):
            m.asm_path = os.path.join(root, 'maps', m.name + '.asm')
