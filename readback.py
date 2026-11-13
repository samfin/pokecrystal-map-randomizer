#!/usr/bin/env python3
"""Read every randomized map back out of a final ROM and check its logic matches vanilla.

    python3 readback.py ../output/crystal-<seed>.gbc"""
import copy, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from maprando.parse import Disasm, load_disasm
from maprando.model import build_model, load_permissions, dead_carpets, classify, open_tile
from maprando.gym import GymGenerator
from maprando.reach import requirement_matrix, Searcher, entry_starts, anchor_masks
from maprando.patch import load_sym, events_layout, BG, OBJ, WARP, warps_at
from generate import ALL_MAPS
from maprando.trainers import unsafe_trainers
from maprando.boulders import bad_boulders
from maprando.blocking import split_pairs, trainer_people_traps
from maprando.scenes import blocked_walks
from maprando.jumps import bad_jumps
from maprando.model import _read_src
from maprando.metrics import reachable
from maprando.ice import traps as ice_traps
from generate import ICE

import argparse
_ap = argparse.ArgumentParser(); _ap.add_argument('rom'); _ap.add_argument('--ir-mode')
_a = _ap.parse_args(); rom_path = _a.rom
src = os.path.join(HERE, '..', 'pokecrystal-speedchoice2')
d = load_disasm(src); perms = load_permissions(d)
sym = load_sym(os.path.join(src, 'crystal-speedchoice.sym'))
rom = open(rom_path, 'rb').read()
if _a.ir_mode:   # compare against the layouts the Item Randomizer mode leaves (e.g. no Ilex cut tree)
    from maprando.irpatch import ir_block_overrides
    for n, b in ir_block_overrides(os.path.join(HERE, '..', 'Pokemon-Crystal-Item-Randomizer'), _a.ir_mode, sym, d, ALL_MAPS).items():
        d.maps[n].blocks = b
    # and the event positions its patches rewrite (hidden items moved off a tile someone stands on)
    from maprando.irpatch import ir_event_overrides
    for n, evs in ir_event_overrides(os.path.join(HERE, '..', 'Pokemon-Crystal-Item-Randomizer'), _a.ir_mode, sym, rom, ALL_MAPS).items():
        for (kind, i), (x, y) in evs.items():
            e = (d.maps[n].bg_events if kind == 'bg' else d.maps[n].objects)[i]
            e.x, e.y = x, y
