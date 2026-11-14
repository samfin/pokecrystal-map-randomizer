#!/usr/bin/env python3
"""Build the web app's data: web/data/bundle.pkl.gz (the parsed disassembly and its ROM symbols)
and web/data/maprando.zip (the Python package).

    python3 tools/build_web.py [--src ../pokecrystal-speedchoice2]   # both; commit the bundle
    python3 tools/build_web.py --package-only                          # the package (GitHub Actions)
    cd web && python3 -m http.server                                   # try it at http://localhost:8000

The bundle needs the built romhack, so it is made here and committed; rebuild it when the romhack
or the Disasm classes change. The package is made from the repository on every deploy."""
import argparse, os, subprocess, sys, zipfile
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from maprando.io.disasm import Disasm
from maprando.io.rom import load_sym
from maprando.web import make_bundle


def git_rev(path):
    """the short commit of the git repository at path ('' if none)."""
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=path, capture_output=True,
                              text=True).stdout.strip()
    except OSError:
        return ''


def version():
    """this repository's commit, with -dirty for uncommitted changes."""
    try:
        return subprocess.run(['git', 'describe', '--always', '--dirty'], cwd=ROOT, capture_output=True,
                              text=True).stdout.strip()
    except OSError:
        return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=os.path.join(ROOT, '..', 'pokecrystal-speedchoice2'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'web', 'data'))
    ap.add_argument('--package-only', action='store_true', help='only the Python package (no disassembly needed)')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    v = version()
    if not a.package_only:
        src_v = git_rev(a.src)
        bundle = make_bundle(Disasm(a.src), load_sym(os.path.join(a.src, 'crystal-speedchoice.sym')),
                             f'speedchoice {src_v}' if src_v else '')
        with open(os.path.join(a.out, 'bundle.pkl.gz'), 'wb') as f: f.write(bundle)
    pkg = os.path.join(ROOT, 'maprando')
    with zipfile.ZipFile(os.path.join(a.out, 'maprando.zip'), 'w', zipfile.ZIP_DEFLATED) as z:
        for dp, dns, fns in os.walk(pkg):
            dns[:] = sorted(d for d in dns if d != '__pycache__')
            for fn in sorted(fns):
                if fn.endswith(('.py', '.json')):
                    full = os.path.join(dp, fn)
                    z.write(full, os.path.relpath(full, ROOT))
    with open(os.path.join(a.out, 'version.txt'), 'w') as f: f.write(v + '\n')
    for fn in ('bundle.pkl.gz', 'maprando.zip'):
        if os.path.exists(os.path.join(a.out, fn)):
            print(f'{fn}: {os.path.getsize(os.path.join(a.out, fn)) // 1024} KB')


if __name__ == '__main__':
    main()
