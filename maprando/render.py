"""Render a map with the real tileset graphics (approximate palettes) plus event markers."""
import os, re
from PIL import Image, ImageDraw

PAL = {  # light -> dark, rough GBC daytime colours
    'GRAY':   [(248, 248, 248), (176, 176, 176), (104, 104, 104), (24, 24, 24)],
    'RED':    [(248, 232, 216), (240, 128, 112), (176, 48, 32), (24, 24, 24)],
    'GREEN':  [(232, 248, 200), (144, 208, 96), (64, 136, 48), (24, 24, 24)],
    'WATER':  [(216, 232, 248), (104, 160, 240), (40, 80, 176), (24, 24, 24)],
    'YELLOW': [(248, 248, 200), (232, 208, 72), (160, 128, 32), (24, 24, 24)],
    'BROWN':  [(240, 224, 200), (192, 152, 104), (120, 88, 56), (24, 24, 24)],
    'ROOF':   [(248, 224, 216), (208, 136, 112), (136, 64, 48), (24, 24, 24)],
    'TEXT':   [(248, 248, 248), (176, 176, 176), (104, 104, 104), (24, 24, 24)],
}

class TileRenderer:
    def __init__(self, disasm, tileset):
        self.d = disasm
        self.meta = disasm.tileset_meta[tileset]
        with open(disasm.path(disasm.tileset_gfx[tileset]), 'rb') as f:
            self.gfx = f.read()
        self.pals = []
        pm = disasm.path(disasm.tileset_palmap[tileset])
        if os.path.exists(pm):
            for line in open(pm):
                mm = re.match(r'\s*tilepal\s+(\d+),(.*)', line)
                if mm:
                    bank = int(mm.group(1))
                    for name in mm.group(2).split(';')[0].split(','):
                        name = name.strip()
                        if name: self.pals.append((bank, name))
        self.cache = {}

    def tile(self, t):
        if t in self.cache: return self.cache[t]
        if t < 0x60: idx, palidx = t, t
        elif t >= 0x80: idx, palidx = t - 0x80 + 0x60, t - 0x80 + 0x60
        else: idx, palidx = None, None
        img = Image.new('RGB', (8, 8), (0, 0, 0))
        if idx is not None and (idx + 1) * 16 <= len(self.gfx):
            name = 'GRAY'
            if palidx < len(self.pals): name = self.pals[palidx][1]
            elif palidx - 0x60 >= 0 and len(self.pals) > 0x60 and palidx < len(self.pals): name = self.pals[palidx][1]
            pal = PAL.get(name, PAL['GRAY'])
            px = img.load()
            for r in range(8):
                lo, hi = self.gfx[idx*16 + r*2], self.gfx[idx*16 + r*2 + 1]
                for c in range(8):
                    v = ((lo >> (7 - c)) & 1) | (((hi >> (7 - c)) & 1) << 1)
                    px[c, r] = pal[v]
        self.cache[t] = img
        return img

    def render(self, m, scale=2, markers=True):
        W, H = m.width * 32, m.height * 32
        img = Image.new('RGB', (W, H))
        for by in range(m.height):
            for bx in range(m.width):
                b = m.blocks[by * m.width + bx]
                for ty in range(4):
                    for tx in range(4):
                        t = self.meta[b * 16 + ty * 4 + tx] if b * 16 + 16 <= len(self.meta) else 0
                        img.paste(self.tile(t), (bx * 32 + tx * 8, by * 32 + ty * 8))
        if markers:
            dr = ImageDraw.Draw(img)
            for w in m.warps:
                dr.rectangle([w.x*16, w.y*16, w.x*16+15, w.y*16+15], outline=(0, 230, 255), width=2)
            for o in m.objects:
                x, y = o.x*16, o.y*16
                if o.obj_type == 'OBJECTTYPE_ITEMBALL': col = (255, 40, 40)
                elif 'ROCK' in o.sprite: col = (150, 150, 150)
                elif 'BOULDER' in o.sprite: col = (160, 100, 40)
                elif o.obj_type == 'OBJECTTYPE_TRAINER': col = (255, 140, 0)
                else: col = (60, 90, 255)
                dr.ellipse([x+3, y+3, x+12, y+12], fill=col, outline=(0, 0, 0))
            for b in m.bg_events:
                x, y = b.x*16, b.y*16
                dr.rectangle([x+5, y+5, x+10, y+10], fill=(255, 230, 0), outline=(0, 0, 0))
        if scale != 1: img = img.resize((W * scale, H * scale), Image.NEAREST)
        return img
