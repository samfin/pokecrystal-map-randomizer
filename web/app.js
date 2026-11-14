// The page: reads the ROM, starts a few Pyodide workers (worker.js), hands each one map at a
// time (slowest first), paints the board as maps finish, then has one worker write the ROM.
(() => {
  const $ = (id) => document.getElementById(id);
  const WORKERS = Math.max(1, Math.min((navigator.hardwareConcurrency || 2) - 1, 6));

  let rom = null, romName = '';
  let data = null;               // {pkg, bundle} fetched once
  let downloads = [];            // object URLs to revoke on the next run

  function newSeed() {
    const a = new Uint32Array(2); crypto.getRandomValues(a);
    return (a[0].toString(36) + a[1].toString(36)).slice(0, 10);
  }
  $('seed').value = newSeed();
  $('new-seed').addEventListener('click', () => { $('seed').value = newSeed(); });

  function showError(text) { $('error').textContent = text; $('error').hidden = false; }
  function clearError() { $('error').hidden = true; }

  async function loadFile(file) {
    clearError();
    if (!file) return;
    rom = new Uint8Array(await file.arrayBuffer());
    romName = file.name.replace(/\.(gbc|gb)$/i, '');
    $('rom-name').textContent = file.name;
    $('rom-hint').textContent = `${(rom.length / 1048576).toFixed(1)} MB. Choose another file to replace it.`;
    $('rom').closest('.drop').classList.add('ready');
    $('go').disabled = false;
  }
  $('rom').addEventListener('change', (e) => loadFile(e.target.files[0]));
  const drop = $('rom').closest('.drop');
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); loadFile(e.dataTransfer.files[0]); });

  async function fetchData() {
    if (data) return data;
    const get = async (url) => {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`Could not load ${url} (${r.status}). Run tools/build_web.py first.`);
      return new Uint8Array(await r.arrayBuffer());
    };
    const [pkg, bundle] = await Promise.all([get('data/maprando.zip'), get('data/bundle.pkl.gz')]);
    const v = await fetch('data/version.txt').then((r) => (r.ok ? r.text() : '')).catch(() => '');
    return (data = { pkg, bundle, version: v.trim() });
  }

  function buildBoard(groups) {
    const board = $('board'); board.textContent = '';
    const cells = {};
    for (const [label, names] of groups) {
      const g = document.createElement('div'); g.className = 'group';
      const h = document.createElement('h2'); h.textContent = label; g.append(h);
      const ul = document.createElement('ul'); ul.className = 'cells';
      for (const n of names) {
        const li = document.createElement('li'); li.className = 'cell'; li.title = n;
        ul.append(li); cells[n] = li;
      }
      g.append(ul); board.append(g);
    }
    return cells;
  }

  function label(name) { return name.replace(/([a-z])([A-Z0-9])/g, '$1 $2'); }

  // send a message to a worker and wait for its next reply
  function ask(w, msg) {
    return new Promise((resolve, reject) => {
      w.onmessage = (e) => (e.data.type === 'error' ? reject(new Error(e.data.message)) : resolve(e.data));
      w.onerror = (e) => reject(new Error(e.message || 'the worker stopped'));
      w.postMessage(msg);
    });
  }

  $('setup').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!rom) return;
    const seed = $('seed').value.trim() || newSeed();
    $('seed').value = seed;
    clearError();
    downloads.forEach(URL.revokeObjectURL); downloads = [];
    $('result').hidden = true; $('run').hidden = false; $('go').disabled = true;
    $('status-text').textContent = 'Starting Python…'; $('count').textContent = ''; $('detail').textContent = '';
    const started = performance.now();
    const workers = [];
    try {
      const { pkg, bundle, version } = await fetchData();
      for (let i = 0; i < WORKERS; i++) workers.push(new Worker('worker.js'));
      const readies = await Promise.all(workers.map((w) => ask(w, { type: 'init', pkg, bundle, rom })));
      const ready = readies[0];
      const versions = [version && `maprando ${version}`, ready.version].filter(Boolean).join(', ');
      if (versions) $('version').textContent = `Version: ${versions}.`;
      if (ready.problems.length) {
        throw new Error('This ROM cannot take new maps:\n' + ready.problems.slice(0, 6).join('\n') +
          (ready.problems.length > 6 ? `\n…and ${ready.problems.length - 6} more` : ''));
      }
      const cells = buildBoard(ready.groups);
      const queue = ready.names.slice(), total = queue.length;
      for (const n in cells) if (!queue.includes(n)) cells[n].classList.add('vanilla');   // never generated
      const maps = {}, reports = {};
      let done = 0;
      $('status-text').textContent = `Generating maps with ${WORKERS} worker${WORKERS > 1 ? 's' : ''}`;
      const tick = () => { $('count').textContent = `${done} of ${total} maps, ${Math.round((performance.now() - started) / 1000)} s`; };
      tick();
      const timer = setInterval(tick, 1000);
      try {
        await Promise.all(workers.map(async (w) => {
          while (queue.length) {
            const name = queue.shift();
            cells[name].classList.add('working');
            const r = await ask(w, { type: 'map', name, seed });
            maps[name] = r.data; reports[name] = r.report; done++;
            cells[name].classList.remove('working');
            cells[name].classList.add(r.data.length ? 'done' : 'vanilla');
            cells[name].title = `${label(name)}: ${r.report}`;
            $('detail').textContent = `${label(name)}: ${r.report}`;
            tick();
          }
        }));
      } finally { clearInterval(timer); }
      $('status-text').textContent = 'Writing the ROM';
      const names = Object.keys(maps);
      const out = await ask(workers[0], {
        type: 'finish', seed, rom, names, version: versions,
        maps: names.map((n) => maps[n]), reports: names.map((n) => reports[n]),
      });
      const changed = names.filter((n) => maps[n].length).length;
      $('status-text').textContent = 'Done';
      $('detail').textContent = `${changed} of ${total} maps have new layouts. Seed ${seed}.`;
      const romUrl = URL.createObjectURL(new Blob([out.rom], { type: 'application/octet-stream' }));
      const logUrl = URL.createObjectURL(new Blob([out.log], { type: 'text/plain' }));
      downloads = [romUrl, logUrl];
      $('download-rom').href = romUrl; $('download-rom').download = `${romName}-maps-${seed}.gbc`;
      $('download-log').href = logUrl; $('download-log').download = `${romName}-maps-${seed}.txt`;
      $('result').hidden = false;
      $('download-rom').focus();
    } catch (err) {
      $('status-text').textContent = 'Stopped';
      showError(err.message);
    } finally {
      workers.forEach((w) => w.terminate());
      $('go').disabled = !rom;
    }
  });
})();
