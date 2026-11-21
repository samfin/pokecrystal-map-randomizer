// One Pyodide interpreter running maprando (maprando/web.py). Messages from app.js:
//   {type: 'init', pkg, bundle, rom, cursed} -> {type: 'ready', problems, groups, names, version}
//   {type: 'map', name, seed}          -> {type: 'map', name, data, report, seconds}
//   {type: 'finish', seed, rom, names, maps, reports, version} -> {type: 'rom', rom, log}
// Failures come back as {type: 'error', message}.
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.28.0/full/';
importScripts(PYODIDE + 'pyodide.js');

let py = null;

// a Python bytes object as a JS Uint8Array (a copy: the Python object may go away)
function bytesOf(proxy) {
  const buf = proxy.getBuffer('u8');
  try { return buf.data.slice(); } finally { buf.release(); proxy.destroy(); }
}

function jsOf(proxy) {
  try { return proxy.toJs(); } finally { proxy.destroy(); }
}

async function init(msg) {
  py = await loadPyodide({ indexURL: PYODIDE });
  py.unpackArchive(msg.pkg, 'zip', { extractDir: '/home/pyodide/app' });
  py.globals.set('bundle_js', msg.bundle);
  py.globals.set('rom_js', msg.rom);
  py.globals.set('cursed', !!msg.cursed);
  py.runPython(`
import sys
sys.path.insert(0, '/home/pyodide/app')
from maprando import web
bundle = bytes(bundle_js.to_py())
session = web.Session(bundle, bytes(rom_js.to_py()), cursed=cursed)
del bundle_js, rom_js
`);
  return {
    type: 'ready',
    problems: jsOf(py.runPython('session.problems')),
    groups: jsOf(py.runPython('web.map_groups()')),
    names: jsOf(py.runPython('session.map_names() if not session.problems else []')),
    version: py.runPython('session.version'),
  };
}

function randomize(msg) {
  const t = performance.now();
  py.globals.set('name', msg.name);
  py.globals.set('seed', msg.seed);
  py.runPython('data, report = session.randomize(name, seed)');
  return {
    type: 'map', name: msg.name,
    data: bytesOf(py.globals.get('data')),
    report: py.globals.get('report'),
    seconds: (performance.now() - t) / 1000,
  };
}

function finish(msg) {
  py.globals.set('seed', msg.seed);
  py.globals.set('version', msg.version || '');
  py.globals.set('names_js', msg.names);
  py.globals.set('maps_js', msg.maps);
  py.globals.set('reports_js', msg.reports);
  py.globals.set('rom_js', msg.rom);
  py.runPython(`
names = list(names_js.to_py())
maps = {n: bytes(b.to_py()) for n, b in zip(names, maps_js)}
reports = dict(zip(names, reports_js.to_py()))
out = web.finish(bundle, bytes(rom_js.to_py()), maps)
log = web.report(seed, version, reports, cursed=cursed)
del names_js, maps_js, reports_js, rom_js
`);
  return { type: 'rom', rom: bytesOf(py.globals.get('out')), log: py.globals.get('log') };
}

self.onmessage = async (e) => {
  const msg = e.data;
  try {
    let reply;
    if (msg.type === 'init') reply = await init(msg);
    else if (msg.type === 'map') reply = randomize(msg);
    else if (msg.type === 'finish') reply = finish(msg);
    else throw new Error('unknown message ' + msg.type);
    const transfer = reply.data ? [reply.data.buffer] : reply.rom ? [reply.rom.buffer] : [];
    self.postMessage(reply, transfer);
  } catch (err) {
    self.postMessage({ type: 'error', message: String(err && err.message || err) });
  }
};
