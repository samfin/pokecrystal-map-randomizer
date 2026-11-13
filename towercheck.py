import sys; sys.path.insert(0,'.')
from maprando.io.disasm import Disasm
from maprando.model.movement import load_permissions
from maprando.model.catalog import Catalog
from maprando.generators.caves import CaveGenerator, GenError
from maprando.maps import TOWERS, ROUTES
d=Disasm('../pokecrystal-speedchoice2'); perms=load_permissions(d); cats={}
names = (ROUTES if sys.argv[2:3] == ["routes"] else sys.argv[2:]) or TOWERS; seeds = sys.argv[1] if len(sys.argv) > 1 else '123'
for n in names:
    m=d.maps[n]; cats.setdefault(m.tileset, Catalog(d,m.tileset,perms,list(d.maps.values())))
    ok=0; last=''
    try:
        g=CaveGenerator(d,m,perms,cats[m.tileset])
        for sd in seeds:
            try: g.generate(sd, int(__import__("os").environ.get("TRIES", "400"))); ok+=1
            except GenError as e: last=str(e)[-80:]
        print(f'{n:22s} {ok}/{len(seeds)}  zones={len(g.Z):2d} locked={len(g.lock):3d}/{m.width*m.height}  {last}  {dict(g.errors.most_common(3)) if last else ""}')
    except GenError as e: print(f'{n:22s} SKIP {e}')
