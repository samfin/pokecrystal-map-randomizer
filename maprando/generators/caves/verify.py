"""Checks a finished layout must pass: same logic as vanilla, and none of the known traps."""
import copy
from maprando.generators.caves.common import D4
from maprando.generators.caves.guards import guard_leaks, standing_in_walls, trigger_matrix
from maprando.model.movement import build_model, classify, open_tile
from maprando.model.reach import requirement_matrix


class VerifyMixin:
    def terrain_stats(self, mm2):
        """reachable / present water, grass and headbutt trees outside locked blocks."""
        from maprando.model.reach import Searcher, entry_starts
        sr = Searcher(mm2)
        reach = set(sr.search([st for a in mm2.anchors if a.is_entry for st in entry_starts(mm2, a)]))
        locked = {mm2.idx(x, y) for (bx, by) in self.lock for x in (bx*2, bx*2+1) for y in (by*2, by*2+1)
                  if x < mm2.w and y < mm2.h}
        def near_reach(c):
            x, y = c % mm2.w, c // mm2.w
            return any(mm2.inb(x+dx, y+dy) and mm2.idx(x+dx, y+dy) in reach for dx, dy in D4)
        st = {}
        water = [c for c in range(mm2.w * mm2.h) if classify(mm2, mm2.coll[c]) == 'water']
        st['water_reach'] = any(c in reach for c in water)
        st['water_free'] = [c for c in water if c not in locked]
        occupied = set(mm2.objects)   # an NPC standing on water (swimmers) is not stray water
        st['water_stray'] = [c for c in water if c not in reach and c not in locked and c not in occupied]
        st['forced_free'] = [c for c in range(mm2.w * mm2.h) if classify(mm2, mm2.coll[c]) == 'forced' and c not in locked]
        grass = [c for c in range(mm2.w * mm2.h) if mm2.coll[c] in (0x18, 0x14)]
        st['grass_reach'] = any(c in reach for c in grass)
        st['grass_free'] = [c for c in grass if c not in locked]
        hb = [c for c in range(mm2.w * mm2.h) if mm2.coll[c] == 0x15]
        st['hb_reach'] = any(near_reach(c) for c in hb)
        st['hb_free'] = [c for c in hb if c not in locked]
        return st

    def encounter_rules(self, mm2):
        """encounter table <=> reachable terrain, for Surf (water), grass and headbutt trees.
        A table only demands terrain if vanilla actually has it reachable."""
        tab = self.tables()
        if not hasattr(self, '_van_stats'): self._van_stats = self.terrain_stats(self.mm)
        v, g = self._van_stats, self.terrain_stats(mm2)
        if tab['water']:
            if v['water_reach'] and not g['water_reach']: return 'Surf table but no reachable water'
        elif not v['water_reach'] and (g['water_free'] or g['forced_free']): return 'no Surf table but water'
        if g['water_stray']: return f"{len(g['water_stray'])} unreachable water cells"
        if tab['grass']:
            if v['grass_reach'] and not g['grass_reach']: return 'grass table but no reachable grass'
        elif not v['grass_reach'] and g['grass_free']: return 'no grass table but grass'
        if tab['headbutt']:
            if v['hb_reach'] and not g['hb_reach']: return 'headbutt table but no reachable headbutt tree'
        elif g['hb_free']: return 'no headbutt table but headbutt trees'
        return None

    def zones_reachable(self, mm2):
        """every zone (walkable area) vanilla can reach must be reachable here too, even if it
        holds no check or warp (the requirement table alone would not notice it missing)."""
        from maprando.model.reach import Searcher, entry_starts
        sr = Searcher(mm2)
        reach = set(sr.search([st for a in mm2.anchors if a.is_entry for st in entry_starts(mm2, a)]))
        seen = set()
        for y, row in enumerate(self.Zc):
            for x, z in enumerate(row):
                if z is not None and mm2.idx(x, y) in reach: seen.add(z)
        missing = [z for z in self.Z if z not in seen and z not in self.objz and z not in self.T]
        return f'zones not reachable: {missing}' if missing else None

    def trainers_ok(self, mm2):
        from maprando.checks.trainers import unsafe_trainers
        if not hasattr(self, 'vanilla_unsafe'):
            self.vanilla_unsafe = {(o.index, o.x, o.y) for o in unsafe_trainers(self.mm)}
        bad = [o for o in unsafe_trainers(mm2) if (o.index, o.x, o.y) not in self.vanilla_unsafe]
        return f'trainer can trap the player: {bad[0].const}' if bad else None

    def boulders_ok(self, mm2):
        # a Strength puzzle whose solution and traps were checked push by push (Cianwood Gym)
        if getattr(self, 'puzzle_checked', False): return None
        from maprando.checks.boulders import bad_boulders
        if not hasattr(self, 'vanilla_bad_boulders'):
            self.vanilla_bad_boulders = {(o.index, o.x, o.y) for o in bad_boulders(self.mm)}
        bad = [o for o in bad_boulders(mm2) if (o.index, o.x, o.y) not in self.vanilla_bad_boulders]
        return f'boulder cannot be pushed out of the way: {bad[0].const}' if bad else None

    def people_ok(self, mm2):
        """with everyone standing on their tile (walking NPCs too), and with any trainer walked up
        to the player, no area may fall apart more than in vanilla."""
        from maprando.checks.blocking import split_pairs, trainer_people_traps
        from maprando.model.metrics import reachable
        if not hasattr(self, 'vanilla_people'):
            r = reachable(self.mm)
            self.vanilla_people = (split_pairs(self.mm, self.m, r),
                                   {t for t, _ in trainer_people_traps(self.mm, self.m, r)})
        r2 = reachable(mm2)
        if split_pairs(mm2, self.m2, r2) > self.vanilla_people[0]: return 'people block a path'
        bad = [t for t, _ in trainer_people_traps(mm2, self.m2, r2) if t not in self.vanilla_people[1]]
        return f'{bad[0]} can block a path together with someone else' if bad else None

    def nook_count(self, mm2):
        """hidden items standing at the tip of a one-tile dead end."""
        def walk(x, y): return mm2.inb(x, y) and open_tile(mm2, mm2.idx(x, y))   # (cut trees count as open)
        return sum(1 for b in self.m2.bg_events if b.kind == 'BGEVENT_ITEM' and walk(b.x, b.y)
                   and sum(1 for dx, dy in D4 if walk(b.x + dx, b.y + dy)) == 1)

    def hidden_ok(self, mm2):
        """every hidden item reached from one zone and not left at its vanilla spot must be the tip
        of a one-tile dead end in the finished map (no block choice may reopen it)."""
        mm = mm2
        # a cut tree beside the nook counts as open: cut, it would make the nook a way on
        def walk(x, y): return mm.inb(x, y) and open_tile(mm, mm.idx(x, y))
        for b0, b in zip(self.m.bg_events, self.m2.bg_events):
            if b0.kind != 'BGEVENT_ITEM' or (b.x, b.y) == (b0.x, b0.y): continue
            S = self.anchor_zones.get(f'bg:{b0.index}', ())
            if len([z for z in S if self.kind.get(z) == 'land']) > 1: continue   # in the wall between two areas
            if len(S) != 1 and not any(self.kind.get(z) == 'land' for z in S): continue
            if not walk(b.x, b.y): continue          # a rock at the water's edge
            if sum(1 for dx, dy in D4 if walk(b.x + dx, b.y + dy)) != 1:
                return 'hidden item is not at the tip of a dead end'
        return None

    def signs_ok(self, mm2):
        """a sign moved with its block can still be read: the tile it is read from is floor"""
        for i, (x, y) in getattr(self, 'sign_place', {}).items():
            u = next((u for u in self.sign_units.values() if u[0].index == i), None)
            if u is None: continue
            fx, fy = x + u[3][0], y + u[3][1]
            if not mm2.inb(fx, fy) or classify(mm2, mm2.coll[mm2.idx(fx, fy)]) != 'land':
                return f'sign {u[0].script} cannot be read'
        return None

    def triggers_ok(self, mm2):
        """no way round a gate trigger that vanilla does not have (Route 32: a second way south past
        the Cooltrainer who walks you back without the Zephyr Badge)"""
        if not self.m.script: return None
        if not hasattr(self, '_trig_v'):
            self._trig_src = self.m.script
            self._trig_v = trigger_matrix(self.mm, self.m, self._trig_src)
        if self._trig_v is None: return None
        return None if trigger_matrix(mm2, self.m2, self._trig_src) == self._trig_v else 'a way round a scene trigger'

    def guard_ok(self, mm2):
        bad = guard_leaks(mm2, self.m2, self.m)
        return f'{bad[0]} can be talked to without its scene (crash)' if bad else None

    def standing_ok(self, mm2):
        """nobody who stands on floor in vanilla starts in a wall (objects parked out of reach, and
        fruit trees / counter NPCs that stand in walls in vanilla, are fine)"""
        bad = standing_in_walls(self.mm, self.m, mm2, self.m2, self.anchor_zones)
        return f'{bad[0]} starts in a wall' if bad else None

    def carpets_ok(self, mm2):
        from maprando.model.movement import dead_carpets
        if not hasattr(self, 'vanilla_dead_carpets'): self.vanilla_dead_carpets = set(dead_carpets(self.mm))
        bad = [w for w in dead_carpets(mm2) if w not in self.vanilla_dead_carpets]
        return f'doormat warp {bad[0]} has floor beyond it' if bad else None

    def swap_variants(self, m):
        """the map as scripts can leave it: each changeblock on its own, then all of them at once."""
        out = []
        sets = [[sw] for sw in self.swaps] + ([self.swaps] if len(self.swaps) > 1 else [])
        for group in sets:
            mv = copy.copy(m); b = bytearray(m.blocks)
            for bx, by, blk in group: b[by * self.Wb + bx] = blk
            mv.blocks = bytes(b); out.append((group, mv))
        return out

    def swaps_ok(self):
        """a door opened, a hole revealed or rocks dropped by a script must connect the same way as
        in vanilla (the room behind Team Rocket Base B3F's password door, Burned Tower's hole)."""
        if not self.swaps: return True, None
        if not hasattr(self, '_swap_mats'):
            self._swap_mats = [requirement_matrix(build_model(self.d, mv, self.perms)) for _, mv in self.swap_variants(self.m)]
        for (group, mv), want in zip(self.swap_variants(self.m2), self._swap_mats):
            if requirement_matrix(build_model(self.d, mv, self.perms)) != want:
                return False, f'after changeblock {group}'
        return True, None

    def edges_ok(self, mm2):
        # what matters is that you cannot get there (a tile kept walkable inside a locked block is
        # fine while nothing reaches it): reachable from some way in
        ew = getattr(self, 'edge_walls', ())
        if not ew: return None
        from maprando.model.reach import Searcher, entry_starts
        sr = Searcher(mm2)
        R = set(sr.search([st for a in mm2.anchors if a.is_entry for st in entry_starts(mm2, a)]))
        if not hasattr(self, '_edge_R0'):
            s0 = Searcher(self.mm)
            self._edge_R0 = set(s0.search([st for a in self.mm.anchors if a.is_entry for st in entry_starts(self.mm, a)]))
        bad = [c for c in ew if c in R and c not in self._edge_R0]
        return f'new way out at the map edge {(bad[0] % mm2.w, bad[0] // mm2.w)}' if bad else None

    def jumps_ok(self, mm2):
        """no ledge jump may land in a wall, on water, on a person or off the map (the game never
        checks where a jump lands: the player is stuck there), beyond what vanilla already has."""
        from maprando.checks.jumps import bad_jumps
        if not hasattr(self, '_van_jumps'): self._van_jumps = {(c, d) for c, d, _ in bad_jumps(self.mm)}
        bad = [(c % mm2.w, c // mm2.w, d) for c, d, _ in bad_jumps(mm2) if (c, d) not in self._van_jumps]
        return f'ledge jump into a wall at {bad[0]}' if bad else None

    def verify(self):
        mm2 = build_model(self.d, self.m2, self.perms)
        self.last_mm2 = mm2
        w = (self.zones_reachable(mm2) or self.encounter_rules(mm2) or self.trainers_ok(mm2)
             or self.boulders_ok(mm2) or self.people_ok(mm2) or self.carpets_ok(mm2) or self.hidden_ok(mm2)
             or self.standing_ok(mm2) or self.guard_ok(mm2) or self.triggers_ok(mm2) or self.signs_ok(mm2)
             or self.edges_ok(mm2)
             or self.jumps_ok(mm2) or self.loop_ok(mm2))
        if w: return False, w
        mat2 = requirement_matrix(mm2, boulders=False)
        if mat2 == self.matrix: return self.swaps_ok()
        for e in self.matrix:
            r1, r2 = self.matrix[e], mat2.get(e, {})
            for a in set(r1) | set(r2):
                if r1.get(a) != r2.get(a):
                    return False, (e, a, r1.get(a), r2.get(a))
        return False, 'entries differ'
