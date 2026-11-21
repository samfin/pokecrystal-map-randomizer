#!/usr/bin/env python3
"""Generate the supported caves for a seed, write them into a working copy of the disassembly
and build it.

    python3 build.py --seed 7 --out ../output/crystal-maprando.gbc
    python3 build.py --no-maps ...        # plain build of the romhack (no layout changes)

The working copy (--work) is created once and reused: each run first restores the map files
it may have changed from the source tree, so later runs only rebuild what changed."""
import argparse, json, multiprocessing as mp, os, pickle, shutil, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from maprando.maps import ALL_MAPS, KNOWN_UNSUPPORTED

MARK = '.maprando-source'

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

def _work_init(src, overrides=None, tries=400, worker=False, cursed=False):
    if worker:
        # Ctrl+C is for the main process: it stops the pool (workers would only print tracebacks)
        import signal
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    from maprando.io.disasm import load_disasm
    from maprando.randomizer import Randomizer
    _W['r'] = Randomizer(load_disasm(src), overrides, cursed=cursed)
    _W['r'].tries = tries

def _work(job):
    """randomize one map (runs in a worker process): (name, new map or None, report). With
    MAPRANDO_CACHE set to a folder, each finished map is kept there and a later run with the same
    seed reuses it (for development: nothing checks that the code is unchanged). With
    MAPRANDO_TIMES set to a file, each map's time is added to it."""
    n, seed = job
    cache = os.environ.get('MAPRANDO_CACHE')
    mode = '-cursed' if _W['r'].cursed else ''
    f = cache and os.path.join(cache, f'{seed}{mode}-{n}.pkl')
    if f and os.path.exists(f):
        with open(f, 'rb') as fh: return pickle.load(fh)
    t = time.time()
    r = (n,) + _W['r'].randomize_map(n, seed)
    if os.environ.get('MAPRANDO_TIMES'):
        with open(os.environ['MAPRANDO_TIMES'], 'a') as fh: fh.write(f'{time.time() - t:.1f} {n}\n')
    if f:
        os.makedirs(cache, exist_ok=True)
        with open(f + '.tmp', 'wb') as fh: pickle.dump(r, fh)
        os.replace(f + '.tmp', f)
    return r

def generate_maps(src, seed, tries, jobs=None, overrides=None, disasm=None, cursed=False):
    """randomize ALL_MAPS over worker processes: (disassembly, {name: new map}, {name: report})."""
    from maprando.io.disasm import load_disasm
    from maprando.randomizer import map_order
    d = disasm or load_disasm(src)
    log = {n: f'vanilla ({KNOWN_UNSUPPORTED[n]})' for n in ALL_MAPS if n in KNOWN_UNSUPPORTED}
    todo = [(n, seed) for n in map_order(ALL_MAPS)]
    jobs = jobs or max(1, min(os.cpu_count() or 1, 8))
    if jobs == 1:
        _work_init(src, overrides, tries=tries, cursed=cursed); res = [_work(j) for j in todo]
    else:
        pool = mp.Pool(jobs, initializer=_work_init, initargs=(src, overrides, tries, True, cursed))
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
    out = {}
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
        d, maps, log = generate_maps(a.src, a.seed, a.tries, overrides=overrides, disasm=d0, cursed=a.cursed)
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
    ap.add_argument('--cursed', action='store_true', help='draw routes with every block of their tileset, in a patchwork')
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
        d, maps, log = generate_maps(a.src, a.seed, a.tries, cursed=a.cursed)
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
