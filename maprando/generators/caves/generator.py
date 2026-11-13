"""Cave layout generator.

1. Condense vanilla into a zone graph (zones + labelled gates).
2. Lock warp/coord-event blocks; seed their zones there.
3. Place every other zone next to an already placed neighbour, building the gate device
   between them (Surf: direct land/water contact; Rock/Boulder/NPC: 1-cell passage holding
   the object; ledge: one-way ledge block; waterfall: waterfall block).
4. Grow zones towards their vanilla size while keeping a wall between zones that must not touch.
5. Roughen edges, place objects/hidden items, pick concrete blocks, then verify that the
   requirement matrix equals vanilla. Any failure raises GenError and the caller retries."""
from collections import defaultdict
import copy, random
from maprando.generators.caves.common import (GenError, MergeError, NookError, PairError, PortConflict, SignError,
                                              SplitError, UnifyError, WarpRoomError)
from maprando.model.movement import build_model
from maprando.model.reach import requirement_matrix
from maprando.model.zones import ZoneGraph
from maprando.generators.caves.analysis import AnalysisMixin
from maprando.generators.caves.placement import PlacementMixin
from maprando.generators.caves.corridors import CorridorMixin
from maprando.generators.caves.plan import PlanMixin
from maprando.generators.caves.objects import ObjectMixin
from maprando.generators.caves.nooks import NookMixin
from maprando.generators.caves.drawing import DrawingMixin
from maprando.generators.caves.ledges import LedgeMixin
from maprando.generators.caves.verify import VerifyMixin


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


class CaveGenerator(AnalysisMixin, PlacementMixin, CorridorMixin, PlanMixin, ObjectMixin, NookMixin,
                    DrawingMixin, LedgeMixin, VerifyMixin):
    """Generates one cave or route layout; the steps live in the mixins (one module each) and
    generate() below runs them in order."""
    best_of = 3

    far_links = True

    @property
    def freq_w(self): return 2.0 if self.route else 0.7

    room = 3.0

    fill = 0.8      # share of the free blocks land zones may claim before thinning

    def __init__(self, disasm, m, perms, catalog):
        self.d, self.m, self.perms, self.cat = disasm, m, perms, catalog
        self.mm = build_model(disasm, m, perms)
        self.zg = ZoneGraph(self.mm)
        self.matrix = requirement_matrix(self.mm, boulders=False)
        self.Wb, self.Hb = m.width, m.height
        self._analyze()

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
                self.corridors(rng)
                self.place_warps(rng)
                self.widen_ledges(rng)
                self.build_plan(rng)
                self.fixed |= getattr(self, 'door_below', set())
                self.erode(rng, check_only=True)
                if not self.route:
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
                    from maprando.model.metrics import steps
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
