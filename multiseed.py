import sys, os, time; sys.path.insert(0, '.')
from collections import Counter
from maprando.parse import Disasm, load_disasm
from maprando.model import load_permissions
from maprando.catalog import Catalog, catalog_for
from maprando.gen import CaveGenerator, GenError
from generate import CAVES, ROUTES
MAPS = [a for a in os.environ.get('MAPS', '').split(',') if a] or CAVES + ROUTES
TRIES = int(os.environ.get('TRIES', '400'))
d = load_disasm('../pokecrystal-speedchoice2'); perms = load_permissions(d)
seeds = sys.argv[1:] or [str(i) for i in range(5)]
cats = {}; t0 = time.time(); tally = Counter(); tries = Counter()
for n in MAPS:
    m = d.maps[n]
    cat = catalog_for(d, m, perms, cats)
    try: g = CaveGenerator(d, m, perms, cat)
    except GenError as e: print(f'{n:28s} SKIP {e}'); continue
    for sd in seeds:
        try:
            _, k = g.generate(sd, TRIES); tally[n] += 1; tries[n] += k
        except GenError: pass
    sys.stdout.flush(); print(f'{n:28s} {time.time()-t0:5.0f}s {tally[n]}/{len(seeds)} ok, avg tries {tries[n]/max(1,tally[n]):.1f}')
print(f'total {sum(tally.values())}/{len(seeds)*len(MAPS)} in {time.time()-t0:.0f}s')
