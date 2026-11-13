"""Scripted scenes: paths that objects and the player walk in applymovement scripts.

Scripts are written for the vanilla map. A scripted walk that runs into a wall or another object
never finishes and the game hangs, so these paths must stay as walkable as in vanilla."""
import re

STEP_LEN = {'step': 1, 'slow_step': 1, 'big_step': 1, 'slide_step': 1, 'slow_slide_step': 1,
            'fast_slide_step': 1, 'jump_step': 2, 'slow_jump_step': 2, 'fast_jump_step': 2}
DIR_XY = {'UP': (0, -1), 'DOWN': (0, 1), 'LEFT': (-1, 0), 'RIGHT': (1, 0)}

def _labels(lines):
    out = {}
    for i, l in enumerate(lines):
        mo = re.match(r'^(\w+):', l)
        if mo: out[mo.group(1)] = i
    return out

def _body(lines, labels, label):
    i = labels.get(label)
    if i is None: return []
    out = []
    for l in lines[i + 1:]:
        if re.match(r'^\w+:', l): break
        out.append(l.split(';')[0].strip())
    return out

def _closure(lines, labels, label, seen=None):
    """the body of a script label and of every global label it jumps or calls into
    (scall, sjump, branches, a trainer's after-battle script), in order."""
    seen = set() if seen is None else seen
    if label in seen or label not in labels: return []
    seen.add(label); out = []
    for l in _body(lines, labels, label):
        out.append(l)
        mo = re.match(r'(scall|sjump|jump|farscall|farsjump|if\w+|trainer|checkevent)\b(.*)', l)
        if not mo or mo.group(1) == 'checkevent': continue
        for tok in re.findall(r'\b([A-Za-z_]\w*)\b', mo.group(2)):
            if tok in labels and tok != label: out += _closure(lines, labels, tok, seen)
    return out

def _walk(lines, labels, mlabel, start):
    x, y = start; path = [(x, y)]
    for ml in _body(lines, labels, mlabel):
        mo = re.match(r'(\w+)\s+(UP|DOWN|LEFT|RIGHT)\b', ml)
        if mo and mo.group(1) in STEP_LEN:
            dx, dy = DIR_XY[mo.group(2)]
            for _ in range(STEP_LEN[mo.group(1)]):
                x += dx; y += dy; path.append((x, y))
        if ml.startswith('step_end') or ml.startswith('step_resume'): break
    return path

def scripted_paths(m, src):
    """[(mover const or 'PLAYER', [(x, y), ...], margin)] for map m whose script source is src.
    Objects start where moveobject put them (earlier in the same script) or at their own spot;
    the player starts at the trigger (coord event tile or the object talked to), so its paths get
    a one-block margin."""
    lines = src.splitlines(); labels = _labels(lines)
    objs = {o.const: o for o in m.objects if o.const}
    out = []
    last_move = {}
    for l in lines:
        l = l.split(';')[0].strip()
        mo = re.match(r'moveobject\s+(\w+),\s*(\d+),\s*(\d+)', l)
        if mo: last_move[mo.group(1)] = (int(mo.group(2)), int(mo.group(3))); continue
        if re.match(r'^\w+:', l): last_move = {}
        mo = re.match(r'applymovement\s+(\w+),\s*(\w+)', l)
        if not mo or mo.group(1) == 'PLAYER': continue
        name = mo.group(1)
        start = last_move.get(name) or ((objs[name].x, objs[name].y) if name in objs else None)
        if start is None: continue
        out.append((name, _walk(lines, labels, mo.group(2), start), 0))
    triggers = [(e.x, e.y, e.script) for e in m.coord_events] + [(o.x, o.y, o.script) for o in m.objects]
    for tx, ty, label in triggers:
        pos = (tx, ty)
        for l in _closure(lines, labels, label):
            mo = re.match(r'applymovement\s+PLAYER,\s*(\w+)', l)
            if not mo: continue
            # from the trigger, and from where the previous walk ended (walks in a row)
            for st in {(tx, ty), pos}:
                out.append(('PLAYER', _walk(lines, labels, mo.group(1), st), 1))
            pos = out[-1][1][-1]
    return out

def scene_starters(m, src):
    """objects whose own script moves objects or the player: their spot matters to the scene."""
    lines = src.splitlines(); labels = _labels(lines)
    out = []
    for o in m.objects:
        body = _closure(lines, labels, o.script)
        if any(l.startswith('moveobject') for l in body): out.append(o); continue
        # only walks that go somewhere: spinning in place (Fuchsia Gym's ninjas) stages nothing
        for l in body:
            mo = re.match(r'applymovement\s+(\w+),\s*(\w+)', l)
            if mo and len(set(_walk(lines, labels, mo.group(2), (0, 0)))) > 1: out.append(o); break
    return out

def blocked_walks(mv, m, mm2, m2, src):
    """scripted walks that are open in vanilla but blocked in the new layout (a tile became
    impassable, or another object stands on the path)."""
    from maprando.model.movement import classify
    bad = []
    others = {(o.x, o.y): o.const for o in m2.objects}
    for name, path, margin in scripted_paths(m, src):
        if name == 'PLAYER': continue        # its exact start depends on how the scene was entered
        for (x, y) in path[1:]:
            if not mv.inb(x, y): continue
            v_open = classify(mv, mv.coll[mv.idx(x, y)]) is not None
            g_open = classify(mm2, mm2.coll[mm2.idx(x, y)]) is not None
            if v_open and not g_open: bad.append((name, (x, y), 'wall')); break
            if others.get((x, y)) not in (None, name) and (x, y) not in {(o.x, o.y) for o in m.objects}:
                bad.append((name, (x, y), others[(x, y)])); break
    return bad


def gate_triggers(m, src):
    """coord events whose scene moves the player (Route 32's Cooltrainer walks you back to Violet
    without the Zephyr Badge; Burned Tower's rival battle): stepping there is a gate, so no way may
    lead round the trigger that vanilla does not have"""
    lines = src.splitlines(); labels = _labels(lines)
    return [e for e in m.coord_events
            if any(re.match(r'applymovement\s+PLAYER\b', l) for l in _closure(lines, labels, e.script))]
