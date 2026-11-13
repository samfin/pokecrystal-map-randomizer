#!/usr/bin/env python3
"""Restore randomized map data the Item Randomizer overwrote afterwards.

Some Item Randomizer patches rewrite map blocks or event coordinates at fixed vanilla
positions (e.g. moving a hidden item off a tile an NPC stands on). On a randomized map those
writes no longer fit, so for every randomized map we copy back our block data and the x/y bytes
of its signs, hidden items and objects. Everything else the Item Randomizer changed is kept.

    python3 reapply.py --ours step1.gbc --final final.gbc --maps-json final.maps.json"""
import argparse, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from maprando.parse import Disasm, load_disasm
from maprando.patch import load_sym, events_layout, fix_checksum, BG, OBJ, WARP, warps_at

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ours', required=True); ap.add_argument('--final', required=True)
    ap.add_argument('--maps-json', required=True)
    ap.add_argument('--vanilla-warps', action='store_true')
    ap.add_argument('--src', default=os.path.join(HERE, '..', 'pokecrystal-speedchoice2'))
    a = ap.parse_args()
    log = json.load(open(a.maps_json))
    names = [n for n, v in log.items() if v.startswith('randomized')]   # 'randomized (gym: ...)' too
    d = load_disasm(a.src)
    sym = load_sym(os.path.join(a.src, 'crystal-speedchoice.sym'))
    ours = open(a.ours, 'rb').read(); fin = bytearray(open(a.final, 'rb').read())
    # vanilla bytes at every place we may write, from the romhack source
    van = bytearray(ours)
    for n in names:
        mv = d.maps[n]
        b = sym[n + '_Blocks']; van[b:b + len(mv.blocks)] = mv.blocks
        bg, nbg, obj, nobj = events_layout(ours, sym[n + '_MapEvents'])
        for i, e in enumerate(mv.bg_events): van[bg + i * BG] = e.y; van[bg + i * BG + 1] = e.x
        for i, o in enumerate(mv.objects): van[obj + i * OBJ + 1] = (o.y + 4) & 0xFF; van[obj + i * OBJ + 2] = (o.x + 4) & 0xFF
        wa, nw = warps_at(ours, sym[n + '_MapEvents'])
        for i, w in enumerate(mv.warps):
            van[wa + i * WARP] = w.y; van[wa + i * WARP + 1] = w.x
            if w.dest_map == mv.const: van[wa + i * WARP + 2] = int(w.dest_idx)
    if a.vanilla_warps:
        # the Item Randomizer refuses moved warps: give it vanilla warp coordinates, restored after
        for n in names:
            wa, nw = warps_at(fin, sym[n + '_MapEvents'])
            for i in range(nw):
                fin[wa + i * WARP:wa + i * WARP + 3] = van[wa + i * WARP:wa + i * WARP + 3]
        fix_checksum(fin); open(a.final, 'wb').write(fin); print('{}'); return
    restored = {}
    for n in names:
        spans = []
        b = sym[n + '_Blocks']; spans.append((b, b + len(d.maps[n].blocks)))
        bg, nbg, obj, nobj = events_layout(ours, sym[n + '_MapEvents'])
        spans += [(bg + i * BG, bg + i * BG + 2) for i in range(nbg)]
        spans += [(obj + i * OBJ + 1, obj + i * OBJ + 3) for i in range(nobj)]
        wa, nw = warps_at(ours, sym[n + '_MapEvents'])
        spans += [(wa + i * WARP, wa + i * WARP + 3) for i in range(nw)]
        # restore only bytes the map randomizer changed (ours differs from vanilla); edits the
        # later randomizers make elsewhere (e.g. patches of their own) stay
        # each span is one unit (an event's coordinates, a warp's coordinates and destination,
        # the block data): if we changed any byte of it, all of it comes back. Byte by byte, a
        # coordinate we happened to leave at its vanilla value kept the Item Randomizer's
        # (Route 32's hidden Great Ball: its x moved by the Item Randomizer, its y by us)
        k = 0
        for s, e in spans:
            if e - s <= 3 and ours[s:e] != van[s:e]:
                for i in range(s, e):
                    if fin[i] != ours[i]: fin[i] = ours[i]; k += 1
                continue
            for i in range(s, e):
                if ours[i] != van[i] and fin[i] != ours[i]: fin[i] = ours[i]; k += 1
        if k: restored[n] = k
    # S.S. Aqua trainers spin; the Item Randomizer may have rewritten their records
    from maprando.ship import patch_spins
    k = patch_spins(fin, sym, d)
    if k: restored['FastShip trainers spinning'] = k
    if restored:
        fix_checksum(fin)
        open(a.final, 'wb').write(fin)
    print(json.dumps(restored))

if __name__ == '__main__':
    main()
