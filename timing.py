#!/usr/bin/env python3
"""Time and profile map generation: per-map seconds, and the hottest functions overall.

    python3 timing.py --seed S [--maps A,B] [--profile out.prof]"""
import argparse, cProfile, os, pstats, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from maprando.parse import Disasm, load_disasm
from maprando.model import load_permissions
from maprando.catalog import Catalog, catalog_for
from maprando.gen import CaveGenerator, GenError
from maprando.ice import IceGenerator, IceError
from generate import ALL_MAPS, ROUTES, ICE, KNOWN_UNSUPPORTED

ap = argparse.ArgumentParser(); ap.add_argument('--seed', default='1'); ap.add_argument('--maps')
ap.add_argument('--profile'); a = ap.parse_args()
names = a.maps.split(',') if a.maps else [n for n in ALL_MAPS if n not in KNOWN_UNSUPPORTED]
d = load_disasm(os.path.join(HERE, '..', 'pokecrystal-speedchoice2')); perms = load_permissions(d); cats = {}
prof = cProfile.Profile() if a.profile else None
for n in names:
    m = d.maps[n]; t = time.time()
    if prof: prof.enable()
    try:
        if n in ICE: IceGenerator(d, m, perms).generate(a.seed); k = '-'
        else:
            cat = catalog_for(d, m, perms, cats)
            _, k = CaveGenerator(d, m, perms, cat).generate(a.seed, 150 if n in ROUTES else 400)
    except (GenError, IceError): k = 'FAIL'
    if prof: prof.disable()
    print(f'{n:28s} {time.time() - t:6.2f}s  tries {k}', flush=True)
if prof: prof.dump_stats(a.profile)
