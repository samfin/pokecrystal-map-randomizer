"""Randomize map layouts: the entry point shared by the command line build and the web app.

    from maprando.io.disasm import load_disasm
    from maprando.randomizer import Randomizer

    r = Randomizer(load_disasm('pokecrystal-speedchoice2'))   # reading files happens only here
    maps, log = r.randomize_maps('my seed')                   # {name: new MapInfo}, {name: report}

Every map is generated from its own seed, so a map comes out the same whether the maps are
generated one after another or spread over worker processes (build.py)."""
from __future__ import annotations
import random
import traceback
from maprando.checks.boulder_room import BoulderRoom, BoulderRoomError
from maprando.checks.pushes import room_submoves
from maprando.checks.ship import spin_trainers
from maprando.generators.caves import GenError, generate_cave
from maprando.generators.chambers import build_room
from maprando.generators.gyms.blackthorn import BoulderGymScratch, GymBoulderError
from maprando.generators.gyms.ecruteak import PitGymGenerator
from maprando.generators.gyms.mahogany import IceGymGenerator
from maprando.generators.gyms.saffron import PadGymGenerator
from maprando.generators.gyms.standard import GymGenerator, PUZZLE_GYMS
from maprando.generators.ice import IceGenerator, IceError
from maprando.io.disasm import Disasm, MapInfo
from maprando.maps import ALL_MAPS, ICE, GYMS, ROUTES, KNOWN_UNSUPPORTED
from maprando.model.catalog import Catalog, catalog_for
from maprando.model.movement import load_permissions

# slow maps first: when the maps are spread over workers, they finish at about the same time
SLOW_MAPS = ['BlackthornGym2F', 'IcePathB1F', 'Route19', 'IcePath1F', 'FuchsiaGym', 'BurnedTowerB1F',
             'GoldenrodGym', 'CianwoodGym']


def map_order(names: list[str] = ALL_MAPS) -> list[str]:
    """the maps to randomize (KNOWN_UNSUPPORTED left out), slowest first."""
    todo = [n for n in names if n not in KNOWN_UNSUPPORTED]
    return sorted(todo, key=lambda n: (SLOW_MAPS.index(n) if n in SLOW_MAPS else len(SLOW_MAPS),
                                       n not in ICE, n not in ROUTES, n))


