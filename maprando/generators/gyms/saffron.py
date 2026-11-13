"""Saffron Gym: the teleport pads re-paired.

The gym is a grid of closed rooms; its 30 pads come in 15 two-way pairs, each joining two rooms.
Walls stay as they are: the puzzle is which pad leads where. A design is a new pairing of the pads
(never two pads of the same room), scored:

  + pad hops on the quickest way from the entrance room to Sabrina's room
  + rooms the quickest way does not pass through that still hold a trainer (worth a detour)
  - a room you can reach only through a single pad (no way round: a dead end) beyond the
    vanilla count

Every room must stay reachable from the entrance (pads work both ways, so then every room reaches
every other). The movement model does not follow warps within a map, so its requirement rows are
per room and cannot see a bad pairing: the generator guarantees connectivity itself and the
readback checks it again on the final ROM (`pad_rooms_ok`)."""
import copy, random
from collections import deque
from maprando.model.movement import build_model, classify
from maprando.generators.caves import GenError

D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))

def pad_rooms(d, m, perms, mm=None):
    """(room of each cell, pads {warp index: room}) for map m: rooms are floor areas, pads are warps
    leading back into the same map."""
    mm = mm or build_model(d, m, perms)
    W, H = mm.w, mm.h
    walls = {c for c, (k, _, _) in mm.objects.items() if k == 'wall'} - {mm.idx(o.x, o.y) for o in m.objects if o.obj_type == 'OBJECTTYPE_TRAINER'}
    def ok(c): return classify(mm, mm.coll[c]) is not None or c in mm.warp_cells
    room, k = {}, 0
    for c0 in range(W * H):
        if c0 in room or not ok(c0): continue
        k += 1; room[c0] = k; q = [c0]
        while q:
            c = q.pop(); x, y = c % W, c // W
            for dx, dy in D4:
                if 0 <= x + dx < W and 0 <= y + dy < H:
                    n = c + dx + dy * W
                    if n not in room and ok(n): room[n] = k; q.append(n)
    pads = {i: room.get(mm.idx(w.x, w.y)) for i, w in enumerate(m.warps) if w.dest_map == m.const}
    return room, pads

def pad_rooms_ok(d, m, perms):
    """every room reachable from the entrance through the pads (pairs as in m)."""
    mm = build_model(d, m, perms)
    room, pads = pad_rooms(d, m, perms, mm)
    ent = {room.get(mm.idx(w.x, w.y)) for w in m.warps if w.dest_map != m.const}
    adj = {}
    for i, r in pads.items():
        j = int(m.warps[i].dest_idx) - 1
        adj.setdefault(r, set()).add(pads.get(j))
    seen = set(ent); q = list(ent)
    while q:
        r = q.pop()
        for t in adj.get(r, ()):
            if t not in seen: seen.add(t); q.append(t)
    rooms = set(pads.values()) | ent
    return sorted(r for r in rooms if r not in seen)

class PadGymGenerator:
    W_HOPS, W_DETOUR, W_DEAD = 3.0, 1.0, 2.0

    def __init__(self, d, m, perms, cache=None):
        self.d, self.m, self.perms = d, m, perms
        self.mm = mm = build_model(d, m, perms)
        self.room, self.pads = pad_rooms(d, m, perms, mm)
        if not self.pads or None in self.pads.values(): raise GenError(f'{m.name}: pads outside rooms')
        # pads must come in two-way pairs
        for i in self.pads:
            j = int(m.warps[i].dest_idx) - 1
            if int(m.warps[j].dest_idx) - 1 != i: raise GenError(f'{m.name}: one-way pads')
        self.ent = {self.room.get(mm.idx(w.x, w.y)) for w in m.warps if w.dest_map != m.const}
        L = next(o for o in m.objects if o.sprite == 'SPRITE_SABRINA')
        self.goal = {self.room.get(mm.idx(L.x + dx, L.y + dy)) for dx, dy in D4} - {None}
        self.trainer_rooms = {self.room.get(mm.idx(o.x + dx, o.y + dy)) for o in m.objects
                              if o.obj_type == 'OBJECTTYPE_TRAINER' for dx, dy in D4} - {None}
        self.van = {i: int(m.warps[i].dest_idx) - 1 for i in self.pads}
        r = self.score(self.van)
        self.van_dead = r[1]['dead'] if r else 0

    def score(self, pair):
        adj = {}
        for i, j in pair.items(): adj.setdefault(self.pads[i], []).append(self.pads[j])
        dist = {r: 0 for r in self.ent}; prev = {}
        q = deque(self.ent)
        while q:
            r = q.popleft()
            for t in adj.get(r, ()):
                if t not in dist: dist[t] = dist[r] + 1; prev[t] = r; q.append(t)
        rooms = set(self.pads.values())
        if any(r not in dist for r in rooms): return None
        g = min(self.goal, key=lambda r: dist[r])
        hops = dist[g]
        way = {g}
        while g in prev: g = prev[g]; way.add(g)
        detour = len((self.trainer_rooms - way) & rooms)
        dead = sum(1 for r in rooms if len(adj.get(r, ())) == 1)
        s = self.W_HOPS * hops + self.W_DETOUR * detour - self.W_DEAD * max(0, dead - getattr(self, 'van_dead', dead))
        return s, dict(hops=hops, detour=detour, dead=dead)

    def generate(self, seed, samples=20000, keep=12):
        rng = random.Random(f'{seed}-{self.m.name}-pads')
        idx = sorted(self.pads)
        found = []
        for _ in range(samples):
            order = idx[:]; rng.shuffle(order)
            pair, left = {}, order
            ok = True
            while left:
                a = left[0]
                cs = [b for b in left[1:] if self.pads[b] != self.pads[a]]
                if not cs: ok = False; break
                b = rng.choice(cs)
                pair[a], pair[b] = b, a
                left = [x for x in left if x not in (a, b)]
            if not ok or pair == self.van: continue
            r = self.score(pair)
            if r: found.append((r[0], rng.random(), pair, r[1]))
        if not found: raise GenError(f'{self.m.name}: no pad pairing keeps every room reachable')
        found.sort(key=lambda t: (-t[0], t[1]))
        s, _, pair, info = rng.choice(found[:keep])
        m2 = copy.deepcopy(self.m)
        for i, j in pair.items(): m2.warps[i].dest_idx = j + 1
        if pad_rooms_ok(self.d, m2, self.perms): raise GenError(f'{self.m.name}: pairing check failed')
        self.m2, self.info = m2, dict(info, score=s, van=self.score(self.van)[1])
        return m2
