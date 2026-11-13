import sys, random; sys.path.insert(0,'.')
from maprando.io.disasm import Disasm
from maprando.model.movement import load_permissions
from maprando.model.catalog import Catalog
from maprando.generators.caves import CaveGenerator, GenError
from collections import defaultdict
n=sys.argv[1]; d=Disasm('../pokecrystal-speedchoice2'); perms=load_permissions(d); m=d.maps[n]
g=CaveGenerator(d,m,perms,Catalog(d,m.tileset,perms,list(d.maps.values())))
print('Z',g.Z,'T',g.T,'objz',g.objz); print('pairs',g.pairs); print('lock',sorted(g.lock))
rng=random.Random(1); g.gate_pool=defaultdict(list); g.gate_cells=[]
for cell,(k,b,o) in g.mm.objects.items():
    if k=='bits': g.gate_pool[b].append(o)
stage='place'
try:
    g.place(rng); stage='plan'; g.build_plan(rng); stage='check'; g.erode(rng,check_only=True)
except GenError as e: print('ERR at',stage,e)
sym='0123456789abcdefghijklmnopqrstuvwxyz'
for by in range(g.Hb):
    row=''
    for bx in range(g.Wb):
        v=g.G[by][bx] if hasattr(g,'G') else None
        row+= '  ' if v is None else 'LL' if v=='LOCK' else (sym[v[1]%36]*2 if v[0]=='Z' else 'D'+v[1][0])
    print(f'{by:2d} {row}')
if hasattr(g,'P'):
    for y,r in enumerate(g.P): print(f'{y:3d} '+''.join(c[0] for c in r)+'   '+''.join('.' if z is None else sym[z%36] for z in g.Zc[y]))
