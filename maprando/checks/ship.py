"""S.S. Aqua: every trainer spins, so none can force a battle.

On the ship a battle the player cannot win is a softlock. A trainer that spins turns at random, so
the player can always wait until it looks away and walk past. Standing trainers on the ship's maps
become SPINRANDOM_SLOW; those that already spin are left as they are. The movement byte is set in
the disasm before generation (so FastShipB1F is generated and checked with spinning trainers) and
written into the ROM again after the Item Randomizer, which may rewrite trainer records."""
from maprando.io.rom import events_layout, OBJ

SHIP_MAPS = ['FastShip1F', 'FastShipB1F', 'FastShipCabins_NNW_NNE_NE', 'FastShipCabins_SW_SSW_NW',
             'FastShipCabins_SE_SSE_CaptainsCabin']
SPIN, SPIN_BYTE = 'SPRITEMOVEDATA_SPINRANDOM_SLOW', 0x03
SPINNERS = {0x03, 0x0a, 0x1e, 0x1f}     # SPINRANDOM_SLOW/FAST, SPINCOUNTERCLOCKWISE/CLOCKWISE

def standing_trainers(d):
    """[(map, object index)] of ship trainers that do not spin in vanilla."""
    return [(n, i) for n in SHIP_MAPS if n in d.maps for i, o in enumerate(d.maps[n].objects)
            if o.obj_type == 'OBJECTTYPE_TRAINER' and 'SPIN' not in o.movement]

def spin_trainers(d):
    """make the ship's standing trainers spin in the disassembly (before generating)."""
    for n, i in standing_trainers(d): d.maps[n].objects[i].movement = SPIN

def patch_spins(rom, sym, d):
    """write the spin into the ROM; returns how many movement bytes changed."""
    k = 0
    for n, i in standing_trainers(d):
        bg, nbg, obj, nobj = events_layout(rom, sym[n + '_MapEvents'])
        a = obj + i * OBJ + 3
        if rom[a] != SPIN_BYTE: rom[a] = SPIN_BYTE; k += 1
    return k

def not_spinning(rom, sym, d):
    """ship trainers whose movement byte in a finished ROM is not a spinner."""
    out = []
    for n in SHIP_MAPS:
        bg, nbg, obj, nobj = events_layout(rom, sym[n + '_MapEvents'])
        for i, o in enumerate(d.maps[n].objects):
            if o.obj_type == 'OBJECTTYPE_TRAINER' and rom[obj + i * OBJ + 3] not in SPINNERS: out.append(f'{n}:{o.const}')
    return out
