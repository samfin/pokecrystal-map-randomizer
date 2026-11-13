import sys, random; sys.path.insert(0,'.')
from collections import defaultdict
from maprando.parse import Disasm
from maprando.model import load_permissions, build_model
from maprando.catalog import Catalog
from maprando.gen import CaveGenerator, GenError, PortConflict
from maprando.reach import Searcher, entry_starts
from maprando.ascii import render
n, entry, want = sys.argv[1], sys.argv[2], sys.argv[3]
d=Disasm('../pokecrystal-speedchoice2'); perms=load_permissions(d); m=d.maps[n]
g=CaveGenerator(d,m,perms,Catalog(d,m.tileset,perms,list(d.maps.values())))
for k in range(150):
    rng=random.Random(f'1-{n}-{k}'); g.gate_pool=defaultdict(list); g.gate_cells=[]
    for cell,(kk,b,o) in g.mm.objects.items():
        if kk=='bits' and (o.x//2,o.y//2) not in g.lock: g.gate_pool[b].append(o)
    try:
        g.place(rng); g.build_plan(rng); g.erode(rng,check_only=True); g.erode(rng); g.smooth(rng)
        g.place_objects(rng); g.decorate(rng); g.pick_blocks(rng)
    except PortConflict as e: g.lock.add(e.blk); continue
    except GenError: continue
    ok,diff=g.verify()
    if ok or want not in str(diff): continue
    print('try',k,diff)
    mm2=build_model(d,g.m2,perms); s=Searcher(mm2)
    e=[a for a in mm2.anchors if a.id==entry][0]; sets=s.search(entry_starts(mm2,e))
    tgt=[a for a in mm2.anchors if a.id==diff[1]][0]
    marks={c:('*' if 0 in v else '+') for c,v in sets.items()}
    for c in tgt.cells: marks[c]='@'
    for c in e.cells: marks[c]='E'
    print(render(mm2,None,marks))
    break
