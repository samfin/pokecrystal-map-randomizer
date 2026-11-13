"""Errors and constants shared by the cave generator modules."""

NORM = {',': '.', 'H': '#', 'T': '#'}   # plan view of grass / headbutt / cut-tree cells

D4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]

LEDGE_PATTERNS = {(0, 1): ('LD', 'LD', '#', '#'), (1, 0): ('LR', '#', 'LR', '#'), (-1, 0): ('#', 'LL', '#', 'LL')}

LEDGE_CELLS = {(0, 1): (0, 1), (1, 0): (0, 2), (-1, 0): (1, 3)}   # quadrant indices holding ledge cells

class GenError(Exception):
    """this try (or, from generate, every try) found no valid layout."""

class UnifyError(GenError):
    """zone z ended up in pieces the corridors could not join."""
    def __init__(self, z):
        super().__init__('cannot unify zone'); self.z = z

class SignError(GenError):
    """moving the signs found nothing in its share of the tries: generate with them in place."""

class MergeError(GenError):
    """merging ledge steps found nothing in its share of the tries: generate without it."""

class SplitError(GenError):
    """the ledge-loop split found nothing in its share of the tries: generate without it."""

class NookError(GenError):
    """no dead end could be made for a hidden item (b: its block)."""
    def __init__(self, b, msg):
        super().__init__(msg); self.b = b

class WarpRoomError(GenError):
    """a moving warp block found no room (blk: the block); the retry keeps it in place."""
    def __init__(self, blk, msg):
        super().__init__(msg); self.blk = blk

class PairError(GenError):
    """the gate between a pair of zones (p) could not be built."""
    def __init__(self, p, msg):
        super().__init__(msg); self.p = p

class PortConflict(GenError):
    """two zones need the same doorway of locked block blk; the retry locks its neighbour too."""
    def __init__(self, blk):
        super().__init__(f'port conflict {blk}'); self.blk = blk
