"""Write generated maps straight into an already-built romhack ROM.

Generated maps keep every size and count, so the ROM layout is identical to a full rebuild:
block data goes to <Map>_Blocks, coordinates go into the <Map>_MapEvents structure
(see macros/scripts/maps.asm). The global header checksum is recomputed like rgbfix does."""

WARP, COORD, BG, OBJ = 5, 8, 5, 13

def load_sym(path):
    sym = {}
    for line in open(path):
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

def patch_map(rom, sym, m2):
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

def fix_checksum(rom):
    s = (sum(rom) - rom[0x14E] - rom[0x14F]) & 0xFFFF
    rom[0x14E], rom[0x14F] = s >> 8, s & 0xFF

def base_matches_source(rom, sym, disasm, names):
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
