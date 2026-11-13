"""Ledge jumps that land somewhere the player cannot stand.

The game jumps when the player stands on a ledge tile, faces one of its directions and the step that
way is blocked (engine/overworld/player_movement.asm .TryJump). It never checks the landing tile, so
a jump can put the player inside a wall, onto water without Surf, onto a person or off the map,
where they are stuck. The requirement model only follows jumps that land on free floor, so these
are checked separately."""
from .model import DIRS, LEDGE_DIRS, classify, side_walls
from .reach import Searcher, entry_starts

def bad_jumps(mm, everywhere=False):
    """[(from cell, direction, landing cell or None)] for jumps the player can make from a tile
    they can reach (any abilities) that land somewhere they cannot stand."""
    s = Searcher(mm)
    reach = set()
    for e in mm.anchors:
        if e.is_entry:
            reach |= set(s.search([(c, (1 << 40) - 1) for c, _ in entry_starts(mm, e)]))
    out = []
    # everywhere: every ledge tile, reachable or not (to find vanilla's decorative ones)
    for cell in (range(mm.w * mm.h) if everywhere else sorted(reach)):
        cA = mm.coll[cell]
        if (cA & 0xF0) != 0xA0: continue
        for d in LEDGE_DIRS[cA & 7]:
            if d in side_walls(cA): continue
            B = s.move(cell, d)
            if B is None:
                blocked = True
            else:
                obj = mm.objects.get(B)
                blocked = (obj is not None and obj[0] == 'wall') or classify(mm, mm.coll[B]) != 'land' \
                          or {'U': 'D', 'D': 'U', 'L': 'R', 'R': 'L'}[d] in side_walls(mm.coll[B])
            if not blocked: continue
            C = s.move(B, d) if B is not None else None
            ok = C is not None and classify(mm, mm.coll[C]) == 'land' and mm.objects.get(C, ('',))[0] != 'wall'
            if not ok: out.append((cell, d, C))
    return out

def ledge_cells(mm):
    """cells in front of and beyond every ledge tile, in each of its directions: kept as in vanilla,
    a ledge jumps exactly when and where it does there."""
    out = set()
    for c in range(mm.w * mm.h):
        cA = mm.coll[c]
        if (cA & 0xF0) != 0xA0: continue
        x, y = c % mm.w, c // mm.w
        for d in LEDGE_DIRS[cA & 7]:
            dx, dy = DIRS[d]
            for k in (1, 2):
                if mm.inb(x + k * dx, y + k * dy): out.add(mm.idx(x + k * dx, y + k * dy))
    return out