same = changed = 0; bad = []; traps = []; by_warp = []; stuck = []; icetraps = []
hidden_total = hidden_ok = 0; hidden_lost = []; moved_warps = 0; blocked_by_people = []; dead = []; tips = []; not_tips = []; walks_blocked = []; swaps_bad = []; jumps_bad = []
pads_bad = []; in_walls = []; leaks = []; round_trig = []
for n in ALL_MAPS:
    m = d.maps[n]; m2 = copy.deepcopy(m)
    a = sym[n + '_Blocks']; m2.blocks = bytes(rom[a:a + len(m.blocks)])
    bg, nbg, obj, nobj = events_layout(rom, sym[n + '_MapEvents'])
    for i, b in enumerate(m2.bg_events): b.y, b.x = rom[bg + i * BG], rom[bg + i * BG + 1]
    for i, o in enumerate(m2.objects): o.y, o.x = rom[obj + i * OBJ + 1] - 4, rom[obj + i * OBJ + 2] - 4
    wa, nw = warps_at(rom, sym[n + '_MapEvents'])
    for i, w in enumerate(m2.warps):
        w.y, w.x = rom[wa + i * WARP], rom[wa + i * WARP + 1]
        if w.dest_map == m2.const: w.dest_idx = rom[wa + i * WARP + 2]
    if n == 'SaffronGym':
        from maprando.gympads import pad_rooms_ok
        pads_bad = pad_rooms_ok(d, m2, perms)
    moved_warps += sum(1 for a_, b_ in zip(m.warps, m2.warps) if (a_.x, a_.y) != (b_.x, b_.y))
    changed += m2.blocks != m.blocks
    mm, mm2 = build_model(d, m, perms), build_model(d, m2, perms)
    # (Strength boulders themselves are not compared: nothing is there, and every way past one is)
    if requirement_matrix(mm2, boulders=False) == requirement_matrix(mm, boulders=False): same += 1
    else: bad.append(n)
    # trainers that could trap the player (vanilla's own cases at vanilla spots are allowed)
    van = {(o.index, o.x, o.y) for o in unsafe_trainers(mm)}
    traps += [f'{n}:{o.const}' for o in unsafe_trainers(mm2) if (o.index, o.x, o.y) not in van]
    if n in ICE and len(ice_traps(mm2)) > len(ice_traps(mm)): icetraps.append(n)
    # hidden items: can you come to rest facing them from some entrance (all abilities)?
    hid = {b.index for b in m.bg_events if b.kind == 'BGEVENT_ITEM'}
    if hid:
        def reachable_items(model):
            S = Searcher(model); got = set()
            sets = {}
            for e in model.anchors:
                if e.is_entry:
                    for c, ms in S.search([(c, (1 << 40) - 1) for c, _ in entry_starts(model, e)]).items(): sets[c] = ms
            for a in model.anchors:
                if a.id.startswith('bg:') and int(a.id[3:]) in hid and anchor_masks(model, sets, a): got.add(a.id)
            return got
        hv, hg = reachable_items(mm), reachable_items(mm2)
        hidden_total += len(hid); hidden_ok += len(hg)
        hidden_lost += [f'{n}:{x}' for x in hv - hg]
    dead += [f'{n}:warp{w}' for w in dead_carpets(mm2) if w not in set(dead_carpets(mm))]
    from maprando.gen import standing_in_walls
    in_walls += [f'{n}:{c}' for c in standing_in_walls(mm, m, mm2, m2)]
    from maprando.gen import trigger_matrix
    if m.asm_path:
        _src = _read_src(m.asm_path)
        _tv = trigger_matrix(mm, m, _src)
        if _tv is not None and trigger_matrix(mm2, m2, _src) != _tv: round_trig.append(n)
    from maprando.gen import guard_leaks
    leaks += [f'{n}:{c}' for c in guard_leaks(mm2, m2, m)]
    # hidden items: at the tip of a one-tile dead end, or why not
    # (a cut tree counts as open: once cut, the nook would lead on)
    def walk2(x, y): return mm2.inb(x, y) and open_tile(mm2, mm2.idx(x, y))
    for b0, b in zip(m.bg_events, m2.bg_events):
        if b0.kind != 'BGEVENT_ITEM': continue
        tipn = walk2(b.x, b.y) and sum(1 for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)) if walk2(b.x + dx, b.y + dy)) == 1
        if tipn: tips.append(n); continue
        if m2.blocks == m.blocks: why = 'map kept vanilla'
        elif (b.x, b.y) == (b0.x, b0.y): why = 'vanilla spot'
        elif not walk2(b.x, b.y): why = 'in a wall/rock'
        else: why = 'open floor'
        not_tips.append(f'{n}:bg{b0.index} ({why})')
    if m.asm_path:
        import re as _re
        sw = []
        for mo in _re.finditer(r'^\s*changeblock\s+(\d+),\s*(\d+),\s*\$([0-9a-fA-F]+)', _read_src(m.asm_path), _re.M):
            t = (int(mo.group(1)) // 2, int(mo.group(2)) // 2, int(mo.group(3), 16))
            if t not in sw: sw.append(t)
        for group in [[t] for t in sw] + ([sw] if len(sw) > 1 else []):
            mats = []
            for mp in (m, m2):
                mv = copy.copy(mp); b = bytearray(mp.blocks)
                for bx, by, blk in group: b[by * m.width + bx] = blk
                mv.blocks = bytes(b); mats.append(requirement_matrix(build_model(d, mv, perms)))
            if mats[0] != mats[1]: swaps_bad.append(f'{n}:{group}')
    if m.asm_path: walks_blocked += [f'{n}:{w[0]} at {w[1]} ({w[2]})' for w in blocked_walks(mm, m, mm2, m2, _read_src(m.asm_path))]
    vj = {(c, dd) for c, dd, _ in bad_jumps(mm)}
    jumps_bad += [f'{n}:({c % mm2.w},{c // mm2.w}){dd}' for c, dd, _ in bad_jumps(mm2) if (c, dd) not in vj]
    rv, rg = reachable(mm), reachable(mm2)
    if split_pairs(mm2, m2, rg) > split_pairs(mm, m, rv): blocked_by_people.append(n)
    vt = {t for t, _ in trainer_people_traps(mm, m, rv)}
    blocked_by_people += [f'{n}:{t}' for t, _ in trainer_people_traps(mm2, m2, rg) if t not in vt]
    vb = {(o.index, o.x, o.y) for o in bad_boulders(mm)}
    # Ice Path B1F's puzzle boulders are checked by the boulder-puzzle check at the end instead
    if n not in ('IcePathB1F', 'BlackthornGym2F', 'CianwoodGym'):
        stuck += [f'{n}:{o.const}' for o in bad_boulders(mm2) if (o.index, o.x, o.y) not in vb]
    # objects / hidden items on or beside a warp (vanilla spots allowed)
    # only warps you arrive through: a pit nobody lands on (Ecruteak Gym's floor) needs no space
    from maprando.model import arrivable as _arr
    near = {(w.x + dx, w.y + dy) for i, w in enumerate(m2.warps) if _arr(d, m2, i)
            for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))}
    old = {(o.x, o.y) for o in m.objects} | {(b.x, b.y) for b in m.bg_events}
    by_warp += [n for e in list(m2.objects) + list(m2.bg_events) if (e.x, e.y) in near and (e.x, e.y) not in old]
