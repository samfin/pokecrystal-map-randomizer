"""Write generated maps into a copy of the disassembly: .blk files and event coordinates."""
import re

def rewrite_events(asm_path, name, m2):
    """rewrite the object and bg event coordinates in a map's .asm file to m2's."""
    src = open(asm_path, encoding='utf-8').read().split('\n')
    in_ev = False; oi = bi = 0
    for i, line in enumerate(src):
        if line.strip().startswith(name + '_MapEvents'): in_ev = True; continue
        if not in_ev: continue
        mo = re.match(r'(\s*object_event\s+)(-?\d+),(\s*)(-?\d+),(.*)', line)
        if mo:
            o = m2.objects[oi]; oi += 1
            src[i] = f'{mo.group(1)}{o.x:2d},{mo.group(3)}{o.y:2d},{mo.group(5)}'.replace(f'{o.x:2d},{mo.group(3)}', f'{o.x:2d},{mo.group(3)}')
            continue
        mb = re.match(r'(\s*bg_event\s+)(-?\d+),(\s*)(-?\d+),(.*)', line)
        if mb:
            b = m2.bg_events[bi]; bi += 1
            src[i] = f'{mb.group(1)}{b.x:2d},{mb.group(3)}{b.y:2d},{mb.group(5)}'
    assert oi == len(m2.objects) and bi == len(m2.bg_events), (name, oi, bi)
    open(asm_path, 'w', encoding='utf-8').write('\n'.join(src))

def emit(root, m2):
    """write a generated map into the disassembly at root: its .blk file and its event coordinates."""
    import os
    with open(os.path.join(root, m2.blk_path), 'wb') as f: f.write(m2.blocks)
    rewrite_events(os.path.join(root, 'maps', m2.name + '.asm'), m2.name, m2)
