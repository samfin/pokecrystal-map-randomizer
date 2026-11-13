"""Zone graph: condense the cell movement graph into zones joined by labelled gates.

A zone is a strongly connected set of cells reachable from each other using only steps
whose requirement equals the terrain's own medium requirement (0 on land, Surf on water).
Edges between zones carry the requirement of the step (ledge jumps are one-way, Surf for
land->water, Waterfall climbs, object bits for rocks/boulders/NPC blockers)."""
from collections import defaultdict
from maprando.model.reach import Searcher
from maprando.model.movement import SURF, classify

ALL = (1 << 62) - 1

def medium(mm, c):
    return SURF if classify(mm, mm.coll[c]) in ('water', 'forced') else 0

def cell_edges(mm, s=None):
    s = s or Searcher(mm)
    edges = defaultdict(set)
    for c in range(mm.w * mm.h):
        if classify(mm, mm.coll[c]) is None and c not in mm.alt: continue
        if mm.objects.get(c, ('',))[0] == 'wall': continue
        for d, req in s.neighbors(c, ALL):
            edges[c].add((d, req))
    return edges

def tarjan(nodes, succ):
    index, low, on, st, comp = {}, {}, set(), [], {}
    n = [0]; k = [0]
    import sys
    sys.setrecursionlimit(100000)
    def visit(v):
        index[v] = low[v] = n[0]; n[0] += 1; st.append(v); on.add(v)
        for w in succ(v):
            if w not in index: visit(w); low[v] = min(low[v], low[w])
            elif w in on: low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            while True:
                w = st.pop(); on.discard(w); comp[w] = k[0]
                if w == v: break
            k[0] += 1
    for v in nodes:
        if v not in index: visit(v)
    return comp

class ZoneGraph:
    def __init__(self, mm):
        self.mm = mm
        s = Searcher(mm)
        E = cell_edges(mm, s)
        cells = set(E) | {d for es in E.values() for d, _ in es} | set(s.terminal)
        def free(c):
            if c in s.terminal: return []
            mc = medium(mm, c)
            return [d for d, r in E.get(c, ()) if r == mc and medium(mm, d) == mc]
        self.zone_of = tarjan(sorted(cells), free)
        self.zones = defaultdict(list)
        for c, z in self.zone_of.items(): self.zones[z].append(c)
        self.edges = set()   # (za, zb, req)
        for c, es in E.items():
            for d, r in es:
                za, zb = self.zone_of[c], self.zone_of[d]
                if za != zb: self.edges.add((za, zb, r))
        self.anchors = defaultdict(list)
        for a in mm.anchors:
            zs = set()
            for c in a.cells:
                if c in self.zone_of and a.mode != 'face': zs.add(self.zone_of[c])
                if a.mode in ('adj', 'face'):
                    x, y = c % mm.w, c // mm.w
                    for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                        if mm.inb(x+dx, y+dy) and (c+dx+dy*mm.w) in self.zone_of: zs.add(self.zone_of[c+dx+dy*mm.w])
            for c in getattr(a, 'via', ()):
                if c in self.zone_of: zs.add(self.zone_of[c])
            for z in zs: self.anchors[z].append(a.id)