class Randomizer:
    """Randomizes maps of one disassembly.

    overrides: maps as the Item Randomizer mode leaves them, {name: blocks}, plus '__events__':
    {name: {(kind, index): (x, y)}} for the events it moves (maprando.io.item_rando).
    cursed: routes are drawn with every block of their tileset that has the right collision, in a
    patchwork (logic is unchanged). The disassembly is changed in place: S.S. Aqua's trainers
    spin and the overrides apply."""
    tries = 400             # cave layout attempts per map (routes: at most 150)
    chamber_tries = 4       # chamber rooms built for Blackthorn 2F (each ~2 s)

    def __init__(self, disasm: Disasm, overrides: dict | None = None, cursed: bool = False):
        self.d = disasm
        self.cursed = cursed
        self.perms = load_permissions(disasm)
        self.catalogs = {}
        spin_trainers(disasm)      # S.S. Aqua trainers spin: generate and check with that
        overrides = dict(overrides or {})
        events = overrides.pop('__events__', {})
        for n, blocks in overrides.items():
            disasm.maps[n].blocks = blocks
        # event positions the Item Randomizer mode rewrites (hidden items moved off an NPC's tile)
        for n, evs in events.items():
            for (kind, i), (x, y) in evs.items():
                e = (disasm.maps[n].bg_events if kind == 'bg' else disasm.maps[n].objects)[i]
                e.x, e.y = x, y

    def randomize_maps(self, seed: str, names: list[str] = ALL_MAPS
                       ) -> tuple[dict[str, MapInfo], dict[str, str]]:
        """randomize every map in names: ({name: new MapInfo}, {name: report}). A map whose
        generator fails stays vanilla and its report says why."""
        maps, log = {}, {n: f'vanilla ({KNOWN_UNSUPPORTED[n]})' for n in names if n in KNOWN_UNSUPPORTED}
        for n in map_order(names):
            m2, log[n] = self.randomize_map(n, seed)
            if m2 is not None: maps[n] = m2
        return maps, {n: log[n] for n in names}

    def randomize_map(self, n: str, seed: str) -> tuple[MapInfo | None, str]:
        """(new MapInfo or None for vanilla, report). A bug in one generator keeps that map vanilla
        instead of failing the whole build."""
        try:
            return self._randomize(n, seed)
        except Exception as e:
            where = traceback.format_exc(limit=-1).strip().splitlines()[-2].strip()
            return None, f'vanilla (error: {type(e).__name__}: {e}; {where})'

    def _randomize(self, n, seed):
        try:
            if n in ICE: return self._ice(n, seed)
            if n in PUZZLE_GYMS: return self._puzzle_gym(n, seed)
            if n in GYMS: return self._gym(n, seed)
            return self._cave(n, seed)
        except GenError as e:
            return None, f'vanilla ({e})'

    def _cave(self, n, seed):
        """caves, routes and the other outdoor areas (generators/caves)."""
        m = self.d.maps[n]
        tries = min(self.tries, 150) if n in ROUTES else self.tries
        cat = catalog_for(self.d, m, self.perms, self.catalogs)
        m2, _, g = generate_cave(self.d, m, self.perms, cat, seed, tries, cursed=self.cursed)
        if getattr(g, 'split_info', None) and getattr(g, 'allow_split', True):
            return m2, 'randomized (ledge loop: %(parts)d parts)' % g.split_info
        return m2, 'randomized'

    def _ice(self, n, seed):
        """Ice Path: new rocks on the ice (generators/ice.py); B1F also gets a new boulder room."""
        d, m, perms = self.d, self.d.maps[n], self.perms
        ig = IceGenerator(d, m, perms)
        if n == 'IcePath1F':
            # as many decisions as possible (forced slides, at corners, are free), rocks never
            # touching (not even diagonally)
            ig.objective, ig.spread, ig.edge_trivial = 'decisions', True, True
        try: m2, _ = ig.generate(seed)
        except IceError as e: return None, f'vanilla ({e})'
        if n != 'IcePathB1F':
            return m2, 'randomized'
        # the Strength-boulder room, built from chambers: small pieces designed whole (walls, holes
        # and boulders together) for the most boulder moves, joined by corridors (generators/chambers.py)
        try:
            room = BoulderRoom(d, m, perms, Catalog(d, m.tileset, perms, [m]), base=m2)
            # the ladders move too: their blocks join the free ones
            holes = {w for _, w in room.pairs}
            ladders = {(w.x // 2, w.y // 2) for k, w in enumerate(m2.warps)
                       if k not in holes and (w.x // 2, w.y // 2) in room.room}
            g = BoulderGymScratch(d, m, perms, free=sorted(set(room.free) | ladders), room=room.room,
                                  move_stairs=True, base=m2)
            built = build_room(g, random.Random(f'{seed}-{n}-chambers'))
        except (GymBoulderError, BoulderRoomError):
            built = None
        if built is None:
            return m2, 'randomized (ice only; boulder room kept: no chamber room found)'
        m3, (moves, submoves) = built[0], built[1][:2]
        return m3, f'randomized (boulder room from chambers: {moves} boulder moves, {submoves} submoves)'

    def _puzzle_gym(self, n, seed):
        """the gyms whose puzzle is the point (generators/gyms/: one module each)."""
        d, m, perms = self.d, self.d.maps[n], self.perms
        kind = PUZZLE_GYMS[n]
        if kind == 'pit':
            g = PitGymGenerator(d, m, perms); m2 = g.generate(seed)
            return m2, 'randomized (invisible floor: walk %(walk)d, %(turns)d turns)' % g.info
        if kind == 'pads':
            g = PadGymGenerator(d, m, perms); m2 = g.generate(seed)
            return m2, 'randomized (pads re-paired: %(hops)d hops to the leader)' % g.info
        if kind == 'ice':
            g = IceGymGenerator(d, m, perms); m2 = g.generate(seed)
            return m2, 'randomized (ice: %(press)d presses to the leader)' % g.info
        assert kind == 'boulders', kind
        return self._blackthorn(n, seed)

    def _blackthorn(self, n, seed):
        """Blackthorn Gym 2F: the from-scratch design and a few chamber rooms compete on the fewest
        submoves the finished room needs; a tie keeps the from-scratch one."""
        d, m, perms = self.d, self.d.maps[n], self.perms
        cands, last = [], None
        for k in range(3):
            try:
                m2 = BoulderGymScratch(d, m, perms).generate(f"{seed}-{k}", chains=2, iters=8000)
                cands.append((room_submoves(d, m, m2, perms) or 0, 1, m2, 'from scratch'))
                break
            except GymBoulderError as e: last = e
        for k in range(self.chamber_tries):
            try:
                built = build_room(BoulderGymScratch(d, m, perms), random.Random(f'{seed}-{n}-chambers-{k}'), name=n)
            except GymBoulderError: built = None
            if built is not None:
                cands.append((room_submoves(d, m, built[0], perms) or 0, 0, built[0], 'from chambers'))
        if not cands:
            return None, f'vanilla ({last or "no boulder room found"})'
        sub, _, m2, how = max(cands, key=lambda c: (c[0], c[1]))
        return m2, f'randomized (boulder gym {how}: {sub} submoves; of {len(cands)} rooms)'

    def _gym(self, n, seed):
        """trainer gyms, drawn from scratch around the walk to the leader (generators/gyms/standard.py).
        A few sub-seeds: a gym whose best designs all fail the checks tries again."""
        last = None
        for k in range(4):
            try:
                g = GymGenerator(self.d, self.d.maps[n], self.perms, self.catalogs)
                m2 = g.generate(seed if k == 0 else f'{seed}-{k}')
                break
            except GenError as e: last = e
        else: raise last
        msg = 'randomized (gym: walk %d, %d trainers watching, second way %d' % (
            g.info.get('walk', 0), g.info.get('watched', 0), g.info.get('alt', 0))
        if 'moves' in g.info: msg += ', %d boulder moves (%d submoves)' % (g.info['moves'], g.info.get('submoves', 0))
        return m2, msg + ')'