print(f'{same}/{len(ALL_MAPS)} maps logic-identical, {changed} with new layouts, {moved_warps} warps moved; failing: {bad}')
print(f'trainers that could trap the player: {traps or "none"}; objects next to warps: {by_warp or "none"}')
print(f'boulders that cannot be pushed out of the way: {stuck or "none"}; ice maps with spots you cannot slide out of: {icetraps or "none"}')
print(f'hidden items you can reach and face: {hidden_ok}/{hidden_total}; reachable in vanilla but not now: {hidden_lost or "none"}')
print(f'paths blocked by people standing together (walking NPCs included): {blocked_by_people or "none"}')
print(f'doormat exits that cannot fire (floor beyond them): {dead or "none"}')
print(f'hidden items at the tip of a one-tile dead end: {len(tips)}/{len(tips) + len(not_tips)}; others: {not_tips or "none"}')
print(f'scripted walks (cutscenes) blocked by walls or objects: {walks_blocked or "none"}')
print(f'doors/holes/rocks changed by scripts that connect differently from vanilla: {swaps_bad or "none"}')
print(f'ledge jumps that land in a wall, on water or on a person (the player is stuck): {jumps_bad or "none"}')
from maprando.ship import not_spinning, SHIP_MAPS
print(f'people and items starting in a wall: {in_walls or "none"}')
print(f'cutscene actors you can talk to without their scene (crash): {leaks or "none"}')
print(f'ways round a scene trigger that moves the player (Route 32 Cooltrainer...): {round_trig or "none"}')
print(f'S.S. Aqua trainers that do not spin (could force a battle): {not_spinning(rom, sym, load_disasm(src)) or "none"}')
if 'SaffronGym' in ALL_MAPS:
    print(f'Saffron Gym pads (every room reachable from the entrance): {("rooms cut off: " + str(pads_bad)) if pads_bad else "fine"}')
