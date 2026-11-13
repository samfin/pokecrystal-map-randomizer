#!/usr/bin/env python3
"""Render vanilla vs generated side by side for each cave: out/preview/<Map>.png"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw
from maprando.parse import Disasm, load_disasm
from maprando.model import load_permissions
from maprando.catalog import Catalog, catalog_for
from maprando.gen import CaveGenerator, GenError
from maprando.render import TileRenderer
from generate import CAVES, ICE
from maprando.ice import IceGenerator, IceError

ap = argparse.ArgumentParser(); ap.add_argument('--seed', default='1'); ap.add_argument('--maps', nargs='*', default=CAVES)
a = ap.parse_args()
d = Disasm(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pokecrystal-speedchoice2'))
perms = load_permissions(d); cats = {}; rends = {}
os.makedirs('out/preview', exist_ok=True)
for n in a.maps:
    m = d.maps[n]
    cat = catalog_for(d, m, perms, cats)
    rends.setdefault(m.tileset, TileRenderer(d, m.tileset))
    try:
        if n in ICE:
            g = IceGenerator(d, m, perms); m2, k = g.generate(a.seed); g.lock = set()
            print(n, "ice (blocks, vanilla (decisions, moves), new (decisions, moves)):", g.report)
        else:
            g = CaveGenerator(d, m, perms, cat); m2, k = g.generate(a.seed, 150)
    except (GenError, IceError) as e:
        print('skip', n, e); continue
    A = rends[m.tileset].render(m, 1); B = rends[m.tileset].render(m2, 1)
    # shade locked blocks on the generated side
    ov = Image.new('RGBA', B.size, (0, 0, 0, 0)); dr = ImageDraw.Draw(ov)
    for bx, by in g.lock: dr.rectangle([bx*32, by*32, bx*32+31, by*32+31], outline=(255, 0, 255, 200), width=1)
    B = Image.alpha_composite(B.convert('RGBA'), ov).convert('RGB')
    out = Image.new('RGB', (A.width * 2 + 16, A.height + 20), (255, 255, 255))
    out.paste(A, (0, 20)); out.paste(B, (A.width + 16, 20))
    dr = ImageDraw.Draw(out); dr.text((2, 4), f'{n} vanilla', fill=(0, 0, 0)); dr.text((A.width + 18, 4), f'generated (seed {a.seed})', fill=(0, 0, 0))
    out.save(f'out/preview/{n}.png'); print('wrote', n)
