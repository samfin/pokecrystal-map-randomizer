import sys, random; sys.path.insert(0,'.')
from collections import defaultdict
from maprando.parse import Disasm
from maprando.model import load_permissions, build_model, classify
from maprando.catalog import Catalog
from maprando.gen import CaveGenerator, GenError, PortConflict
from maprando.ascii import render
n=sys.argv[1]; want=sys.argv[2] if len(sys.argv)>2 else None
d=Disasm('../pokecrystal-speedchoice2'); perms=load_permissions(d); m=d.maps[n]
g=CaveGenerator(d,m,perms,Catalog(d,m.tileset,perms,list(d.maps.values())))
for k in range(80):
    rng=random.Random(f'1-{n}-{k}'); g.gate_pool=defaultdict(list); g.gate_cells=[]
    for cell,(kk,b,o) in g.mm.objects.items():
        if kk=='bits' and (o.x//2,o.y//2) not in g.lock: g.gate_pool[b].append(o)
    try:
        g.place(rng); g.build_plan(rng); g.erode(rng,check_only=True); g.erode(rng); g.smooth(rng)
        if not g.route: g.voidify()
        g.place_objects(rng); g.decorate(rng); g.pick_blocks(rng)
    except PortConflict as e: g.lock.add(e.blk); continue
    except GenError as e: continue
    ok,diff=g.verify()
    if ok or (want and want not in str(diff)): continue
    print('try',k,diff)
    mm2=build_model(d,g.m2,perms)
    st=g.terrain_stats(mm2)
    marks={c:'S' for c in st['water_stray']}
    lockc={mm2.idx(x,y) for (bx,by) in g.lock for x in (bx*2,bx*2+1) for y in (by*2,by*2+1)}
    print(render(mm2,None,marks))
    print('locked blocks:', sorted(g.lock)[:60])
    pass
    print('plan grass cells', sum(r.count(',') for r in g.P))
    break
if '--plan' in sys.argv:
    sym='0123456789abcdefghijklmnop'
    for y,r in enumerate(g.Zc):
        print(f'{y:3d} '+''.join(('L' if (x//2,y//2) in g.lock else '.') if z is None else sym[z%26] for x,z in enumerate(r))+'   '+''.join(c[0] for c in g.P[y]))
