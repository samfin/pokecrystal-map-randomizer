"""Render a map with the real tileset graphics (approximate palettes) plus event markers."""
import io, os, re
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

# marker colours (render's markers), also described in LEGEND
WARP = (0, 230, 255)
ITEM = (255, 40, 40)
ROCK = (150, 150, 150)
BOULDER = (160, 100, 40)
TRAINER = (255, 140, 0)
PERSON = (60, 90, 255)
SIGN = (255, 230, 0)

LEGEND = """Markers on the map images:
  cyan square outline   warp (door, stairs, ladder, cave exit)
  red dot               item ball
  orange dot            trainer
  blue dot              other person or object
  grey dot              smashable rock
  brown dot             strength boulder
  small yellow square   sign or hidden item
Colours are approximate daytime colours.
"""


def tileset_art(disasm, tileset) -> tuple[bytes, list[str]]:
    """a tileset's tile graphics (2bpp) and the palette name of each tile, from the disassembly."""
    with open(disasm.path(disasm.tileset_gfx[tileset]), 'rb') as f:
        gfx = f.read()
    pals = []
    pm = disasm.path(disasm.tileset_palmap[tileset])
    if os.path.exists(pm):
        for line in open(pm):
            mm = re.match(r'\s*tilepal\s+\d+,(.*)', line)
            if mm: pals += [n.strip() for n in mm.group(1).split(';')[0].split(',') if n.strip()]
    return gfx, pals


class TileRenderer:
    """Draws maps of one tileset with its real tile graphics (approximate palettes). art is
    tileset_art's (graphics, palettes); without it they are read from the disassembly."""
    def __init__(self, disasm, tileset, art=None):
        self.meta = disasm.tileset_meta[tileset]
        self.gfx, self.pals = art if art is not None else tileset_art(disasm, tileset)
        self.tiles, self.blocks = {}, {}

    def tile(self, t):
        """8x8 image of tile number t (cached). Tiles $60-$7F are the font, drawn black."""
        if t in self.tiles: return self.tiles[t]
        idx = t if t < 0x60 else t - 0x80 + 0x60 if t >= 0x80 else None
        img = Image.new('RGB', (8, 8), (0, 0, 0))
        if idx is not None and (idx + 1) * 16 <= len(self.gfx):
            pal = PAL.get(self.pals[idx] if idx < len(self.pals) else 'GRAY', PAL['GRAY'])
            px = img.load()
            for r in range(8):
                lo, hi = self.gfx[idx*16 + r*2], self.gfx[idx*16 + r*2 + 1]
                for c in range(8):
                    px[c, r] = pal[((lo >> (7 - c)) & 1) | (((hi >> (7 - c)) & 1) << 1)]
        self.tiles[t] = img
        return img

    def block(self, b):
        """32x32 image of block b (cached)."""
        if b not in self.blocks:
            img = Image.new('RGB', (32, 32))
            if b * 16 + 16 <= len(self.meta):
                for i in range(16):
                    img.paste(self.tile(self.meta[b * 16 + i]), ((i % 4) * 8, (i // 4) * 8))
            self.blocks[b] = img
        return self.blocks[b]

    def render(self, m, scale=2, markers=True):
        """image of map m at the given scale; markers draws warps, events and objects on top."""
        W, H = m.width * 32, m.height * 32
        img = Image.new('RGB', (W, H))
        for by in range(m.height):
            for bx in range(m.width):
                img.paste(self.block(m.blocks[by * m.width + bx]), (bx * 32, by * 32))
        if markers:
            dr = ImageDraw.Draw(img)
            for w in m.warps:
                dr.rectangle([w.x*16, w.y*16, w.x*16+15, w.y*16+15], outline=WARP, width=2)
            for o in m.objects:
                x, y = o.x*16, o.y*16
                if o.obj_type == 'OBJECTTYPE_ITEMBALL': col = ITEM
                elif 'ROCK' in o.sprite: col = ROCK
                elif 'BOULDER' in o.sprite: col = BOULDER
                elif o.obj_type == 'OBJECTTYPE_TRAINER': col = TRAINER
                else: col = PERSON
                dr.ellipse([x+3, y+3, x+12, y+12], fill=col, outline=(0, 0, 0))
            for b in m.bg_events:
                x, y = b.x*16, b.y*16
                dr.rectangle([x+5, y+5, x+10, y+10], fill=SIGN, outline=(0, 0, 0))
        if scale != 1: img = img.resize((W * scale, H * scale), Image.NEAREST)
        return img

    def png(self, m, scale=1) -> bytes:
        """render's image of map m as PNG bytes."""
        buf = io.BytesIO()
        self.render(m, scale=scale).save(buf, 'PNG', optimize=True)
        return buf.getvalue()
