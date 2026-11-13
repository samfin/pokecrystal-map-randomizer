#!/usr/bin/env python3
"""Generate the supported caves for a seed, write them into a working copy of the disassembly
and build it.

    python3 build.py --seed 7 --out ../output/crystal-maprando.gbc
    python3 build.py --no-maps ...        # plain build of the romhack (no layout changes)

The working copy (--work) is created once and reused: each run first restores the map files
it may have changed from the source tree, so later runs only rebuild what changed."""
import argparse, json, os, shutil, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from generate import ROUTES, ICE, GYMS, ALL_MAPS, KNOWN_UNSUPPORTED

MARK = '.maprando-source'
# Blackthorn 2F from chambers (maprando/chambers.py): off - its puzzles came out dull; the
# from-scratch builder (maprando/gymboulders.py, classic scoring) is used
CHAMBER_TRIES = 4       # chamber rooms built for Blackthorn 2F (each ~2 s)

def prepare_work(src, work):
    src = os.path.abspath(src)
    mark = os.path.join(work, MARK)
    if os.path.exists(mark) and open(mark).read().strip() == src:
        # restore every file a previous run may have rewritten
        from maprando.io.disasm import Disasm
        d = Disasm(src)
        for n in ALL_MAPS:
            m = d.maps[n]
            for rel in (m.blk_path, os.path.join('maps', n + '.asm')):
                a, b = os.path.join(src, rel), os.path.join(work, rel)
                if open(a, 'rb').read() != open(b, 'rb').read():
                    shutil.copy2(a, b)
        return 'reused'
    if os.path.exists(work): shutil.rmtree(work)
    shutil.copytree(src, work, ignore=shutil.ignore_patterns('.git'), symlinks=True)
    open(mark, 'w').write(src)
    return 'created'

_W = {}

def _work_init(src, overrides=None, cache_path=None, worker=False):
    if worker:
        # Ctrl+C is for the main process: it stops the pool (workers would only print tracebacks)
        import signal
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    from maprando.io.disasm import Disasm
    from maprando.model.movement import load_permissions
    import pickle
    d = None
    if cache_path and os.path.exists(cache_path):
        try:
            from maprando.io.disasm import _reroot
            with open(cache_path, 'rb') as f: d = pickle.load(f)
            _reroot(d, os.path.abspath(src))
        except Exception: d = None
    _W['d'] = d or Disasm(src); _W['perms'] = load_permissions(_W['d']); _W['cats'] = {}
    from maprando.checks.ship import spin_trainers
    spin_trainers(_W['d'])      # S.S. Aqua trainers spin: generate and check with that
    for n, blocks in (overrides or {}).items():
        if n == '__events__': continue
        _W['d'].maps[n].blocks = blocks
    # event positions the Item Randomizer mode rewrites (hidden items moved off an NPC's tile)
    for n, evs in (overrides or {}).get('__events__', {}).items():
        for (kind, i), (x, y) in evs.items():
            e = (_W['d'].maps[n].bg_events if kind == 'bg' else _W['d'].maps[n].objects)[i]
            e.x, e.y = x, y

def _timed(job):
    t = time.time()
    try:
        r = _work1(job)
    except Exception as e:
        # a bug in one generator keeps that map vanilla instead of failing the whole build
        import traceback
        r = (job[0], None, f'vanilla (error: {type(e).__name__}: {e}; {traceback.format_exc(limit=-1).strip().splitlines()[-2].strip()})')
    f = os.environ.get('MAPRANDO_TIMES')
    if f:
        with open(f, 'a') as fh: fh.write(f'{time.time() - t:.1f} {job[0]}\n')
    return r

def _work(job):
    """generate one map (runs in a worker process). With MAPRANDO_CACHE set to a folder, each
    finished map is kept there and a later run with the same seed reuses it (for development:
    nothing checks that the code is unchanged)."""
    cache = os.environ.get('MAPRANDO_CACHE')
    if not cache: return _timed(job)
    import pickle
    n, seed, tries = job
    f = os.path.join(cache, f'{seed}-{n}.pkl')
    if os.path.exists(f):
        with open(f, 'rb') as fh: return pickle.load(fh)
    r = _timed(job)
    os.makedirs(cache, exist_ok=True)
    with open(f + '.tmp', 'wb') as fh: pickle.dump(r, fh)
    os.replace(f + '.tmp', f)
    return r

