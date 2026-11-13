"""Gyms designed from scratch.

A gym has one job: a walk from the door to the leader, past the gym's trainers. Every supported gym
has no requirements (its requirement matrix is all zero), so the layout is free apart from the
entrance (statues, guide, door), scripted spots and the gym's back wall.

The design is made on the tile grid first and drawn afterwards (abstract first, tiles last):

  1. walls are laid as straight runs of tiles; gyms that are mirror-symmetric in vanilla stay
     symmetric (every run has its mirror twin). A run is only kept if every block it touches can
     still be drawn with one of the gym's own blocks (Pewter's rock ridges hang from the top of a
     block, Violet's pits fill whole blocks, ...).
  2. simulated annealing scores the grid like a level:
       + walk from door to leader, up to about 1.6x vanilla
       + a second way to the leader (a choice), + trainers that watch the ways
       - floor nobody reaches, dead ends nobody stands in, single wall tiles, wall-run ends
         (ragged edges), big empty floor, wall share far from vanilla's
  3. the leader keeps their vanilla spot (top centre: every gym's dais); trainers stand where their
     view crosses a way to the leader, mirrored in pairs when the gym is symmetric, and never where
     standing or stopping after walking up to the player (either facing) would cut the gym in two.
  4. blocks are chosen per 2x2 pattern from the gym's own blocks, then re-chosen for the most
     natural seams (how often two blocks sit side by side in the gym).
The best designs go through CaveGenerator.verify (requirement matrix and every other rule)."""
import copy, math, random
from collections import deque
from maprando.model.catalog import Catalog, catalog_for
from maprando.checks.trainers import FACING
from maprando.generators.caves import CaveGenerator, GenError
from maprando.model.movement import STRENGTH

GYM_LEADERS = {'SPRITE_FALKNER', 'SPRITE_BUGSY', 'SPRITE_WHITNEY', 'SPRITE_JASMINE', 'SPRITE_BROCK',
               'SPRITE_MISTY', 'SPRITE_SURGE', 'SPRITE_ERIKA', 'SPRITE_JANINE', 'SPRITE_SABRINA',
               'SPRITE_BLAINE', 'SPRITE_BLUE', 'SPRITE_CHUCK', 'SPRITE_MORTY', 'SPRITE_PRYCE', 'SPRITE_CLAIR'}
# gyms designed from scratch (Cianwood with its Strength gate). Kept vanilla: Cerulean (the Rocket
# scene fills the room), Seafoam (tiny), Vermilion (the trash-can switch puzzle
# locks most of it), Olivine (Jasmine's dais and carpet are its only full-floor blocks: a new layout
# would be carpet everywhere).
SCRATCH_GYMS = ['VioletGym', 'AzaleaGym', 'GoldenrodGym', 'PewterGym', 'CeladonGym', 'FuchsiaGym', 'CianwoodGym',
                'ViridianGym']
# gyms with a puzzle of their own: Ecruteak's invisible floor (maprando/gympit.py), Mahogany's ice
# (maprando/gymice.py), Blackthorn 2F's boulders and holes (maprando/boulderroom.py), Saffron's
# teleport pads (maprando/gympads.py: re-paired, walls as in vanilla). Blackthorn 1F
# stays vanilla: the boulders falling from 2F fill its lava at fixed spots (changeblock), which
# locks nearly all of it.
# gyms that are mazes (Fuchsia: invisible walls): corridors and dead ends, a long walk to the leader
MAZE_GYMS = {'FuchsiaGym'}
PUZZLE_GYMS = {'EcruteakGym': 'pit', 'MahoganyGym': 'ice', 'BlackthornGym2F': 'boulders', 'SaffronGym': 'pads'}
ALL_GYMS = SCRATCH_GYMS + list(PUZZLE_GYMS)
D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
MIRROR = {(1, 0): (-1, 0), (-1, 0): (1, 0), (0, 1): (0, 1), (0, -1): (0, -1)}

def walkable(c):
    """a plan tile the player can walk on: floor or a warp."""
    return c in ('.', 'W')

