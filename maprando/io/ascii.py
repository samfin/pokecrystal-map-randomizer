"""Text view of a map model: one character per cell (for debugging)."""
from maprando.model.movement import classify

def render(mm, rows=None, marks=None):
    marks = marks or {}
    out = []
    for y in (rows or range(mm.h)):
        line = ''
        for x in range(mm.w):
            i = mm.idx(x, y); c = mm.coll[i]
            ch = {'land': '.', 'water': '~', 'forced': 'v', None: '#'}[classify(mm, c)]
            if (c & 0xF0) == 0xA0: ch = 'L'
            if (c & 0xF0) == 0xB0: ch = '|'
            if c == 0x18 or c == 0x14: ch = ','
            if i in mm.alt: ch = 'T' if ch == '#' else ch
            if c == 0x24: ch = 'O'
            if i in mm.objects: ch = {'wall': 'o', 'bits': 'b'}[mm.objects[i][0]]
            if i in mm.warp_cells: ch = 'W'
            ch = marks.get(i, ch)
            line += ch
        out.append(f'{y:3d} {line}')
    return '\n'.join(out)