from maprando.boulderroom import bad_boulder_room
from maprando.catalog import catalog_for
if 'CianwoodGym' in ALL_MAPS:
    # Cianwood's Strength puzzle, push by push: solvable, and no push shuts the player in
    from maprando.pushes import gym_puzzle
    _n = 'CianwoodGym'; _m = d.maps[_n]; _m2 = copy.deepcopy(_m)
    _a = sym[_n + '_Blocks']; _m2.blocks = bytes(rom[_a:_a + len(_m.blocks)])
    _bg, _nbg, _obj, _nobj = events_layout(rom, sym[_n + '_MapEvents'])
    for i, o in enumerate(_m2.objects): o.y, o.x = rom[_obj + i * OBJ + 1] - 4, rom[_obj + i * OBJ + 2] - 4
    _mv, _st, _tr = gym_puzzle(d, _m2, perms)
    print(f'CianwoodGym Strength puzzle: ' + ('NOT SOLVABLE' if _mv is None else f'{_mv[2]} submoves at fewest ({_mv[0]} boulder moves, {_mv[1]} submoves at fewest moves) to Chuck') + ('; every boulder touches another' if not GymGenerator.lone_boulders(GymGenerator(d, d.maps['CianwoodGym'], perms, {}).gate_cells(_m2)) else '; SOME BOULDER TOUCHES NO OTHER')
          + ('; traps not checked (too many positions)' if _st is None else
             f'; {_tr} of {_st} positions shut the player in' if _tr else '; no push shuts the player in'))
for _n in ('IcePathB1F', 'BlackthornGym2F'):
    if _n not in ALL_MAPS: continue
    _m = d.maps[_n]; _m2 = copy.deepcopy(_m)
    _a = sym[_n + '_Blocks']; _m2.blocks = bytes(rom[_a:_a + len(_m.blocks)])
    _bg, _nbg, _obj, _nobj = events_layout(rom, sym[_n + '_MapEvents'])
    for i, o in enumerate(_m2.objects): o.y, o.x = rom[_obj + i * OBJ + 1] - 4, rom[_obj + i * OBJ + 2] - 4
    _wa, _nw = warps_at(rom, sym[_n + '_MapEvents'])
    for i, w in enumerate(_m2.warps): w.y, w.x = rom[_wa + i * WARP], rom[_wa + i * WARP + 1]
    # the fewest boulder moves (runs of one boulder's pushes) that drop every boulder, in one go
    from maprando.pushes import drop_moves
    from maprando.boulderroom import stonetable
    _mm = build_model(d, _m2, perms); _W = _mm.w; _pairs = stonetable(_m)
    _ppl = {_mm.idx(o.x, o.y) for o in _m2.objects if 'BOULDER' not in o.sprite and _mm.inb(o.x, o.y)}
    _fl = {c for c in range(_W * _mm.h) if classify(_mm, _mm.coll[c]) == 'land' and c not in _mm.warp_cells and c not in _ppl}
    _own = {i: _mm.idx(_m2.warps[w].x, _m2.warps[w].y) for i, w in _pairs}
    _bl = {i: _mm.idx(o.x, o.y) for i, o in enumerate(_m2.objects) if 'BOULDER' in o.sprite}
    _st = [_mm.idx(w.x, w.y) for k, w in enumerate(_m2.warps) if k not in dict(_pairs).values()]
    _starts = [c for s_ in _st for c in (s_ - 1, s_ + 1, s_ - _W, s_ + _W) if c in _fl]
    _mv = drop_moves(_W, _fl, _bl, _own, _starts, boxonly=frozenset(c for c in _st if classify(_mm, _mm.coll[c]) == 'land'), limit=200000, detail=True)
    _sub = drop_moves(_W, _fl, _bl, _own, _starts, boxonly=frozenset(c for c in _st if classify(_mm, _mm.coll[c]) == 'land'), limit=200000, detail=True, weight=0)
    print(f'{_n} boulder puzzle (each boulder only into its own hole, all solvable, no push shuts the player in): {bad_boulder_room(d, _m, _m2, perms, catalog_for(d, _m, perms, {})) or "fine"}'
          + (f'; {_sub[1] if _sub else "?"} submoves at fewest ({_mv[0]} boulder moves, {_mv[1]} submoves at fewest moves)' if _mv is not None else '; boulder moves not counted (a reset needed or too many positions)'))