class GymGenerator:
    # weights: walk, second way, trainers watching, unreached, dead ends, lone walls, run ends,
    # open floor, wall share
    """Trainer gyms (and Cianwood's boulder gym): the free tiles are redrawn by simulated annealing
    around the walk to the leader, then the trainers are placed along it (see the module
    docstring)."""
    W_WALK, W_ALT, W_WATCH, W_UNREACH, W_DEAD, W_LONE, W_ENDS, W_OPEN, W_SHARE = \
        1.0, 8.0, 5.0, 1.5, 2.0, 3.0, 0.4, 0.6, 60.0

    def __init__(self, d, m, perms, cache=None):
        self.d, self.m, self.perms = d, m, perms
        cache = {} if cache is None else cache
        self.cat = catalog_for(d, m, perms, cache)
        self.cave = CaveGenerator(d, m, perms, self.cat)      # locks and the verifier
        # a Strength gate is a puzzle: optimize_gate checks its solution and traps push by push
        # a Strength gate (Cianwood) is checked push by push in generate (gym_puzzle), not by the
        # cave's usual boulder check
        self.cave.puzzle_checked = any('BOULDER' in o.sprite for o in m.objects)
        # zones are the cave generator's plan; a gym is one walkable area, and the matrix plus
        # people_ok already ask that everything stays reachable
        self.cave.zones_reachable = lambda mm2: None
        masks = {}
        for row in self.cave.matrix.values():
            for a, v in row.items():
                masks.setdefault(a, set()).update(v if isinstance(v, (list, tuple, set)) else [v])
        if any(x not in (0, STRENGTH) for v in masks.values() for x in v):
            raise GenError(f'{m.name}: gym with requirements other than Strength')
        # anchors behind the Strength gate (Cianwood: Chuck and the black belt beside him)
        self.inner_ids = {a for a, v in masks.items() if STRENGTH in v and 0 not in v}
        W, H = self.W, self.H = m.width, m.height
        self.W2, self.H2 = W * 2, H * 2
        self.lock = set(self.cave.lock)
        own = self.own = Catalog(d, m.tileset, perms, [m])
        # the shapes this gym can draw: 2x2 patterns of plain floor and wall among its own blocks
        self.pats = {p: bs for p, bs in own.cands.items() if bs and all(c in '.#' for c in p)}
        # the back wall's own blocks (Fuchsia's window strip) are not drawn inside the hall
        # unless nothing else has their shape
        back = {m.blocks[x] for x in range(W)}
        inside = {m.blocks[y * W + x] for y in range(1, H) for x in range(W)}
        for p, bs in list(self.pats.items()):
            keep = [b for b in bs if b in inside or b not in back]
            if keep: self.pats[p] = keep
        if ('.',) * 4 not in self.pats: raise GenError(f'{m.name}: no floor block')
        lc = {(x, y) for (bx, by) in self.lock for x in (bx*2, bx*2+1) for y in (by*2, by*2+1)}
        # frame: the back wall row and solid edge blocks stay
        def frame(bx, by):
            if by == 0: return True
            return (bx in (0, W - 1) or by == H - 1) and '.' not in self.cat.pattern[m.blocks[by * W + bx]]
        self.free_blocks = [(bx, by) for by in range(H) for bx in range(W)
                            if (bx, by) not in self.lock and not frame(bx, by)]
        fb = set(self.free_blocks)
        # vanilla tile grid; fixed tiles are those outside the free blocks
        self.van = self.cells(m.blocks)
        self.fixed = {(x, y) for y in range(self.H2) for x in range(self.W2) if (x // 2, y // 2) not in fb}
        self.kept = [o for o in m.objects if (o.x, o.y) in lc]
        self.leader = next((o for o in m.objects if o.sprite in GYM_LEADERS), None)
        if self.leader is None: raise GenError(f'{m.name}: no leader')
        if self.leader not in self.kept: self.kept.append(self.leader)
        L = (self.leader.x, self.leader.y)
        f = FACING.get(self.leader.movement)
        self.leader_front = [(L[0] + f[0], L[1] + f[1])] if f else [(L[0] + dx, L[1] + dy) for dx, dy in D4]
        # floor that must stay floor: under people kept in place and in front of the leader
        self.must_floor = {(o.x, o.y) for o in self.kept} | set(self.leader_front)
        free_objs = [o for o in m.objects if o not in self.kept]
        def aid(o): return f'obj:{o.const or o.index}'
        boulders = [o for o in free_objs if 'BOULDER' in o.sprite]
        self.gate = [o for o in boulders if aid(o) not in self.inner_ids]
        if any(aid(o) in self.inner_ids for o in boulders): raise GenError(f'{m.name}: boulder behind the gate moves')
        if self.inner_ids and not self.gate: raise GenError(f'{m.name}: Strength without a boulder gate')
        if not self.inner_ids and self.gate: raise GenError(f'{m.name}: boulders that gate nothing')
        free_objs = [o for o in free_objs if o not in boulders]
        self.inner_objs = {o.index for o in free_objs if aid(o) in self.inner_ids}
        tr = [o for o in free_objs if o.obj_type == 'OBJECTTYPE_TRAINER']
        units, done = [], set()
        for o in tr:
            if o.index in done: continue
            mate = next((p for p in tr if p is not o and p.index not in done and p.sprite == o.sprite == 'SPRITE_TWIN'
                         and p.y == o.y and abs(p.x - o.x) == 1), None)
            if mate:
                a, b = sorted((o, mate), key=lambda q: q.x); units.append([a, b]); done |= {a.index, b.index}
            else:
                units.append([o]); done.add(o.index)
        self.units = units
        self.others = [o for o in free_objs if o.obj_type != 'OBJECTTYPE_TRAINER']
        self.starts = [(w.x, w.y) for w in m.warps]
        self.near_door = {(x + dx, y + dy) for (x, y) in self.starts for dx in (-1, 0, 1) for dy in (-2, -1, 0, 1)}
        # symmetric gyms stay symmetric (Violet, Celadon, Azalea, Pewter, Fuchsia; not Goldenrod)
        same = sum(m.blocks[y * W + x] == m.blocks[y * W + W - 1 - x] for y in range(H) for x in range(W))
        self.sym = same >= 0.6 * W * H and not self.gate     # a one-gap gate cannot be mirrored
        self.free_tiles = [(x, y) for y in range(self.H2) for x in range(self.W2) if (x, y) not in self.fixed]
        vfree = [self.van[y][x] for (x, y) in self.free_tiles]
        self.van_share = sum(1 for c in vfree if c == '#') / max(1, len(vfree))
        r = self.score(self.van, place=False)
        self.van_walk = r[2]['walk'] if r else 20
        self.walk_cap = max(self.van_walk * 1.6, self.van_walk + 10)
        self.maze = m.name in MAZE_GYMS
        # the search scores layouts alone; people are placed afterwards on the best few (a maze's
        # people are part of its walls, so there they are placed at every step)
        self.place_in_search = self.maze
        if self.maze:
            # narrow passages, branches that end, open floor costly; not mirrored
            self.sym = False
            self.walk_cap = self.van_walk * 4
            self.W_DEAD, self.W_OPEN = -0.5, 3.0
            # the leader moves too: she stands where the maze ends (chosen per design in score)
            lead = (self.leader.x, self.leader.y)
            if (lead[0] // 2, lead[1] // 2) not in self.lock:
                self.move_leader = True
                self.kept = [o for o in self.kept if o is not self.leader]
                self.must_floor = {(o.x, o.y) for o in self.kept}

    # ------------------------------------------------------------------ grids
    def cells(self, blocks):
        g = [[None] * self.W2 for _ in range(self.H2)]
        for by in range(self.H):
            for bx in range(self.W):
                p = self.cat.pattern[blocks[by * self.W + bx]]
                for q in range(4): g[by*2 + (q >> 1)][bx*2 + (q & 1)] = p[q]
        return g

    def drawable(self, g, blks):
        for (bx, by) in blks:
            if (bx, by) in self.lock: continue
            p = tuple(g[by*2 + (q >> 1)][bx*2 + (q & 1)] for q in range(4))
            if all(c in '.#' for c in p) and p not in self.pats: return False
        return True

    # ------------------------------------------------------------------ scoring
    def score(self, g, place=True, rng=None):
        """(score, placement, info) of floor plan g, or None if it breaks a rule; with place=True the
        trainers are placed too (rng chooses among equal spots)."""
        W2, H2 = self.W2, self.H2
        block = {(o.x, o.y) for o in self.kept}
        lead_at = None
        if getattr(self, 'move_leader', False):
            # the leader stands at the far end of the maze: the reachable floor tile farthest from
            # the door, a dead-end tip if there is one (she spins: any side reaches her)
            def wk(c): x, y = c; return 0 <= x < W2 and 0 <= y < H2 and walkable(g[y][x]) and c not in block
            d0 = {st: 0 for st in self.starts if wk(st)}; q = deque(d0)
            while q:
                c = q.popleft()
                for dx, dy in D4:
                    n = (c[0] + dx, c[1] + dy)
                    if n not in d0 and wk(n): d0[n] = d0[c] + 1; q.append(n)
            cands = [c for c in d0 if c not in self.near_door and c not in set(self.starts)]
            tips = [c for c in cands if sum(1 for dx, dy in D4 if wk((c[0] + dx, c[1] + dy))) == 1]
            pool = tips or cands
            if not pool: return None
            lead_at = max(pool, key=lambda c: (d0[c], c))
            self.leader_front = [(lead_at[0] + dx, lead_at[1] + dy) for dx, dy in D4]
            block = block | {lead_at}
        def walk(c): x, y = c; return 0 <= x < W2 and 0 <= y < H2 and walkable(g[y][x]) and c not in block
        def bfs(srcs, avoid=()):
            dist, prev = {}, {}
            dq = deque()
            for s in srcs:
                if walk(s) and s not in avoid: dist[s] = 0; dq.append(s)
            while dq:
                c = dq.popleft()
                for dx, dy in D4:
                    n = (c[0] + dx, c[1] + dy)
                    if n not in dist and n not in avoid and walk(n): dist[n] = dist[c] + 1; prev[n] = c; dq.append(n)
            return dist, prev
        dist, prev = bfs(self.starts)
        fronts = [t for t in self.leader_front if t in dist]
        if not fronts: return None
        lt = min(fronts, key=lambda t: dist[t])
        path = [lt]
        while path[-1] in prev: path.append(prev[path[-1]])
        path.reverse()
        walk_len = len(path)
        inner = set(path[3:-2])
        # a second way: the leader is still reachable with the first way's middle blocked
        d2, p2 = bfs(self.starts, avoid=inner)
        alt = lt in d2 and len(inner) > 2
        path2 = []
        if alt:
            path2 = [lt]
            while path2[-1] in p2: path2.append(p2[path2[-1]])
        free = self.free_tiles
        floor_free = [c for c in free if g[c[1]][c[0]] == '.']
        unreached = sum(1 for c in floor_free if c not in dist)
        dead = sum(1 for c in floor_free if c in dist and sum(1 for dx, dy in D4 if walk((c[0]+dx, c[1]+dy))) == 1)
        def isw(x, y): return not (0 <= x < W2 and 0 <= y < H2) or not walkable(g[y][x])
        lone = ends = 0
        for (x, y) in free:
            if g[y][x] != '#': continue
            n = [isw(x+dx, y+dy) for dx, dy in D4]
            if not any(n): lone += 1
            elif sum(n) == 1: ends += 1
        opn = sum(1 for (x, y) in floor_free if all(not isw(x+dx, y+dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)))
        share = 1 - len(floor_free) / max(1, len(free))
        s = (self.W_WALK * min(walk_len, getattr(self, 'walk_cap', 1e9)) + self.W_ALT * alt
             - self.W_UNREACH * unreached - self.W_DEAD * dead - self.W_LONE * lone - self.W_ENDS * ends
             - self.W_OPEN * opn - self.W_SHARE * abs(share - getattr(self, 'van_share', share)))
        if getattr(self, 'maze', False):
            # corridor tiles: floor with exactly two ways on
            corr = sum(1 for c in floor_free if c in dist and sum(1 for dx, dy in D4 if walk((c[0]+dx, c[1]+dy))) == 2)
            s += 0.4 * corr
        info = dict(walk=walk_len, alt=int(alt), unreached=unreached, dead=dead, lone=lone, open=opn,
                    share=round(share, 2))
        if not place: return s, None, info
        rng = rng or random.Random(0)
        ways = set(path) | set(path2)
        # (a gym with a Strength gate places its people in optimize_gate, never here)
        pl = self.place(g, dist, ways, rng, self.units, self.others, set(self.leader_front))
        if pl is None: return None
        pos, watched = pl
        info['watched'] = watched
        if lead_at is not None: pos[self.leader.index] = lead_at
        if getattr(self, 'maze', False):
            # the maze as the player meets it: people are walls
            occ = set(pos.values())
            d3, _ = bfs(self.starts, avoid=occ)
            fd = [d3[t] for t in self.leader_front if t in d3]
            if not fd: return None
            walkp = min(fd) + 1
            lost = sum(1 for c in floor_free if c in dist and c not in d3 and c not in occ)
            info['walk_people'] = walkp
            s += self.W_WALK * (min(walkp, self.walk_cap) - min(walk_len, self.walk_cap)) - self.W_UNREACH * lost
        return s + self.W_WATCH * watched, pos, info


    # ------------------------------------------------------------------ people
    def place(self, g, dist, ways, rng, units, others, goal):
        """(spots, trainers watching) for the movable people (units) and others on plan g: trainers
        watch the way to the goal where possible. None if they do not fit."""
        base = set(dist)
        W2 = self.W2
        def cuts(free):
            disc, low, out = {}, {}, set()
            t = 0
            for root in sorted(free):
                if root in disc: continue
                disc[root] = low[root] = t; t += 1
                stack = [(root, None, iter(D4))]; kids = 0
                while stack:
                    v, par, it = stack[-1]
                    adv = False
                    for dx, dy in it:
                        n = (v[0] + dx, v[1] + dy)
                        if n not in free or n == par: continue
                        if n in disc: low[v] = min(low[v], disc[n]); continue
                        disc[n] = low[n] = t; t += 1
                        if v == root: kids += 1
                        stack.append((n, v, iter(D4))); adv = True; break
                    if adv: continue
                    stack.pop()
                    if par is not None:
                        low[par] = min(low[par], low[v])
                        if par != root and low[v] >= disc[par]: out.add(par)
                if kids > 1: out.add(root)
            return out
        fronts = set(goal)
        def ok_occ(occ):
            fr = base - occ
            if not (fronts & fr): return False
            return all(any((t[0]+dx, t[1]+dy) in fr for dx, dy in D4) for t in occ)
        def view(c, sight, dirs, occ):
            seen = set()
            for dx, dy in dirs:
                for k in range(1, sight + 1):
                    s = (c[0] + dx*k, c[1] + dy*k)
                    if s not in base or s in occ: break
                    seen.add(s)
            return seen
        def stops(c, sight, dirs, fr):
            out = []
            for dx, dy in dirs:
                for k in range(1, sight):
                    s = (c[0] + dx*k, c[1] + dy*k)
                    if s not in fr or (s[0] + dx, s[1] + dy) not in fr: break
                    out.append(s)
            return out
        def dirs_of(o):
            return [FACING[o.movement]] if o.movement in FACING else list(D4)
        taken = set(); pos = {}; watched = 0
        # in a symmetric gym, trainers whose facings mirror each other stand as mirror twins
        units = list(units)
        rng.shuffle(units)
        groups = []
        if self.sym:
            left = list(units)
            while left:
                u = left.pop(0)
                mate = None
                if len(u) == 1:
                    o = u[0]
                    for v in left:
                        if len(v) == 1 and int(v[0].sight or 0) == int(o.sight or 0) and \
                           FACING.get(v[0].movement) == MIRROR.get(FACING.get(o.movement)) and o.movement in FACING:
                            mate = v; break
                if mate: left.remove(mate); groups.append([u, mate])
                else: groups.append([u])
        else:
            groups = [[u] for u in units]
        def mir(c): return (W2 - 1 - c[0], c[1])
        for grp in groups:
            opts = []
            for c in sorted(base):
                cells = []
                for k, unit in enumerate(grp):
                    offs = [(o.x - unit[0].x, o.y - unit[0].y) for o in unit]
                    anchor = c if k == 0 else mir(c)
                    if k == 1: offs = [(-ox, oy) for ox, oy in offs]
                    cells.append([(anchor[0] + ox, anchor[1] + oy) for ox, oy in offs])
                flat = [x for cs in cells for x in cs]
                if len(set(flat)) != len(flat): continue
                if any(x not in base or x in taken or x in ways or x in self.near_door or x in self.fixed for x in flat): continue
                sees = set()
                for unit, cs in zip(grp, cells):
                    for o, x in zip(unit, cs):
                        sees |= view(x, int(o.sight or 0), dirs_of(o), taken | set(flat))
                opts.append((0 if sees & ways else 1, rng.random(), cells, flat))
            opts.sort(key=lambda t: (t[0], t[1]))
            pick = None
            cut0 = cuts(base - taken)
            for w, _r, cells, flat in opts[:40]:
                if any(x in cut0 for x in flat): continue
                occ = taken | set(flat)
                if not ok_occ(occ): continue
                fr = base - occ
                cut1 = cuts(fr)
                bad = False
                for unit, cs in zip(grp, cells):
                    for o, x in zip(unit, cs):
                        if int(o.sight or 0) <= 0: continue
                        ds = dirs_of(o); ds2 = ds + [(-a, -b) for a, b in ds if (-a, -b) not in ds]
                        if any(st in cut1 for st in stops(x, int(o.sight), ds2, fr)): bad = True; break
                    if bad: break
                if bad: continue
                pick = (w, cells, flat); break
            if pick is None: return None
            for unit, cs in zip(grp, pick[1]):
                for o, x in zip(unit, cs): pos[o.index] = x
            taken |= set(pick[2]); watched += (1 - pick[0]) * len(grp)
        if getattr(self, 'maze', False):
            # people who never leave their tile (Fuchsia's look-alikes only spin; no sight) are
            # part of the maze: each goes where the walk to the leader gets longest, everyone
            # still reachable and no floor shut off behind anyone
            fr0 = [t for t in goal]
            starts = [c for c in self.starts if c in base]
            def reach(free):
                dd = {c: 0 for c in starts if c in free}; q = deque(dd)
                while q:
                    c = q.popleft()
                    for dx, dy in D4:
                        n = (c[0] + dx, c[1] + dy)
                        if n in free and n not in dd: dd[n] = dd[c] + 1; q.append(n)
                return dd
            for o in others:
                cs = [c for c in base if c not in taken and c not in self.fixed and c not in self.near_door
                      and c not in goal and c not in set(self.starts)]
                rng.shuffle(cs)
                best = None
                for c in cs[:24]:
                    occ = taken | {c}
                    free = base - occ
                    dd = reach(free)
                    fd = [dd[t] for t in fr0 if t in dd]
                    if not fd: continue
                    if not all(any((x[0] + dx, x[1] + dy) in dd for dx, dy in D4) for x in occ): continue
                    if len(dd) < len(free): continue          # no floor shut off behind anyone
                    val = min(fd) + 0.5 * rng.random()
                    if best is None or val > best[0]: best = (val, c)
                if best is None: return None
                pos[o.index] = best[1]; taken.add(best[1])
            return pos, watched
        for o in others:
            cut0 = cuts(base - taken)
            cs = sorted(c for c in base if c not in taken and c not in ways and c not in self.fixed
                        and c not in cut0 and c not in set(self.starts))
            rng.shuffle(cs)
            for c in cs:
                if ok_occ(taken | {c}): pos[o.index] = c; taken.add(c); break
            else:
                return None
        return pos, watched

    # ------------------------------------------------------------------ search
    def anneal(self, rng, iters):
        """one simulated-annealing chain over the free tiles: [(score, plan, what score() returned)] of
        the valid designs it found."""
        W2, H2 = self.W2, self.H2
        g = [row[:] for row in self.van]
        for (x, y) in self.free_tiles: g[y][x] = '.'
        # a Strength gate's boulders are part of the search: every step is scored with the submoves
        # its gate needs; people are placed after the search (optimize_gate)
        joint = bool(self.gate)
        if joint:
            memo = {}
            fl = [(x, y) for (x, y) in self.free_tiles if (x, y) not in self.near_door and (x, y) not in self.leader_front]
            bl = dict(zip([o.index for o in self.gate], rng.sample(fl, len(self.gate))))
            def total(r_):
                if not r_: return -1e9, r_
                sub = self.gate_submoves(g, bl, memo)
                if sub is None: return -1e9, None
                lone = self.lone_boulders(bl)
                return (r_[0] + self.joint_w * sub - self.joint_lone * lone,
                        (r_[0], dict(bl), dict(r_[2], submoves=sub, lone=lone)))
        cur_r = self.score(g, place=self.place_in_search, rng=random.Random(0))
        cur = (total(cur_r)[0] if joint else cur_r[0]) if cur_r else -1e9
        found = []
        T0 = 3.0
        for it in range(iters):
            T = T0 * (1 - it / iters) + 0.05
            x, y = rng.choice(self.free_tiles)
            if joint and rng.random() < self.joint_move:
                # one gate boulder moved (near where it was, mostly)
                i = rng.choice(sorted(bl)); c0 = bl[i]
                cand = [c for c in self.free_tiles if walkable(g[c[1]][c[0]]) and c not in bl.values()
                        and c not in self.near_door and c not in self.leader_front]
                near = [c for c in cand if abs(c[0] - c0[0]) + abs(c[1] - c0[1]) <= 3]
                if not cand: continue
                bes = self.beside_others(i, bl, cand) if rng.random() < 0.5 else []
                bl[i] = rng.choice(bes or (near if near and rng.random() < 0.7 else cand))
                sc, rr = total(self.score(g, place=self.place_in_search, rng=random.Random(it)))
                if sc >= cur or rng.random() < math.exp((sc - cur) / T):
                    cur = sc
                    if rr and rr[2]['submoves'] >= 1 and not rr[2]['lone']: found.append((sc, tuple(tuple(row) for row in g), rr))
                else: bl[i] = c0
                continue
            if self.gate and rng.random() < 0.08:
                # a gate: a wall right across the hall, its only gap as wide as the boulder row, and
                # floor on both sides of the gap to push from and into
                # its gap 1 to 3 tiles wide (the boulders need not stand in a row)
                gw = rng.randint(1, min(3, len(self.gate)))
                x0 = rng.randrange(0, W2 - gw + 1)
                cells = [(a, y) for a in range(W2) if (a, y) not in self.fixed and not x0 <= a < x0 + gw]
                cells = [c for c in cells if c not in self.must_floor]
                # (two rows each side: the middle boulder is pushed two tiles so the gap opens
                # beside it, and pulled back the same way from inside)
                gap = [(a, b) for a in range(x0, x0 + gw) for b in (y - 2, y - 1, y, y + 1, y + 2)
                       if 0 <= b < H2 and (a, b) not in self.fixed]
                old = [(c, g[c[1]][c[0]]) for c in cells + gap]
                for (a, b) in cells: g[b][a] = '#'
                for (a, b) in gap: g[b][a] = '.'
                if not self.drawable(g, {(a // 2, b // 2) for (a, b), _ in old}):
                    for (a, b), o in old: g[b][a] = o
                    continue
                if joint:
                    # the boulders into the gap (and beside it), as the earlier gate did
                    bl0 = dict(bl)
                    spots = [(a, y) for a in range(x0, x0 + gw)] + [c for c in gap if c[1] != y]
                    spots = [c for c in spots if c not in self.near_door and c not in self.leader_front]
                    for k_, i in enumerate(sorted(bl)):
                        if k_ < len(spots): bl[i] = spots[k_] if k_ < gw else rng.choice(spots[gw:] or spots)
                    if len(set(bl.values())) < len(bl): bl = bl0
                    sc, r = total(self.score(g, place=self.place_in_search, rng=random.Random(it)))
                else:
                    r = self.score(g, place=self.place_in_search, rng=random.Random(it))
                    sc = r[0] if r else -1e9
                if sc >= cur or rng.random() < math.exp((sc - cur) / T):
                    cur = sc
                    if r and (not joint or (r[2]['submoves'] >= 1 and not r[2]['lone'])): found.append((sc, tuple(tuple(row) for row in g), r))
                else:
                    for (a, b), o in old: g[b][a] = o
                    if joint: bl = bl0
                continue
            horiz = rng.random() < 0.5
            L = rng.choice((1, 2, 2, 3, 3, 4, 5, 6))
            v = '#' if rng.random() < 0.55 else '.'
            cells = [(x + k, y) if horiz else (x, y + k) for k in range(L)]
            if self.sym: cells += [(W2 - 1 - a, b) for a, b in cells]
            cells = [c for c in cells if 0 <= c[0] < W2 and 0 <= c[1] < H2 and c not in self.fixed
                     and not (v == '#' and c in self.must_floor)]
            if not cells: continue
            old = [(c, g[c[1]][c[0]]) for c in cells]
            if all(o == v for _, o in old): continue
            for (a, b) in cells: g[b][a] = v
            if not self.drawable(g, {(a // 2, b // 2) for a, b in cells}):
                for (a, b), o in old: g[b][a] = o
                continue
            if joint:
                s, r = total(self.score(g, place=self.place_in_search, rng=random.Random(it)))
            else:
                r = self.score(g, place=self.place_in_search, rng=random.Random(it))
                s = r[0] if r else -1e9
            if s >= cur or rng.random() < math.exp((s - cur) / T):
                cur = s
                if r and (not joint or (r[2]['submoves'] >= 1 and not r[2]['lone'])): found.append((s, tuple(tuple(row) for row in g), r))
            else:
                for (a, b), o in old: g[b][a] = o
        return found

    def draw(self, g, rng):
        """blocks for a tile grid: the gym's own blocks per pattern, then re-chosen for seams."""
        out = bytearray(self.m.blocks)
        own = self.own
        fb = self.free_blocks
        for (bx, by) in fb:
            p = tuple(g[by*2 + (q >> 1)][bx*2 + (q & 1)] for q in range(4))
            bs = self.pats[p]
            out[by * self.W + bx] = max(bs, key=lambda b: (own.ufreq.get((b, p), 0), rng.random()))
        def nb(bx, by):
            r = {}
            for k, (dx, dy) in (('L', (-1, 0)), ('R', (1, 0)), ('U', (0, -1)), ('D', (0, 1))):
                if 0 <= bx + dx < self.W and 0 <= by + dy < self.H: r[k] = out[(by + dy) * self.W + bx + dx]
            return r
        for _ in range(4):
            for (bx, by) in fb:
                p = tuple(g[by*2 + (q >> 1)][bx*2 + (q & 1)] for q in range(4))
                n = nb(bx, by)
                out[by * self.W + bx] = max(self.pats[p], key=lambda b: (own.score(b, p, n, fw=0.7), -b))
        return bytes(out)

    # Strength gate (Cianwood): the gate boulders are designed with the walls in the layout search
    W_MOVES = 12.0         # optimize_gate: score per submove
    gate_layouts = 4       # layouts whose boulders are polished (optimize_gate)
    joint_w = 16.0         # layout score per submove the gate needs
    joint_move = 0.3       # share of search steps that move a boulder
    joint_polish = 1500    # boulder-only steps on the search's best designs
    joint_lone = 12.0      # every gate boulder touches another (8 ways): penalty per lone one
    gate_limit = 12000     # solver positions per design

    @staticmethod
    def lone_boulders(cells):
        """gate boulders with no other boulder in the 8 tiles around them"""
        cs = list(cells.values())
        return sum(1 for c in cs if not any(o != c and abs(o[0] - c[0]) <= 1 and abs(o[1] - c[1]) <= 1 for o in cs))

    def gate_cells(self, m2):
        """{index: tile} of the gate boulders on a drawn map (not the kept one beside the leader)"""
        ids = {o.index for o in self.gate}
        return {o.index: (o.x, o.y) for o in m2.objects if o.index in ids}

    @staticmethod
    def beside_others(i, cells, cand):
        """tiles of `cand` touching (8 ways) a gate boulder other than `i`"""
        os_ = [c for j, c in cells.items() if j != i]
        return [c for c in cand if any(abs(o[0] - c[0]) <= 1 and abs(o[1] - c[1]) <= 1 for o in os_)]

    def gate_submoves(self, g, cells, memo):
        """the fewest submoves (runs of one boulder pushed one way) to reach the leader with the gate
        boulders at `cells` ({object index: tile}); 0 if the gate shuts nothing or a boulder is off
        the reachable floor, None if it cannot be opened"""
        from maprando.checks.pushes import min_moves
        W2, H2 = self.W2, self.H2
        key = (tuple(''.join(r) for r in g), frozenset(cells.values()))
        if key in memo: return memo[key]
        block = {(o.x, o.y) for o in self.kept}
        dist, dq = set(), deque()
        for c in self.starts:
            x, y = c
            if 0 <= x < W2 and 0 <= y < H2 and walkable(g[y][x]) and c not in block: dist.add(c); dq.append(c)
        while dq:
            x, y = dq.popleft()
            for dx, dy in D4:
                n = (x + dx, y + dy)
                if n not in dist and 0 <= n[0] < W2 and 0 <= n[1] < H2 and walkable(g[n[1]][n[0]]) and n not in block:
                    dist.add(n); dq.append(n)
        fr = [t for t in self.leader_front if t in dist]
        out = 0
        if fr and all(c in dist and c not in self.fixed and c not in self.near_door and c not in self.leader_front
                      for c in cells.values()):
            idx = lambda c: c[1] * W2 + c[0]
            mv = min_moves(W2, {idx(c) for c in dist}, {i: idx(c) for i, c in cells.items()},
                           [idx(c) for c in self.starts if c in dist], [idx(c) for c in fr],
                           limit=self.gate_limit, detail=True, weight=0)
            out = None if mv is None else mv[1]
        memo[key] = out
        return out

    def optimize_gate(self, g, pos0, rng, evals=8000):
        """a Strength gate designed for the most boulder moves (a move: a run of pushes of one
        boulder) the player needs before the leader is open. Walls and boulders change together:
        a step flips one tile (as long as its block can still be drawn) or moves one boulder; the
        aim is W_MOVES x moves + the layout's own score. Then nobody can be shut in (the door is
        always reachable again) and people stand outside / inside the gate as in vanilla (any other
        area behind boulders is extra, like a ledge's). Returns (moves, grid, {object index: cell})
        or None."""
        from maprando.checks.pushes import min_moves
        from maprando.checks.boulder_room import player_traps
        W2, H2 = self.W2, self.H2
        block = {(o.x, o.y) for o in self.kept}
        g = [list(row) for row in g]
        idx = lambda c: c[1] * W2 + c[0]
        ids = [o.index for o in self.gate]
        def walk(c): x, y = c; return 0 <= x < W2 and 0 <= y < H2 and walkable(g[y][x]) and c not in block
        def bfs(srcs, avoid=()):
            dist, prev = {}, {}
            dq = deque()
            for s_ in srcs:
                if walk(s_) and s_ not in avoid: dist[s_] = 0; dq.append(s_)
            while dq:
                c = dq.popleft()
                for dx, dy in D4:
                    n = (c[0] + dx, c[1] + dy)
                    if n not in dist and n not in avoid and walk(n): dist[n] = dist[c] + 1; prev[n] = c; dq.append(n)
            return dist, prev
        memo, lay = {}, {}
        def layout_of():
            # the layout's own score and reach, once per layout (boulder moves keep the layout)
            gk = tuple(''.join(r) for r in g)
            v = lay.get(gk)
            if v is None:
                r = self.score(g, place=False)
                v = lay[gk] = (r, bfs(self.starts)[0] if r is not None else None)
            return gk, v
        def ev(cells):
            gk, (r, dist) = layout_of()
            key = (gk, frozenset(cells.values()))
            if key in memo: return memo[key]
            out = None
            if r is not None and not self.lone_boulders(cells):
                fr = [t for t in self.leader_front if t in dist]
                if fr and all(c in dist for c in cells.values()):
                    mv = min_moves(W2, {idx(c) for c in dist}, {i: idx(c) for i, c in cells.items()},
                                   [idx(c) for c in self.starts if c in dist], [idx(c) for c in fr], limit=self.gate_limit,
                                   detail=True, weight=0)
                    # (submoves only: the fewest runs of one boulder pushed one way, whatever the moves)
                    if mv is not None and mv[1] >= 1: out = (mv[0] or 0, mv[1], r[0])
            memo[key] = out
            return out
        cur = {i: pos0[i] for i in ids if i in pos0}
        if len(cur) < len(ids): return None
        cv = ev(cur)
        if cv is None: return None
        # submoves: runs of one boulder's pushes in one direction
        J = lambda v: self.W_MOVES * v[1]
        cj = J(cv)
        keep = [(cv, [row[:] for row in g], dict(cur))]
        T0 = 6.0
        for it in range(evals):
            T = T0 * (1 - it / evals) + 0.3
            i = rng.choice(ids); c0 = cur[i]
            d_ = layout_of()[1][1]
            if d_ is None: continue
            cand = [c for c in d_ if c not in self.fixed and c not in self.near_door
                    and c not in self.leader_front and c not in cur.values()]
            near = [c for c in cand if abs(c[0] - c0[0]) + abs(c[1] - c0[1]) <= 3]
            if not cand: continue
            bes = self.beside_others(i, cur, cand) if rng.random() < 0.5 else []
            nxt = dict(cur); nxt[i] = rng.choice(bes or (near if near and rng.random() < 0.7 else cand))
            v = ev(nxt)
            if v is not None and (J(v) >= cj or rng.random() < math.exp((J(v) - cj) / T)):
                cur, cv, cj = nxt, v, J(v); keep.append((cv, [row[:] for row in g], dict(cur)))
        keep.sort(key=lambda t: (-t[0][1], -t[0][2]))
        tried = set()
        for (mv, sub, ls), gg, cells in keep:
            key = (tuple(''.join(r) for r in gg), frozenset(cells.values()))
            if key in tried: continue
            tried.add(key)
            if len(tried) > 60: break
            g = gg
            dist, prev = bfs(self.starts)
            fr = [t for t in self.leader_front if t in dist]
            Fi = {idx(c) for c in dist}
            bi = {i: idx(c) for i, c in cells.items()}
            starts_i = [idx(c) for c in self.starts if c in dist]
            occ = set(cells.values())
            d0, _ = bfs(self.starts, avoid=occ)
            di, _ = bfs(fr, avoid=occ)
            near_b = {(b[0] + dx, b[1] + dy) for b in occ for dx in range(-2, 3) for dy in range(-2, 3)
                      if abs(dx) + abs(dy) <= 2}
            path = [min(fr, key=lambda t: dist[t])]
            while path[-1] in prev: path.append(prev[path[-1]])
            ways = set(path) | near_b
            out_units = [u for u in self.units if u[0].index not in self.inner_objs]
            in_units = [u for u in self.units if u[0].index in self.inner_objs]
            pl = self.place(g, d0, ways & set(d0), rng, out_units,
                            [o for o in self.others if o.index not in self.inner_objs], set(self.starts) & set(d0))
            if pl is None: continue
            pos, _w = pl
            pl = self.place(g, di, ways & set(di), rng, in_units,
                            [o for o in self.others if o.index in self.inner_objs], set(self.leader_front))
            if pl is None: continue
            pos.update(pl[0])
            # nobody can be shut in: from every position the door is reachable again (people are
            # walls)
            ppl = {idx(c) for i, c in pos.items()}
            tr = player_traps(W2, Fi - ppl, frozenset(), set(), {}, bi, starts_i, set(starts_i), limit=60000)
            if tr is None or tr[1]:
                continue
            pos.update(cells)
            return (mv, sub), tuple(tuple(r) for r in g), pos
        return None

    def generate(self, seed, iters=None, chains=3, keep=15):
        """the new map for a seed: the best design of a few chains that draws and passes every check
        (self.info has the walk, watching trainers and boulder moves)."""
        if iters is None: iters = min(40 * len(self.free_tiles), 4000)   # Goldenrod: 280 tiles
        found = []
        for k in range(chains):
            rng = random.Random(f'{seed}-{self.m.name}-gym-{k}')
            found += self.anneal(rng, iters)
        found.sort(key=lambda t: -t[0])
        seen, last = set(), None
        rng = random.Random(f'{seed}-{self.m.name}-draw')
        if not self.place_in_search and not self.gate:
            # people on the best layouts, then the layouts ranked again with them
            full = []
            for s_, g_, r_ in found:
                if g_ in seen: continue
                seen.add(g_)
                if len(seen) > 40: break
                rf = self.score(g_, rng=random.Random(f'{seed}-{self.m.name}-people-{len(seen)}'))
                if rf is not None: full.append((rf[0], g_, rf))
            full.sort(key=lambda t: -t[0])
            found = full
            seen = set()
        if self.gate:
            # a Strength gate: the boulders of the best few layouts are moved for the most boulder
            # moves; layouts are then tried most moves first
            opt, tries_ = [], 0
            for s_, g_, r_ in found:
                if g_ in seen: continue
                seen.add(g_)
                if len(opt) >= self.gate_layouts or len(seen) > 20 or tries_ >= self.gate_layouts + 3: break
                # the boulders the search designed with the layout, then polished a little
                rf = (s_, r_[1], r_[2])
                tries_ += 1
                o_ = self.optimize_gate(g_, rf[1], random.Random(f'{seed}-{self.m.name}-boulders-{len(seen)}'),
                                        evals=self.joint_polish)
                if o_ is None: continue
                mvt, g2, pos = o_
                opt.append((mvt, s_, g2, (s_, pos, dict(rf[2], moves=mvt[0], submoves=mvt[1]))))
            opt.sort(key=lambda t: (-t[0][1], -t[1]))
            found = [(s_, g_, r_) for mv, s_, g_, r_ in opt]
            seen = set()
        for s, g, r in found:
            if g in seen: continue
            seen.add(g)
            if len(seen) > keep: break
            m2 = copy.deepcopy(self.m); m2.blocks = self.draw(g, rng)
            for o in m2.objects:
                if o.index in r[1]: o.x, o.y = r[1][o.index]
            self.cave.m2 = m2
            ok, why = self.cave.verify()
            if ok and self.gate:
                # every Strength gate (designed for moves or not): solvable push by push, and no
                # position shuts the player in
                # the drawn map's puzzle, push by push, as the readback checks it
                from maprando.checks.pushes import gym_puzzle
                mv, st, tr = gym_puzzle(self.d, m2, self.perms)
                if mv is None or st is None or tr: ok, why = False, f'Strength puzzle: moves {mv}, traps {tr} of {st}'
                elif self.lone_boulders(self.gate_cells(m2)):
                    ok, why = False, 'a gate boulder touches no other'
            if ok:
                self.m2, self.info = m2, dict(r[2], score=round(s, 1))
                return m2
            last = why
        raise GenError(f'{self.m.name}: no gym design passed ({last})')
