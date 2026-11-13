#!/usr/bin/env python3
"""Compare extracted geometric requirements with the Item Randomizer's Map Data YAML.

For every YAML location whose Code starts with a label that matches an object/bg script in a
randomizable map, compare:
  ours  = abilities required from *every* entry of the map (intersection of all minimal sets)
  yaml  = geometric ItemReqs on the location plus its ancestors within the same .asm file
"""
import glob, json, os, re, sys, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maprando.io.disasm import Disasm
from maprando.model.movement import build_model, load_permissions, ABILITY_NAMES
from maprando.model.reach import requirement_matrix

GEOM = set(ABILITY_NAMES.values())
HERE = os.path.dirname(os.path.abspath(__file__))
IR = os.path.join(HERE, '..', 'Pokemon-Crystal-Item-Randomizer', 'Map Data')

def walk(locs, parent, out):
    for e in locs or []:
        e['_parent'] = parent
        out.append(e)
        walk(e.get('Sublocations'), e, out)

def first_label(code):
    for line in (code or '').splitlines():
        m = re.match(r'^([A-Za-z_]\w*)::?\s*$', line.strip())
        if m: return m.group(1)
    return None

def main():
    d = Disasm(os.path.join(HERE, '..', 'pokecrystal-speedchoice2'))
    perms = load_permissions(d)
    locs = []
    for f in sorted(glob.glob(os.path.join(IR, '*.yml'))):
        y = yaml.safe_load(open(f))
        if isinstance(y, dict): walk(y.get('Location'), None, locs)
    by_name = {}
    for l in locs: by_name.setdefault(l.get('Name'), []).append(l)
    models = {}
    rows = []
    for l in locs:
        fn = l.get('FileName') or ''
        mapname = fn[:-4] if fn.endswith('.asm') else None
        if not mapname or mapname not in d.maps: continue
        m = d.maps[mapname]
        if m.environment not in ('ROUTE', 'CAVE', 'DUNGEON'): continue
        lab = first_label(l.get('Code'))
        if not lab: continue
        if mapname not in models:
            mm = build_model(d, m, perms)
            models[mapname] = (mm, requirement_matrix(mm))
        mm, mat = models[mapname]
        anchor = None
        for o in m.objects:
            if o.script == lab: anchor = f'obj:{o.const or o.index}'
        for b in m.bg_events:
            if b.script == lab: anchor = f'bg:{b.index}'
        if anchor is None: continue
        # yaml requirements: own + ancestors in same file (via _parent and LocationReqs chain)
        yreq, cur, seen = set(), l, set()
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            yreq |= {str(x) for x in (cur.get('ItemReqs') or [])} & GEOM
            yreq |= {str(x)[:-len(' Usable')] for x in (cur.get('FlagReqs') or []) if str(x).endswith(' Usable')} & GEOM
            nxt = cur['_parent']
            if nxt is None:
                lr = cur.get('LocationReqs')
                lr = lr[0] if isinstance(lr, list) and lr else lr
                cands = [c for c in by_name.get(lr, []) if c.get('FileName') == fn]
                nxt = cands[0] if cands else None
            cur = nxt
        all_masks = [ms for row in mat.values() for aid, ms in row.items() if aid == anchor]
        if not all_masks:
            rows.append((mapname, l['Name'], anchor, 'UNREACHABLE', sorted(yreq))); continue
        best = [x for ms in all_masks for x in ms]
        inter = None
        for x in best: inter = x if inter is None else inter & x
        ours = {n for b, n in ABILITY_NAMES.items() if inter & b}
        anyours = set()
        for x in best: anyours |= {n for b, n in ABILITY_NAMES.items() if x & b}
        status = 'ok'
        if ours - yreq: status = 'OURS_STRICTER'
        elif yreq - anyours: status = 'YAML_STRICTER'
        rows.append((mapname, l['Name'], anchor, status, sorted(ours), sorted(yreq)))
    import collections
    print(collections.Counter(r[3] for r in rows))
    for r in rows:
        if r[3] != 'ok': print(r)
    json.dump(rows, open(os.path.join(HERE, 'out', 'crosscheck.json'), 'w'), indent=1)

main()
