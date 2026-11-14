"""Write generated maps straight into an already-built romhack ROM.

Generated maps keep every size and count, so the ROM layout is identical to a full rebuild:
block data goes to <Map>_Blocks, coordinates go into the <Map>_MapEvents structure
(see macros/scripts/maps.asm). The global header checksum is recomputed like rgbfix does."""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from maprando.io.disasm import Disasm, MapInfo

WARP, COORD, BG, OBJ = 5, 8, 5, 13

def load_sym(path: str) -> dict[str, int]:
    """the symbol file at path (from the romhack build): {label: ROM offset}."""
    with open(path) as f: return parse_sym(f.read())

def parse_sym(text: str) -> dict[str, int]:
    """a symbol file's text: {label: ROM offset}."""
    sym = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) != 2 or ':' not in p[0]: continue
        bank, addr = (int(x, 16) for x in p[0].split(':'))
        sym[p[1]] = bank * 0x4000 + (addr - 0x4000) if bank else addr
    return sym

def events_layout(rom, at):
    """offsets of the bg_event and object_event arrays inside a MapEvents structure."""
    p = at + 2                          # filler
    n = rom[p]; p += 1 + n * WARP
    n = rom[p]; p += 1 + n * COORD
    nbg = rom[p]; bg = p + 1; p = bg + nbg * BG
    nobj = rom[p]; obj = p + 1
    return bg, nbg, obj, nobj

def warps_at(rom, at):
    """offset and count of the warp_event array inside a MapEvents structure."""
    return at + 3, rom[at + 2]

def patch_map(rom: bytearray, sym: dict[str, int], m2: MapInfo) -> None:
    """write a generated map into a built ROM: its blocks, warp positions (and in-map warp
    destinations), and event and object positions. The ROM must be built from the same source
    (same event counts)."""
    a = sym[m2.name + '_Blocks']
    rom[a:a + len(m2.blocks)] = m2.blocks
    wa, nw = warps_at(rom, sym[m2.name + '_MapEvents'])
    if nw != len(m2.warps): raise ValueError(f'{m2.name}: warp count in ROM does not match the source')
    for i, w in enumerate(m2.warps):
        rom[wa + i * WARP] = w.y; rom[wa + i * WARP + 1] = w.x
        # where a warp within the map leads (Saffron Gym's pads are re-paired)
        if w.dest_map == m2.const: rom[wa + i * WARP + 2] = int(w.dest_idx)
    bg, nbg, obj, nobj = events_layout(rom, sym[m2.name + '_MapEvents'])
    if nbg != len(m2.bg_events) or nobj != len(m2.objects):
        raise ValueError(f'{m2.name}: event counts in ROM do not match the source (rebuild the base ROM)')
    for i, b in enumerate(m2.bg_events):
        rom[bg + i * BG] = b.y; rom[bg + i * BG + 1] = b.x
    for i, o in enumerate(m2.objects):
        rom[obj + i * OBJ + 1] = (o.y + 4) & 0xFF; rom[obj + i * OBJ + 2] = (o.x + 4) & 0xFF

def fix_checksum(rom: bytearray) -> None:
    """recompute the cartridge header's global checksum."""
    s = (sum(rom) - rom[0x14E] - rom[0x14F]) & 0xFFFF
    rom[0x14E], rom[0x14F] = s >> 8, s & 0xFF

def base_matches_source(rom: bytes, sym: dict[str, int], disasm: Disasm,
                        names: list[str]) -> list[str]:
    """the base ROM must have been built from the current source: compare vanilla map data."""
    bad = []
    for n in names:
        m = disasm.maps[n]
        a = sym.get(n + '_Blocks')
        if a is None or bytes(rom[a:a + len(m.blocks)]) != m.blocks: bad.append(n); continue
        bg, nbg, obj, nobj = events_layout(rom, sym[n + '_MapEvents'])
        if nobj != len(m.objects) or nbg != len(m.bg_events): bad.append(n); continue
        if any(rom[obj + i * OBJ + 2] != (o.x + 4) & 0xFF or rom[obj + i * OBJ + 1] != (o.y + 4) & 0xFF
               for i, o in enumerate(m.objects)):
            bad.append(n)
    return bad


def rom_problems(rom: bytes, sym: dict[str, int], disasm: Disasm, names: list[str]) -> list[str]:
    """why a finished ROM (speedchoice, then zxplus and the Item Randomizer) cannot take new
    layouts: not built from this source (event counts differ) or its maps already randomized
    (warps moved). Empty if it is fine."""
    out = []
    for n in names:
        m = disasm.maps[n]
        a, ev = sym.get(n + '_Blocks'), sym.get(n + '_MapEvents')
        if a is None or ev is None or a + len(m.blocks) > len(rom) or ev + 3 > len(rom):
            out.append(f'{n}: not in this ROM'); continue
        bg, nbg, obj, nobj = events_layout(rom, ev)
        wa, nw = warps_at(rom, ev)
        if (nw, nbg, nobj) != (len(m.warps), len(m.bg_events), len(m.objects)):
            out.append(f'{n}: events differ from the speedchoice source'); continue
        if any((rom[wa + i * WARP + 1], rom[wa + i * WARP]) != (w.x, w.y) for i, w in enumerate(m.warps)):
            out.append(f'{n}: warps already moved (maps randomized before?)')
    return out


def rom_overrides(rom: bytes, sym: dict[str, int], disasm: Disasm, names: list[str]) -> dict:
    """what a finished ROM changed in the maps' vanilla data (the Item Randomizer's map patches),
    in Randomizer's overrides format: {name: blocks} for changed block data, and '__events__':
    {name: {('bg'|'obj', index): (x, y)}} for moved signs, hidden items and objects."""
    out, events = {}, {}
    for n in names:
        m = disasm.maps[n]
        a = sym[n + '_Blocks']
        blocks = bytes(rom[a:a + len(m.blocks)])
        if blocks != m.blocks: out[n] = blocks
        bg, nbg, obj, nobj = events_layout(rom, sym[n + '_MapEvents'])
        for i, b in enumerate(m.bg_events):
            x, y = rom[bg + i * BG + 1], rom[bg + i * BG]
            if (x, y) != (b.x, b.y): events.setdefault(n, {})[('bg', i)] = (x, y)
        for i, o in enumerate(m.objects):
            x, y = rom[obj + i * OBJ + 2] - 4, rom[obj + i * OBJ + 1] - 4
            if (x & 0xFF, y & 0xFF) != (o.x & 0xFF, o.y & 0xFF): events.setdefault(n, {})[('obj', i)] = (x, y)
    if events: out['__events__'] = events
    return out