def _work1(job):
    from maprando.model.catalog import Catalog, catalog_for
    from maprando.generators.caves import GenError
    n, seed, tries = job
    d, perms, cats = _W['d'], _W['perms'], _W['cats']
    m = d.maps[n]
    cat = catalog_for(d, m, perms, cats)
    try:
        if n in ICE:
            from maprando.generators.ice import IceGenerator, IceError
            ig = IceGenerator(d, m, perms)
            if n == 'IcePath1F':
                # as many decisions as possible (forced slides, at corners, are free), rocks never
                # touching (not even diagonally)
                ig.objective, ig.spread, ig.edge_trivial = 'decisions', True, True
            try: m2, k = ig.generate(seed)
            except IceError as e: return n, None, f'vanilla ({e})'
            if n == 'IcePathB1F':
                # the Strength-boulder room: new walls, boulder spots and holes (maprando/boulderroom.py)
                from maprando.checks.boulder_room import BoulderRoom, BoulderRoomError
                # built from chambers: small pieces designed whole (walls, holes and boulders
                # together) for the most boulder moves, joined by corridors (maprando/chambers.py)
                import random as _r
                from maprando.generators.gyms.blackthorn import BoulderGymScratch, GymBoulderError
                from maprando.generators.chambers import build_room
                built = None
                try:
                    br_ = BoulderRoom(d, m, perms, Catalog(d, m.tileset, perms, [m]), base=m2)
                    # the ladders move too: their blocks join the free ones
                    hw_ = {w for _, w in br_.pairs}
                    lad_ = {(w.x // 2, w.y // 2) for k, w in enumerate(m2.warps) if k not in hw_ and (w.x // 2, w.y // 2) in br_.room}
                    g_ = BoulderGymScratch(d, m, perms, free=sorted(set(br_.free) | lad_), room=br_.room, move_stairs=True, base=m2)
                    built = build_room(g_, _r.Random(f'{seed}-{n}-chambers'))
                except (GymBoulderError, BoulderRoomError): built = None
                if built is not None:
                    return n, built[0], f'randomized (boulder room from chambers: {built[1][0]} boulder moves, {built[1][1]} submoves)'
                return n, m2, 'randomized (ice only; boulder room kept: no chamber room found)'
        elif n in GYMS:
            from maprando.generators.gyms.standard import GymGenerator, PUZZLE_GYMS
            kind = PUZZLE_GYMS.get(n)
            if kind == 'pit':
                from maprando.generators.gyms.ecruteak import PitGymGenerator
                g = PitGymGenerator(d, m, perms); m2 = g.generate(seed)
                return n, m2, 'randomized (invisible floor: walk %(walk)d, %(turns)d turns)' % g.info
            if kind == 'pads':
                from maprando.generators.gyms.saffron import PadGymGenerator
                g = PadGymGenerator(d, m, perms); m2 = g.generate(seed)
                return n, m2, 'randomized (pads re-paired: %(hops)d hops to the leader)' % g.info
            if kind == 'ice':
                from maprando.generators.gyms.mahogany import IceGymGenerator
                g = IceGymGenerator(d, m, perms); m2 = g.generate(seed)
                return n, m2, 'randomized (ice: %(press)d presses to the leader)' % g.info
            if kind == 'boulders':
                last = None
                if n == 'BlackthornGym2F':
                    # built from chambers (library pieces with the boulders, gate boulders inside;
                    # stairs and trainers in the corridors: maprando/chambers.py)
                    from maprando.generators.gyms.blackthorn import BoulderGymScratch, GymBoulderError
                    from maprando.generators.chambers import build_room
                    import random as _r
                    from maprando.checks.pushes import room_submoves
                    # both kinds compete on the fewest submoves the finished room needs: chamber
                    # rooms (a few seeds; cheap) and the earlier from-scratch design with its
                    # boulders moved for more (maprando/gymboulders.py); a tie keeps the earlier
                    cands = []
                    for k in range(3):
                        try:
                            g = BoulderGymScratch(d, m, perms)
                            m2 = g.generate(f"{seed}-{k}", chains=2, iters=8000)
                            cands.append((room_submoves(d, m, m2, perms) or 0, 1, m2, 'from scratch'))
                            break
                        except GymBoulderError as e: last = e
                    for k in range(CHAMBER_TRIES):
                        try:
                            built = build_room(BoulderGymScratch(d, m, perms), _r.Random(f'{seed}-{n}-chambers-{k}'), name=n)
                        except GymBoulderError: built = None
                        if built is not None:
                            cands.append((room_submoves(d, m, built[0], perms) or 0, 0, built[0], 'from chambers'))
                    if cands:
                        sub, _, m2, how = max(cands, key=lambda c: (c[0], c[1]))
                        return n, m2, f'randomized (boulder gym {how}: {sub} submoves; of {len(cands)} rooms)'
                return n, None, f'vanilla ({last or "no boulder room found"})'
            # a few sub-seeds: a gym whose best designs all fail the checks tries again
            last = None
            for k in range(4):
                try:
                    g = GymGenerator(d, m, perms, cats); m2 = g.generate(seed if k == 0 else f'{seed}-{k}')
                    break
                except GenError as e: last = e
            else: raise last
            msg = 'randomized (gym: walk %d, %d trainers watching, second way %d' % (g.info.get('walk', 0), g.info.get('watched', 0), g.info.get('alt', 0))
            if 'moves' in g.info: msg += ', %d boulder moves (%d submoves)' % (g.info['moves'], g.info.get('submoves', 0))
            return n, m2, msg + ')'
        else:
            from maprando.generators.caves import generate_cave
            m2, k, g = generate_cave(d, m, perms, cat, seed, tries)
            if getattr(g, 'split_info', None) and getattr(g, 'allow_split', True):
                return n, m2, 'randomized (ledge loop: %(parts)d parts)' % g.split_info
        return n, m2, 'randomized'
    except GenError as e:
        return n, None, f'vanilla ({e})'

def generate_maps(src, seed, tries, jobs=None, overrides=None, disasm=None):
    """every map is generated from its own seed, so the result does not depend on how many
    worker processes run or in which order they finish."""
    from maprando.io.disasm import load_disasm
    import multiprocessing as mp
    d = disasm or load_disasm(src); out = {}; log = {}
    cache = getattr(d, 'cache_path', None)
    todo = []
    for n in ALL_MAPS:
        if n in KNOWN_UNSUPPORTED: log[n] = f'vanilla ({KNOWN_UNSUPPORTED[n]})'; continue
        todo.append((n, seed, min(tries, 150) if n in ROUTES else tries))
    # slow maps first: better balance across the workers
    SLOW = ['BlackthornGym2F', 'IcePathB1F', 'Route19', 'IcePath1F', 'FuchsiaGym', 'BurnedTowerB1F', 'GoldenrodGym', 'CianwoodGym']
    todo.sort(key=lambda j: (SLOW.index(j[0]) if j[0] in SLOW else len(SLOW), j[0] not in ICE, j[0] not in ROUTES, j[0]))
    jobs = jobs or max(1, min(os.cpu_count() or 1, 8))
    if jobs == 1:
        _work_init(src, overrides, cache); res = [_work(j) for j in todo]
    else:
        pool = mp.Pool(jobs, initializer=_work_init, initargs=(src, overrides, cache, True))
        try:
            # wait in short slices: a plain blocking map() cannot be interrupted on Windows
            ar = pool.map_async(_work, todo, chunksize=1)
            while not ar.ready(): ar.wait(0.2)
            res = ar.get()
            pool.close()
        except KeyboardInterrupt:
            pool.terminate(); pool.join()
            print('cancelled'); sys.exit(130)
        pool.join()
    for n, m2, msg in res:
        if m2 is not None: out[n] = m2
        log[n] = msg
    return d, out, {n: log[n] for n in ALL_MAPS}

def patch_mode(a):
    """generate caves and write them into an existing romhack build: no make/gcc/rgbds needed."""
    from maprando.io.rom import load_sym, patch_map, fix_checksum, base_matches_source
    t0 = time.time()
    sym_path = os.path.splitext(a.patch_base)[0] + '.sym'
    rom = bytearray(open(a.patch_base, 'rb').read()); sym = load_sym(sym_path)
    log = {}
    if not a.no_maps:
        from maprando.io.disasm import load_disasm
        d0 = load_disasm(a.src)
        stale = base_matches_source(rom, sym, d0, ALL_MAPS)
        if stale:
            print(f'base ROM does not match the romhack source ({", ".join(stale[:5])}...): rebuild it'); return 2
        overrides = {}
        if a.ir_mode:
            from maprando.io.item_rando import ir_block_overrides
            overrides = ir_block_overrides(a.ir_root, a.ir_mode, sym, d0, ALL_MAPS)
            if overrides: print(f'maps as the Item Randomizer mode leaves them: {", ".join(sorted(overrides))}')
            from maprando.io.item_rando import ir_event_overrides
            evs = ir_event_overrides(a.ir_root, a.ir_mode, sym, rom, ALL_MAPS)
            if evs:
                overrides['__events__'] = evs
                print(f'events as the Item Randomizer mode leaves them: {", ".join(sorted(evs))}')
        d, maps, log = generate_maps(a.src, a.seed, a.tries, overrides=overrides, disasm=d0)
        stale = []
        if stale:
            print(f'base ROM does not match the romhack source ({", ".join(stale[:5])}...): rebuild it'); return 2
        for m2 in maps.values(): patch_map(rom, sym, m2)
        from maprando.checks.ship import patch_spins
        k = patch_spins(rom, sym, d0)
        print(f'S.S. Aqua: {k} standing trainers now spin (no forced battles on the ship)')
        fix_checksum(rom)
        print(f'maps: {len(maps)}/{len(ALL_MAPS)} randomized ({time.time()-t0:.0f}s)')
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, 'wb').write(rom)
    json.dump(log, open(os.path.splitext(a.out)[0] + '.maps.json', 'w'), indent=1)
    print(f'built {a.out} ({time.time()-t0:.0f}s)')
    return 0

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', default='1')
    ap.add_argument('--src', default=os.path.join(HERE, '..', 'pokecrystal-speedchoice2'))
    ap.add_argument('--work', default=os.path.expanduser('~/maprando-build'))
    ap.add_argument('--rgbds', default=os.path.join(HERE, '..', 'tools', 'rgbds-0.4.0') + '/')
    ap.add_argument('--out', default=os.path.join(HERE, '..', 'output', 'crystal-maprando.gbc'))
    ap.add_argument('--no-maps', action='store_true', help='build the romhack without changing layouts')
    ap.add_argument('--tries', type=int, default=400)
    ap.add_argument('--ir-mode', help='Item Randomizer mode file: generate from the layouts its patches leave')
    ap.add_argument('--ir-root', default=os.path.join(HERE, '..', 'Pokemon-Crystal-Item-Randomizer'))
    ap.add_argument('--patch-base', help='patch this already-built romhack ROM (needs <rom>.sym beside it) instead of rebuilding')
    a = ap.parse_args(argv)
    if a.patch_base:
        return patch_mode(a)

    t0 = time.time()
    how = prepare_work(a.src, a.work)
    print(f'working copy {how}: {a.work} ({time.time()-t0:.0f}s)')
    log = {}
    if not a.no_maps:
        from maprando.io.disasm_write import emit
        d, maps, log = generate_maps(a.src, a.seed, a.tries)
        for m2 in maps.values(): emit(a.work, m2)
        print(f'maps: {len(maps)}/{len(ALL_MAPS)} randomized ({time.time()-t0:.0f}s)')
    json.dump(log, open(os.path.join(a.work, 'maprando.json'), 'w'), indent=1)
    rgbds = os.path.abspath(a.rgbds) + '/'
    r = subprocess.run(['make', '-j2', f'RGBDS={rgbds}'], cwd=a.work, capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-2000:], r.stderr[-3000:]); return 1
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    shutil.copy(os.path.join(a.work, 'crystal-speedchoice.gbc'), a.out)
    shutil.copy(os.path.join(a.work, 'crystal-speedchoice.sym'), os.path.splitext(a.out)[0] + '.sym')
    json.dump(log, open(os.path.splitext(a.out)[0] + '.maps.json', 'w'), indent=1)
    print(f'built {a.out} ({time.time()-t0:.0f}s)')
    return 0

if __name__ == '__main__':
    sys.exit(main())
