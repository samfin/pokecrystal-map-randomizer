import sys, random; sys.path.insert(0,'.')
from maprando.io.disasm import Disasm
from maprando.model.movement import load_permissions, build_model
from maprando.model.catalog import Catalog
from maprando.generators.caves import CaveGenerator, GenError
from maprando.io.ascii import render
from maprando.model.reach import requirement_matrix
from collections import defaultdict
n=sys.argv[1]; k0=int(sys.argv[2]) if len(sys.argv)>2 else 0
d=Disasm('../pokecrystal-speedchoice2'); perms=load_permissions(d); m=d.maps[n]
g=CaveGenerator(d,m,perms,Catalog(d,m.tileset,perms,list(d.maps.values())))
print('pairs',g.pairs)
for k in range(k0,k0+60):
    rng=random.Random(f'1-{n}-{k}'); g.gate_pool=defaultdict(list); g.gate_cells=[]
    for cell,(kk,b,o) in g.mm.objects.items():
        if kk=='bits': g.gate_pool[b].append(o)
    try:
        g.place(rng); g.build_plan(rng); g.erode(rng,check_only=True); g.erode(rng); g.place_objects(rng); g.pick_blocks(rng)
    except GenError as e: continue
    ok,diff=g.verify()
    if ok: continue
    print('try',k,'diff',diff)
    mm2=build_model(d,g.m2,perms)
    marks={}
    for o in g.m2.objects: marks[mm2.idx(o.x,o.y)]='n' if 'BALL' not in o.const else 'B'
    for dv in g.devices: print('  device',dv[0],dv[1],dv[2],'src',dv[3],'dst',dv[4], getattr(dv[5],'const',None))
    print(render(mm2,None,marks))
    for y,r in enumerate(g.Zc): print(f'{y:3d} '+''.join('.' if z is None else '0123456789abcdefghijklmnop'[z%26] for z in r))
    break
o=[]
if o:
    o=o[0]; print('nerd2 at',o.x,o.y, 'vanilla', [v for v in m.objects if v.const==o.const][0].x, [v for v in m.objects if v.const==o.const][0].y)
    print('S', g.anchor_zones.get('obj:'+o.const))
    print('warps', [(w.x,w.y) for w in g.m2.warps])
    from maprando.model.reach import Searcher, entry_starts
    s=Searcher(mm2); e=[a for a in mm2.anchors if a.id=='warp:0'][0]; sets=s.search(entry_starts(mm2,e))
    for y in range(o.y-2,o.y+3):
        print(''.join(('*' if (mm2.idx(x,y) in sets) else ' ') for x in range(o.x-4,o.x+5)), ' ', render(mm2,[y]).split(' ',1)[1][o.x-4:o.x+5] if 0<=y<mm2.h else '')
