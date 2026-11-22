"""Entry points for the browser app (web/): bytes in, bytes out, no files.

The page runs maprando in Pyodide web workers. Each worker opens a Session on the data bundle and
the player's ROM, then randomizes the maps it is handed; finish() then writes every new map into
the ROM, and images_zip() packs a picture of each new map. The ROM is a finished one (speedchoice, then zxplus and the Item Randomizer): what the
Item Randomizer changed in the maps is read from the ROM itself, so the layouts match its logic.

tools/build_web.py makes the bundle (make_bundle) from the disassembly."""
from __future__ import annotations
import gzip
import io
import pickle
import zipfile
from maprando.checks.ship import patch_spins
from maprando.io.rom import fix_checksum, patch_map, rom_overrides, rom_problems
from maprando.maps import ALL_MAPS, CAVES, GYMS, ICE, KNOWN_UNSUPPORTED, OTHERS, ROUTES
from maprando.randomizer import Randomizer, map_order

BUNDLE_FORMAT = 2


def make_bundle(disasm, sym: dict[str, int], version: str = '') -> bytes:
    """the data the page needs: the parsed disassembly, the map symbols of its ROM build and the
    tile graphics of the maps' tilesets (for images_zip)."""
    from maprando.io.render import tileset_art
    art = {ts: tileset_art(disasm, ts) for ts in sorted({disasm.maps[n].tileset for n in ALL_MAPS})}
    disasm.root = ''
    for m in disasm.maps.values():
        m.asm_path = m.blk_path = ''
    if hasattr(disasm, 'cache_path'): del disasm.cache_path
    sym = {k: v for k, v in sym.items() if k.endswith(('_Blocks', '_MapEvents'))}
    data = {'format': BUNDLE_FORMAT, 'disasm': disasm, 'sym': sym, 'art': art, 'version': version}
    return gzip.compress(pickle.dumps(data, protocol=4), mtime=0)


def _open_bundle(data: bytes) -> dict:
    b = pickle.loads(gzip.decompress(bytes(data)))
    if b.get('format') != BUNDLE_FORMAT: raise ValueError('data bundle from another version')
    return b


def load_bundle(data: bytes):
    """(disassembly, symbols, version) from make_bundle's bytes."""
    b = _open_bundle(data)
    return b['disasm'], b['sym'], b['version']


def map_groups() -> list[tuple[str, list[str]]]:
    """the maps by kind, as the page lays them out."""
    return [('Caves and towers', CAVES), ('Routes', ROUTES), ('Ice Path', ICE),
            ('Other areas', OTHERS), ('Gyms', GYMS)]


class Session:
    """One worker's randomizer for one ROM. problems lists why the ROM cannot be used (then
    nothing else works). cursed: routes drawn with every block of their tileset."""

    def __init__(self, bundle: bytes, rom: bytes, cursed: bool = False):
        b = _open_bundle(bundle)
        self.disasm, self.sym, self.version, self.art = b['disasm'], b['sym'], b['version'], b['art']
        self.renderers = {}
        rom = bytes(rom)
        self.problems = rom_problems(rom, self.sym, self.disasm, ALL_MAPS)
        if len(rom) != 0x200000: self.problems.insert(0, 'not a 2 MB Pokemon Crystal ROM')
        if self.problems: return
        self.randomizer = Randomizer(self.disasm, rom_overrides(rom, self.sym, self.disasm, ALL_MAPS),
                                     cursed=cursed)

    def map_names(self) -> list[str]:
        """every map to randomize, slowest first (hand them out in this order)."""
        return map_order(ALL_MAPS)

    def randomize(self, name: str, seed: str) -> tuple[bytes, str]:
        """(the new map, pickled, or b'' if it stays vanilla; its one-line report)."""
        m2, report = self.randomizer.randomize_map(name, seed)
        return (pickle.dumps(m2, protocol=4) if m2 is not None else b''), report

    def picture(self, data: bytes) -> bytes:
        """a PNG of a new map (randomize's pickled map) with its warps, objects and signs marked.
        Needs Pillow (the worker loads it)."""
        from maprando.io.render import TileRenderer
        m2 = pickle.loads(bytes(data))
        if m2.tileset not in self.renderers:
            self.renderers[m2.tileset] = TileRenderer(self.disasm, m2.tileset, self.art[m2.tileset])
        return self.renderers[m2.tileset].png(m2)


def finish(bundle: bytes, rom: bytes, results: dict[str, bytes]) -> bytes:
    """the ROM with every new map written in (results: Session.randomize's pickled maps by name),
    S.S. Aqua's trainers spinning and the checksum fixed."""
    disasm, sym, _ = load_bundle(bundle)
    out = bytearray(rom)
    for name in ALL_MAPS:
        data = results.get(name)
        if data: patch_map(out, sym, pickle.loads(bytes(data)))
    patch_spins(out, sym, disasm)
    fix_checksum(out)
    return bytes(out)


def images_zip(seed: str, images: dict[str, bytes], cursed: bool = False) -> bytes:
    """a zip of Session.picture's PNGs by map name, with a README of what the markers mean."""
    from maprando.io.render import LEGEND
    buf = io.BytesIO()
    stamp = (2000, 1, 1, 0, 0, 0)    # fixed, so a seed always gives the same zip
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as z:   # PNGs are compressed already
        mode = ', cursed mode' if cursed else ''
        z.writestr(zipfile.ZipInfo('README.txt', stamp), f'Crystal map randomizer, seed {seed}{mode}\n'
                   f'{len(images)} maps with new layouts, one image each.\n\n{LEGEND}')
        for n in ALL_MAPS:
            if images.get(n): z.writestr(zipfile.ZipInfo(f'{n}.png', stamp), bytes(images[n]))
    return buf.getvalue()


def report(seed: str, version: str, reports: dict[str, str], cursed: bool = False) -> str:
    """the text log the page offers next to the ROM."""
    lines = ['Crystal map randomizer', f'version: {version}', f'seed: {seed}']
    if cursed: lines.append('cursed mode: routes drawn with every block of their tileset')
    lines.append('')
    for n in ALL_MAPS:
        r = reports.get(n) or (f'vanilla ({KNOWN_UNSUPPORTED[n]})' if n in KNOWN_UNSUPPORTED else 'vanilla (not run)')
        lines.append(f'{n}: {r}')
    k = sum(1 for n in ALL_MAPS if reports.get(n, '').startswith('randomized'))
    lines += ['', f'{k}/{len(ALL_MAPS)} maps randomized']
    return '\n'.join(lines) + '\n'
