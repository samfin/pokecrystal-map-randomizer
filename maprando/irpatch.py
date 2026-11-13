"""Events whose bytes the Item Randomizer rewrites (from its label/address lists).
Their coordinates may be overwritten after we run, so they must keep their vanilla spot."""
import glob, json, os
from .patch import load_sym, events_layout, WARP, COORD, BG, OBJ

def ir_ranges(ir_root):
    rs = []
    files = [os.path.join(ir_root, 'crystal-speedchoice-label-details.json')]
    files += glob.glob(os.path.join(ir_root, 'Config', '*details.json'))
    files += glob.glob(os.path.join(ir_root, 'Warp Data', '*details.json'))
    for f in files:
        try: data = json.load(open(f))
        except Exception: continue
        if isinstance(data, dict): data = list(data.values())
        for e in data:
            try: rs.append((e['address_range']['begin'], e['address_range']['end']))
            except (KeyError, TypeError): pass
    return rs

def patched_events(rom_path, sym_path, ir_root, names):
    """{map: {('bg'|'obj', index)}} for events whose coordinate bytes overlap an IR patch range."""
    if not (os.path.exists(rom_path) and os.path.exists(sym_path) and os.path.isdir(ir_root)): return {}
    rom = open(rom_path, 'rb').read(); sym = load_sym(sym_path); rs = sorted(ir_ranges(ir_root))
    def hit(a, b):
        return any(s < b and a < e for s, e in rs)
    out = {}
    for n in names:
        at = sym.get(n + '_MapEvents')
        if at is None: continue
        bg, nbg, obj, nobj = events_layout(rom, at)
        for i in range(nbg):
            if hit(bg + i * BG, bg + i * BG + 2): out.setdefault(n, set()).add(('bg', i))
        for i in range(nobj):
            if hit(obj + i * OBJ + 1, obj + i * OBJ + 3): out.setdefault(n, set()).add(('obj', i))
    return out


def mode_patch_files(ir_root, mode_path):
    """NewGamePatches of every modifier a mode file selects (paths relative to ir_root)."""
    import yaml
    mode = yaml.safe_load(open(mode_path, encoding='utf-8'))
    mods = []
    def walk(v):
        if isinstance(v, str) and v.endswith('.yml'): mods.append(v)
        elif isinstance(v, list):
            for x in v: walk(x)
        elif isinstance(v, dict):
            for x in v.values(): walk(x)
    walk(mode)
    files = []
    for m in mods:
        p = os.path.join(ir_root, m)
        if not os.path.exists(p): continue
        data = yaml.safe_load(open(p, encoding='utf-8')) or {}
        for f in data.get('NewGamePatches', []) or []:
            files.append(os.path.join(ir_root, f))
    return files

def ir_block_overrides(ir_root, mode_path, sym, disasm, names):
    """{map name: block bytes} for maps whose block data the mode's patches rewrite (e.g.
    'Remove cut tree in Ilex' replaces Ilex Forest's blocks). Generating from these keeps the
    layout consistent with the Item Randomizer's logic for that mode."""
    out = {}
    for f in mode_patch_files(ir_root, mode_path):
        try: entries = json.load(open(f, encoding='utf-8'))
        except Exception: continue
        if isinstance(entries, dict): entries = [entries]
        for e in entries:
            if not isinstance(e, dict) or 'address_range' not in e or 'integer_values' not in e: continue
            a, b = e['address_range']['begin'], e['address_range']['end']
            vals = e['integer_values']
            vals = [int(v) for v in (vals.split() if isinstance(vals, str) else vals['new'] if isinstance(vals, dict) else vals)]
            for n in names:
                s0 = sym.get(n + '_Blocks')
                if s0 is None: continue
                cur = bytearray(out.get(n, disasm.maps[n].blocks))
                s1 = s0 + len(cur)
                if a < s1 and b > s0:
                    for i in range(max(a, s0), min(b, s1)):
                        if 0 <= i - a < len(vals): cur[i - s0] = vals[i - a]
                    out[n] = bytes(cur)
    return {n: v for n, v in out.items() if v != disasm.maps[n].blocks}

def ir_event_overrides(ir_root, mode_path, sym, rom, names):
    """{map name: {('bg'|'obj', index): (x, y)}} for signs, hidden items and objects whose
    coordinates the mode's patches rewrite (e.g. a hidden item moved off a tile an NPC stands on).
    Applied to vanilla, they give the layout the Item Randomizer's logic expects."""
    writes = {}
    for f in mode_patch_files(ir_root, mode_path):
        try: entries = json.load(open(f, encoding='utf-8'))
        except Exception: continue
        if isinstance(entries, dict): entries = [entries]
        for e in entries:
            if not isinstance(e, dict) or 'address_range' not in e or 'integer_values' not in e: continue
            a = e['address_range']['begin']
            vals = e['integer_values']
            vals = [int(v) for v in (vals.split() if isinstance(vals, str) else vals['new'] if isinstance(vals, dict) else vals)]
            for i, v in enumerate(vals): writes[a + i] = v
    out = {}
    for n in names:
        at = sym.get(n + '_MapEvents')
        if at is None: continue
        bg, nbg, obj, nobj = events_layout(rom, at)
        for i in range(nbg):
            y, x = bg + i * BG, bg + i * BG + 1
            if y in writes or x in writes:
                out.setdefault(n, {})[('bg', i)] = (writes.get(x, rom[x]), writes.get(y, rom[y]))
        for i in range(nobj):
            y, x = obj + i * OBJ + 1, obj + i * OBJ + 2
            if y in writes or x in writes:
                out.setdefault(n, {})[('obj', i)] = (writes.get(x, rom[x]) - 4, writes.get(y, rom[y]) - 4)
    return out
