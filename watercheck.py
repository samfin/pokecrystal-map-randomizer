"""Water vs encounter tables: every cave with a Surf encounter table must have reachable water;
caves without one must have no water at all (reachable or not)."""
import re, sys, os; sys.path.insert(0, '.')
from maprando.io.disasm import Disasm
from maprando.model.movement import load_permissions, build_model, classify, SURF
from maprando.model.reach import Searcher, entry_starts
from maprando.maps import CAVES

def water_tables(d):
    s = set()
    for f in ('johto_water.asm', 'kanto_water.asm'):
        for line in d.read('data', 'wild', f):
            mm = re.match(r'\s*map_id\s+(\w+)', line)
            if mm: s.add(mm.group(1))
    return s

def water_info(d, m, perms):
    mm = build_model(d, m, perms)
    water = [c for c in range(mm.w * mm.h) if classify(mm, mm.coll[c]) in ('water', 'forced')]
    sr = Searcher(mm)
    reach = set(sr.search([st for a in mm.anchors if a.is_entry for st in entry_starts(mm, a)]))
    rw = [c for c in water if c in reach]
    return len(water), len(rw)

if __name__ == '__main__':
    v = Disasm('../pokecrystal-speedchoice2'); perms = load_permissions(v)
    g = Disasm(sys.argv[1]) if len(sys.argv) > 1 else None
    wt = water_tables(v)
    for n in CAVES:
        m = v.maps[n]; has = m.const in wt
        vw, vr = water_info(v, m, perms)
        line = f'{n:26s} table={"yes" if has else "no ":3s}  vanilla water={vw:4d} reachable={vr:4d}'
        if g:
            gw, gr = water_info(g, g.maps[n], perms)
            ok = (gr > 0) if has else (gw == 0)
            line += f'   generated water={gw:4d} reachable={gr:4d}  {"OK" if ok else "VIOLATION"}'
        print(line)
