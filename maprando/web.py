"""Entry points for the browser app (web/): bytes in, bytes out, no files.

The page runs maprando in Pyodide web workers. Each worker opens a Session on the data bundle and
the player's ROM, then randomizes the maps it is handed; finish() then writes every new map into
the ROM. The ROM is a finished one (speedchoice, then zxplus and the Item Randomizer): what the
Item Randomizer changed in the maps is read from the ROM itself, so the layouts match its logic.

tools/build_web.py makes the bundle (make_bundle) from the disassembly."""
from __future__ import annotations
import gzip
import pickle
from maprando.checks.ship import patch_spins
from maprando.io.rom import fix_checksum, patch_map, rom_overrides, rom_problems
from maprando.maps import ALL_MAPS, CAVES, GYMS, ICE, KNOWN_UNSUPPORTED, OTHERS, ROUTES
from maprando.randomizer import Randomizer, map_order

BUNDLE_FORMAT = 1


def make_bundle(disasm, sym: dict[str, int], version: str = '') -> bytes:
    """the data the page needs: the parsed disassembly and the map symbols of its ROM build."""
    disasm.root = ''
    for m in disasm.maps.values():
        m.asm_path = m.blk_path = ''
    if hasattr(disasm, 'cache_path'): del disasm.cache_path
    sym = {k: v for k, v in sym.items() if k.endswith(('_Blocks', '_MapEvents'))}
    data = {'format': BUNDLE_FORMAT, 'disasm': disasm, 'sym': sym, 'version': version}
    return gzip.compress(pickle.dumps(data, protocol=4), mtime=0)


def load_bundle(data: bytes):
    """(disassembly, symbols, version) from make_bundle's bytes."""
    b = pickle.loads(gzip.decompress(bytes(data)))
    if b.get('format') != BUNDLE_FORMAT: raise ValueError('data bundle from another version')
    return b['disasm'], b['sym'], b['version']


def map_groups() -> list[tuple[str, list[str]]]:
    """the maps by kind, as the page lays them out."""
    return [('Caves and towers', CAVES), ('Routes', ROUTES), ('Ice Path', ICE),
            ('Other areas', OTHERS), ('Gyms', GYMS)]


class Session:
    """One worker's randomizer for one ROM. problems lists why the ROM cannot be used (then
    nothing else works). mode: 'normal', 'cursed' or 'impossible' (Randomizer)."""

    def __init__(self, bundle: bytes, rom: bytes, mode: str = 'normal'):
        self.disasm, self.sym, self.version = load_bundle(bundle)
        rom = bytes(rom)
        self.problems = rom_problems(rom, self.sym, self.disasm, ALL_MAPS)
        if len(rom) != 0x200000: self.problems.insert(0, 'not a 2 MB Pokemon Crystal ROM')
        if self.problems: return
        self.randomizer = Randomizer(self.disasm, rom_overrides(rom, self.sym, self.disasm, ALL_MAPS),
                                     mode=mode)

    def map_names(self) -> list[str]:
        """every map to randomize, slowest first (hand them out in this order)."""
        return map_order(ALL_MAPS)

    def randomize(self, name: str, seed: str) -> tuple[bytes, str]:
        """(the new map, pickled, or b'' if it stays vanilla; its one-line report)."""
        m2, report = self.randomizer.randomize_map(name, seed)
        return (pickle.dumps(m2, protocol=4) if m2 is not None else b''), report


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


MODE_NOTES = {'cursed': 'routes drawn with every block of their tileset',
              'impossible': 'route walls drawn with block 0, open ground with the block that looks like it'}


def report(seed: str, version: str, reports: dict[str, str], mode: str = 'normal') -> str:
    """the text log the page offers next to the ROM."""
    lines = ['Crystal map randomizer', f'version: {version}', f'seed: {seed}']
    if mode in MODE_NOTES: lines.append(f'{mode} mode: {MODE_NOTES[mode]}')
    lines.append('')
    for n in ALL_MAPS:
        r = reports.get(n) or (f'vanilla ({KNOWN_UNSUPPORTED[n]})' if n in KNOWN_UNSUPPORTED else 'vanilla (not run)')
        lines.append(f'{n}: {r}')
    k = sum(1 for n in ALL_MAPS if reports.get(n, '').startswith('randomized'))
    lines += ['', f'{k}/{len(ALL_MAPS)} maps randomized']
    return '\n'.join(lines) + '\n'
