"""Block catalogue per tileset: collision pattern of every block, which blocks vanilla maps
actually use, and how often blocks sit next to each other (for visual coherence)."""
import math, random
from collections import Counter, defaultdict
from .model import classify, side_walls, FORCED, COLL_PIT

def cell_class(mm_like, c):
    if (c & 0xF0) == 0xA0: return 'L' + 'RLUDrlur'[c & 7]
    if 0x70 <= c <= 0x7f or c == COLL_PIT: return 'W'
    if c in FORCED: return 'v'
    if c in (0x18, 0x14): return ','     # tall / long grass
    if c == 0x15: return 'H'             # headbutt tree
    if c == 0x12: return 'T'             # cut tree
    k = classify(mm_like, c)
    return {'land': '.', 'water': '~', 'forced': 'v', None: '#'}[k]

class _P:
    def __init__(self, perms): self.perms = perms

class Catalog:
    def __init__(self, disasm, tileset, perms, sample_maps):
        self.tileset = tileset
        self.coll = disasm.tileset_coll[tileset]
        P = _P(perms)
        self.pattern = [tuple(cell_class(P, c) for c in q) for q in self.coll]
        self.walls = [tuple(frozenset(side_walls(c)) for c in q) for q in self.coll]
        self.freq = Counter(); self.H = Counter(); self.V = Counter()
        for m in sample_maps:
            if m.tileset != tileset: continue
            W, Hh, b = m.width, m.height, m.blocks
            self.freq.update(b)
            for y in range(Hh):
                for x in range(W):
                    if x + 1 < W: self.H[(b[y*W+x], b[y*W+x+1])] += 1
                    if y + 1 < Hh: self.V[(b[y*W+x], b[(y+1)*W+x])] += 1
        # usage patterns: like self.pattern but passable cells that no entry can reach are 'V' (void)
        from .model import build_model
        from .reach import Searcher, entry_starts
        self.usage = Counter()
        near_warp = Counter(); total = Counter()
        for m in sample_maps:
            if m.tileset != tileset or not m.blocks: continue
            wb = {(w.x // 2, w.y // 2) for w in m.warps}
            for by in range(m.height):
                for bx in range(m.width):
                    blk = m.blocks[by * m.width + bx]; total[blk] += 1
                    if any((bx + dx, by + dy) in wb for dx, dy in ((0,0),(1,0),(-1,0),(0,1),(0,-1))): near_warp[blk] += 1
        self.warp_only = {b for b in total if near_warp[b] >= 0.8 * total[b]}
        # sign / statue blocks: (nearly) always under a sign (a gym's statues, a notice board).
        # They are not drawn as plain wall elsewhere; blocks that stay as in vanilla keep theirs.
        at_sign = Counter()
        for m in sample_maps:
            if m.tileset != tileset or not m.blocks: continue
            sb = {(b.x // 2, b.y // 2) for b in m.bg_events if b.kind not in ('BGEVENT_ITEM',)}
            for (bx, by) in sb:
                if 0 <= bx < m.width and 0 <= by < m.height: at_sign[m.blocks[by * m.width + bx]] += 1
        self.sign_only = {b for b in total if at_sign[b] >= 0.8 * total[b]}
        # building / structure blocks: almost always within 2 blocks of a warp
        near2 = Counter()
        for m in sample_maps:
            if m.tileset != tileset or not m.blocks: continue
            wb = {(w.x // 2, w.y // 2) for w in m.warps}
            for by in range(m.height):
                for bx in range(m.width):
                    if any(abs(bx - x) <= 2 and abs(by - y) <= 2 for x, y in wb):
                        near2[m.blocks[by * m.width + bx]] += 1
        self.structure = {b for b in total if near2[b] >= 0.9 * total[b]}
        self.cut_opts = self._cut_options(disasm, tileset, P)
        for m in sample_maps:
            if m.tileset != tileset or not m.blocks: continue
            try:
                mm = build_model(disasm, m, perms)
            except Exception:
                continue
            srch = Searcher(mm)
            starts = [st for a in mm.anchors if a.is_entry for st in entry_starts(mm, a)]
            reach = set(srch.search(starts)) if starts else set()
            for by in range(m.height):
                for bx in range(m.width):
                    blk = m.blocks[by * m.width + bx]
                    pat = list(self.pattern[blk])
                    for q in range(4):
                        c = mm.idx(bx*2 + (q & 1), by*2 + (q >> 1))
                        if pat[q] == '.' and c not in reach: pat[q] = 'V'
                    self.usage[(blk, tuple(pat))] += 1
        vcount, tcount = Counter(), Counter()
        for (blk, pat), n in self.usage.items():
            tcount[blk] += n * 4
            vcount[blk] += n * sum(1 for c in pat if c == 'V')
        self.voidish = {b for b in tcount if vcount[b] > 0.5 * tcount[b]}
        # walls that look like open ground (mostly floor tiles): never use them as wall
        meta = disasm.tileset_meta.get(tileset, b'')
        def tiles(b): return list(meta[b*16:(b+1)*16]) if (b+1)*16 <= len(meta) else []
        floor_tiles = set()
        for b, pat in enumerate(self.pattern):
            if pat in (('.',) * 4, (',',) * 4) and self.freq[b] > 0: floor_tiles |= set(tiles(b))
        self.fake_floor = set()
        for b, pat in enumerate(self.pattern):
            ts = tiles(b)
            if all(c == '#' for c in pat) and ts and sum(t in floor_tiles for t in ts) >= 12:
                self.fake_floor.add(b)
        # deep wall: the block vanilla uses for wall surrounded only by wall (forest, rock)
        deep = Counter()
        for m in sample_maps:
            if m.tileset != tileset or not m.blocks: continue
            W_, H_ = m.width, m.height
            def allwall(x, y):
                return not (0 <= x < W_ and 0 <= y < H_) or all(c == '#' for c in self.pattern[m.blocks[y*W_ + x]])
            for by in range(H_):
                for bx in range(W_):
                    b = m.blocks[by*W_ + bx]
                    if b in self.fake_floor: continue
                    if all(allwall(bx+dx, by+dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)): deep[b] += 1
        self.deep = deep
        self.cands = defaultdict(list)
        self.ufreq = {}
        for (blk, pat), n in self.usage.items():
            if blk in self.warp_only and not all(c in ('#', 'V') for c in pat) and pat != ('.', '.', '.', '.'):
                continue
            if blk in self.warp_only: continue
            if blk in self.sign_only: continue
            if blk in self.voidish and '.' in pat: continue   # walkable floor must not look like void
            if blk in self.fake_floor: continue                # wall must not look like floor
            self.cands[pat].append(blk); self.ufreq[(blk, pat)] = n

    def _cut_options(self, disasm, tileset, P):
        """cut-tree blocks usable as a 1-cell gate: {(entry side, exit side): [block ids]}.
        A block qualifies for (A, B) when, before cutting, nothing connects side A to side B
        inside the block, and after cutting something does."""
        SIDES = {(0, -1): (0, 1), (0, 1): (2, 3), (-1, 0): (0, 2), (1, 0): (1, 3)}
        ADJ = {0: (1, 2), 1: (0, 3), 2: (0, 3), 3: (1, 2)}
        def open_(cls): return cls in ('.', ',')
        def joined(pat, a, b):
            start = [q for q in SIDES[a] if open_(pat[q])]
            seen = set(start); st = list(start)
            while st:
                q = st.pop()
                for n in ADJ[q]:
                    if n not in seen and open_(pat[n]): seen.add(n); st.append(n)
            return any(q in seen for q in SIDES[b] if open_(pat[q]))
        out = {}
        for blk, rep in disasm.cut_blocks.get(tileset, {}).items():
            before = tuple(cell_class(P, c) for c in self.coll[blk])
            after = tuple(cell_class(P, c) for c in self.coll[rep])
            if 'T' not in before: continue
            for a in SIDES:
                b = (-a[0], -a[1])
                if not joined(before, a, b) and joined(after, a, b):
                    out.setdefault((a, b), []).append(blk)
        return out

    def has(self, pat):
        return bool(self.cands.get(tuple(pat)))

    def choose(self, pat, left, up, rng, ok=None, fw=0.7):
        cs = [b for b in self.cands.get(tuple(pat), []) if ok is None or ok(b)]
        if not cs: return None
        best, bs = None, -1e9
        for b in cs:
            s = math.log(1 + self.ufreq.get((b, tuple(pat)), 0)) * fw
            if left is not None: s += math.log(1 + self.H[(left, b)]) * 1.5
            if up is not None: s += math.log(1 + self.V[(up, b)]) * 1.5
            s += rng.random() * 0.8
            if s > bs: best, bs = b, s
        return best

    def score(self, b, pat, nb, fw=0.7):
        """nb: dict with optional 'L','R','U','D' neighbour block ids."""
        s = math.log(1 + self.ufreq.get((b, tuple(pat)), 0)) * fw
        if nb.get('L') is not None: s += math.log(1 + self.H[(nb['L'], b)]) * 1.5
        if nb.get('R') is not None: s += math.log(1 + self.H[(b, nb['R'])]) * 1.5
        if nb.get('U') is not None: s += math.log(1 + self.V[(nb['U'], b)]) * 1.5
        if nb.get('D') is not None: s += math.log(1 + self.V[(b, nb['D'])]) * 1.5
        return s

# maps drawn in a style of their own within a shared tileset: their blocks come only from maps of
# the same tileset and the same kind of area. Tin Tower's roof shares the tower tileset with the
# floors below, but it is golden roof tiles and a walkway; Fast Ship B1F shares the Lighthouse
# tileset but should look like the ship, not stone walls and grass.
OWN_STYLE = {'TinTowerRoof', 'FastShipB1F'}
# gyms each have a look of their own within shared tilesets: their blocks come from the gym itself,
# or, when it lacks a shape the generator needs, from the gyms on the same tileset (`wide`)
GYMS = {'VioletGym', 'AzaleaGym', 'GoldenrodGym', 'OlivineGym', 'PewterGym', 'CeruleanGym', 'VermilionGym',
        'CeladonGym', 'FuchsiaGym', 'SaffronGym', 'SeafoamGym', 'ViridianGym', 'EcruteakGym', 'CianwoodGym',
        'MahoganyGym', 'BlackthornGym1F', 'BlackthornGym2F'}

def catalog_for(d, m, perms, cache):
    """the block catalog to draw map m with (cached per tileset, per style for OWN_STYLE, per gym)."""
    if m.name in GYMS:
        key = ('gym', m.name)
        if key not in cache:
            c = Catalog(d, m.tileset, perms, [m])
            # shapes the gym never uses (Pewter and Fuchsia have no solid wall block: their walls
            # are the map edge) come from every map on the tileset; the gym's own blocks still win
            # wherever it has one
            w = catalog_for(d, type('M', (), {'name': '', 'tileset': m.tileset})(), perms, cache)
            for pat, bs in w.cands.items():
                if not c.cands.get(pat):
                    c.cands[pat] = list(bs)
                    for b in bs: c.ufreq[(b, pat)] = w.ufreq.get((b, pat), 0)
            for mine, theirs in ((c.H, w.H), (c.V, w.V), (c.deep, w.deep)):
                for k, n in theirs.items():
                    if k not in mine: mine[k] = n
            cache[key] = c
        return cache[key]
    own = m.name in OWN_STYLE
    key = (m.tileset, m.environment) if own else m.tileset
    if key not in cache:
        pool = ([x for x in d.maps.values() if x.tileset == m.tileset and x.environment == m.environment]
                if own else list(d.maps.values()))
        cache[key] = Catalog(d, m.tileset, perms, pool)
    return cache[key]
