"""Layout metrics: how far you walk, and how much of the land is wide open."""
from collections import deque
from maprando.model.movement import classify
from maprando.model.reach import Searcher, entry_starts

ALL = (1 << 40) - 1

def steps(mm):
    """mean shortest walk (all abilities) from each entry to each other anchor; also the mean of
    each entry's farthest anchor."""
    s = Searcher(mm)
    tot = n = 0; far = []
    for e in mm.anchors:
        if not e.is_entry: continue
        dist = {}; q = deque()
        for c, _ in entry_starts(mm, e):
            if c not in dist: dist[c] = 0; q.append(c)
        while q:
            c = q.popleft()
            if c in s.terminal and dist[c] > 0: continue
            for d, _ in s.neighbors(c, ALL):
                if d not in dist: dist[d] = dist[c] + 1; q.append(d)
        best = 0
        for a in mm.anchors:
            if a.id == e.id: continue
            cells = set(a.cells)
            if a.mode in ('adj', 'face'):
                for c in list(cells):
                    x, y = c % mm.w, c // mm.w
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        if mm.inb(x + dx, y + dy): cells.add(c + dx + dy * mm.w)
                if a.mode == 'face': cells -= set(a.cells)
            cells |= set(getattr(a, 'via', ()))
            ds = [dist[c] for c in cells if c in dist]
            if ds: tot += min(ds); n += 1; best = max(best, min(ds))
        far.append(best)
    return (tot / n if n else 0), (sum(far) / len(far) if far else 0)

def reachable(mm):
    s = Searcher(mm); seen = set(); q = deque()
    for e in mm.anchors:
        if e.is_entry:
            for c, _ in entry_starts(mm, e):
                if c not in seen: seen.add(c); q.append(c)
    while q:
        c = q.popleft()
        for d, _ in s.neighbors(c, ALL):
            if d not in seen: seen.add(d); q.append(d)
    return seen

def openness(mm):
    """share of reachable land cells whose 8 neighbours are all reachable land."""
    R = reachable(mm)
    land = {c for c in R if classify(mm, mm.coll[c]) == 'land' and mm.objects.get(c, ('',))[0] != 'wall'}
    if not land: return 0.0
    k = 0
    for c in land:
        x, y = c % mm.w, c // mm.w
        if all(mm.inb(x + dx, y + dy) and (c + dx + dy * mm.w) in land
               for dx in (-1, 0, 1) for dy in (-1, 0, 1)): k += 1
    return k / len(land)
