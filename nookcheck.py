import sys; sys.path.insert(0,'.')
from maprando.io.disasm import Disasm
from maprando.model.movement import load_permissions, build_model, classify
from maprando.model.catalog import Catalog
from maprando.generators.caves import CaveGenerator, GenError
from maprando.maps import CAVES
d=Disasm('../pokecrystal-speedchoice2'); perms=load_permissions(d); cats={}
tot=innook=false_nooks=0; locked_hidden=0
for seed in sys.argv[1:] or ['1']:
  for n in CAVES:
    m=d.maps[n]; cats.setdefault(m.tileset, Catalog(d,m.tileset,perms,list(d.maps.values())))
    try: g=CaveGenerator(d,m,perms,cats[m.tileset]); m2,_=g.generate(seed,150)
    except GenError: continue
    mm=build_model(d,m2,perms)
    from maprando.model.reach import Searcher, entry_starts
    sr=Searcher(mm); reach=set(sr.search([st for a in mm.anchors if a.is_entry for st in entry_starts(mm,a)]))
    def walk(x,y): return mm.inb(x,y) and classify(mm,mm.coll[mm.idx(x,y)])=='land'
    hidden={(b.x,b.y) for b in m2.bg_events if b.kind=='BGEVENT_ITEM'}
    locked={(x,y) for (bx,by) in g.lock for x in (bx*2,bx*2+1) for y in (by*2,by*2+1)}
    def is_nook(x,y):
        if not walk(x,y): return False
        nb=[walk(x+dx,y+dy) for dx,dy in ((1,0),(-1,0),(0,1),(0,-1))]
        if sum(nb)!=1: return False
        # all other neighbours solid wall (not water)
        return all(walk(x+dx,y+dy) or not mm.inb(x+dx,y+dy) or classify(mm,mm.coll[mm.idx(x+dx,y+dy)]) is None for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)))
    for (x,y) in hidden:
        if (x,y) in locked: locked_hidden+=1; continue
        tot+=1; k=is_nook(x,y); innook+=k
        if not k: print('  not in nook:', n, (x,y), 'S=', g.anchor_zones.get('bg:%d'%[b.index for b in m2.bg_events if (b.x,b.y)==(x,y)][0]))
    objs={(o.x,o.y) for o in m2.objects}
    for y in range(mm.h):
        for x in range(mm.w):
            if (x,y) in locked or (x,y) in objs: continue
            if mm.idx(x,y) in reach and is_nook(x,y) and (x,y) not in hidden and mm.idx(x,y) not in mm.warp_cells:
                if True: print('  false nook', n, x, y)
                # count only nooks in reachable area
                false_nooks+=1
print(f'hidden items outside locked blocks: {tot}, in a nook: {innook}; hidden items inside locked blocks (vanilla spot): {locked_hidden}; one-cell nooks without a hidden item: {false_nooks}')
