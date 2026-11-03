// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
// IntelliCat platform script: what every web app includes (<script src="/platform.js"></script>).
//   Platform.onClose(async () => {...})   called when the user presses MODE; save here. The app is killed 2 s later if it has not finished.
//   Platform.exit()                       the app asks to be closed
//   Platform.files.list(path, ext) / .read / .write(path, text, {create}) / .space() / .mkdir / .rename(path, name) / .copy(path, toDir) / .move / .remove / .open(path) / .audioUrl(path) / .videoUrl(path) / .imageUrl(path)   files inside the home directory (the SD card) only; audioUrl / videoUrl / imageUrl: the address of an audio / video / image file (jpg, png, gif, webp, bmp) for <audio src> / <video src> / <img src> (seekable)
//   Platform.launchArg()                  what the app was started with ({open: path} from the file manager), once; null otherwise
//   Platform.db.get(key) / .set(key, value)   the launcher database: this app's own settings
//   Platform.state.get() / .set(obj)     the app's state for the shared window: set it in onClose, read it at the start; the app comes back to the same place
//   Platform.net.status() / .scan() / .connect(ssid, password)   the network (scan and connect: the Wi-Fi app only)
//   Platform.settings.get() / .set(key, value)   system settings: {language, layouts}; Platform.t(text) translates a string
//   Platform.time.status() / .zones() / .set({year, month, day, hour, minute}) / .setZone(zone) / .setNtp(on)   the console's clock (status for everyone, the rest for Settings only)
//   Platform.notifications.list() / .dismiss(id) / .clear(app?) / .read() / .dnd(on) / .on(fn)   the launcher's notification panel (an app sees and clears its own; .dnd: Settings only; .on: events, see below)
//   Platform.notify(text, {level, sound, vibrate})   system notification (level: info | warning | error)
//   Platform.tools.handle(name, async args => text | {text, result})   answers a tool Caroline calls on this page (declared in the manifest, "caroline": {"tools": [...]}); every page also has dom_text, dom_tree, dom_click, dom_type, dom_key
//   Platform.messageBox({title, text, kind | buttons, icon, default, timeout})   system MessageBox over the page, resolves {index, button, reason: button | cancel | timeout}; kind: ok | okcancel | yesno | yesnocancel, or buttons (1..4 texts); icon: none | info | question | warning | error; ←/→ choose, A confirms, B cancels
//   Platform.sw.call(command, params, agent?)   an agent of this app calls the owner's Camerlengo service (only what the agent's manifest "camerlengo" lists; agent: its id, optional when the app has one); resolves the service's answer; errors carry .kind: network | auth | balance | config | service | forbidden
//   Platform.sound.list() / .play(name, {force})   system sounds: notify, message, ok, warning, error, click, alarm, ring (do-not-disturb silences all but alarm and ring)
//   Platform.vibra.status() / .pulse(pattern, strength) / .stop()   the vibration motor: pattern a name (tap, short, long, double, alarm, ring) or milliseconds [on, off, on, ...]; strength 0..100; off with the system setting "vibration"
//   Platform.clipboard.read({host}) / .write(text) / .clear()   the device's one clipboard (text, shared by every app): read resolves {text, length, at, source}; host: true asks the simulator's host clipboard first (the keyboard's Paste does)
//   Platform.capabilities() / .device()   what of the device the app declared and has; where it runs (kind, touch, screen size). docs/system-api.md
//   Platform.display.status() / .on(fn) / .open(arg) / .close() / .send(msg) / .onMessage(fn) / .size() / .launch()   the external (HDMI) screen: the app's big page (manifest "external") goes on it, the two pages of the app talk; status and on are for every page. docs/system-api.md
//   Platform.onSuspend(async reason => {...})   the BIG page only: it is covered by another app, closed or unplugged (reason: covered | closed | unplugged | reopen): save the state (Platform.state) here
//   Platform.pickFile({ext})             file dialog; resolves {path} (a file or "Last used"), {default:true} ("Default"), or null (B at the top)
// It also shows a thin scrollbar (where a page is longer than the screen), lets the D-pad scroll the page, and loads the on-screen keyboard (osk.js).
(function () {
  const appId = (location.pathname.match(/^\/(?:apps|system)\/([^/]+)\//) || [])[1] || '';

  // the interface language: i18n.js translates the page (and I18N.t the strings scripts build); it has to run before the rest
  if (!window.I18N) document.write('<script src="/i18n.js"><\/script>');

  const style = document.createElement('style');
  style.textContent = '::-webkit-scrollbar{width:6px;height:6px}::-webkit-scrollbar-track{background:rgba(128,128,128,.25)}::-webkit-scrollbar-thumb{background:#f2a900;border-radius:3px}';
  document.head.appendChild(style);

  // input diagnostics (what this page really receives) -> shelld input.log
  const plog = m => fetch('/api/log', {method: 'POST', body: JSON.stringify({msg: appId + ' ' + m})}).catch(() => {});
  addEventListener('keydown', e => plog('keydown key=' + e.key + ' code=' + e.code), true);
  addEventListener('pointerdown', e => plog('pointerdown ' + e.clientX + ',' + e.clientY + ' type=' + e.pointerType), true);
  addEventListener('click', e => plog('click ' + e.clientX + ',' + e.clientY + ' on ' + (e.target.className || e.target.tagName)), true);

  // the page of the external screen (the app's "external" page): its address has ?ext=1; it keeps its own state and is asked to save with "suspend", not "close"
  const isBig = /[?&]ext=1(&|$)/.test(location.search);

  // ---- closing --------------------------------------------------------------------------------
  let closeHandler = null;
  let suspendHandler = null;
  const dispHandlers = [], msgHandlers = [];
  const fire = (list, arg) => list.forEach(fn => { try { fn(arg); } catch (err) { console.error('handler failed', err); } });
  async function saveBig(reason, ws) {   // the big page saves its state and says so; the shell loads the next page after that
    try { if (suspendHandler) await suspendHandler(reason); else if (closeHandler) await closeHandler(); } catch (err) { console.error('onSuspend failed', err); }
    ws.send(JSON.stringify({t: 'suspended'}));
  }
  const noteHandlers = [];
  // ---- tools Caroline calls on the page -------------------------------------------------------------
  const toolHandlers = {};
  const tools = {handle: (name, fn) => { toolHandlers[name] = fn; }};
  const visible = el => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const labelOf = el => (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title || el.alt || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  let domRefs = [];
  const domSelector = 'a,button,input,textarea,select,summary,[role=button],[role=tab],[role=menuitem],[role=checkbox],[onclick],[tabindex]:not([tabindex="-1"])';
  const byRef = (ref, text) => {
    if (ref !== undefined && ref !== null && ref !== '') { const el = domRefs[Number(ref)]; if (!el || !el.isConnected) throw new Error('ref ' + ref + ' is gone: call dom_tree again'); return el; }
    const want = String(text || '').trim().toLowerCase();
    const el = want && [...document.querySelectorAll(domSelector)].filter(visible).find(x => labelOf(x).toLowerCase().includes(want));
    if (!el) throw new Error('nothing to press with the text "' + text + '"');
    return el;
  };
  Object.assign(toolHandlers, {
    dom_text: async () => (document.body.innerText || '').trim().slice(0, 20000) || '(the page shows no text)',
    dom_tree: async () => {
      domRefs = [...document.querySelectorAll(domSelector)].filter(visible);
      return domRefs.map((el, i) => '[' + i + '] ' + el.tagName.toLowerCase() + (el.type ? '(' + el.type + ')' : '') + (el.disabled ? ' disabled' : '') + ' "' + labelOf(el) + '"' + (el.value && el.tagName !== 'BUTTON' ? ' value="' + String(el.value).slice(0, 60) + '"' : '')).join('\n') || '(nothing to press or type into)';
    },
    dom_click: async a => { const el = byRef(a.ref, a.text); el.scrollIntoView({block: 'nearest'}); el.click(); return 'pressed "' + labelOf(el) + '"'; },
    dom_type: async a => {
      const el = byRef(a.ref, a.text); el.focus();
      if (el.isContentEditable) el.textContent = a.value; else el.value = a.value;
      el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true}));
      return 'typed into "' + labelOf(el) + '"';
    },
    dom_key: async a => {
      const t = document.activeElement || document.body;
      for (const type of ['keydown', 'keyup']) t.dispatchEvent(new KeyboardEvent(type, {key: a.key, bubbles: true, cancelable: true}));
      return 'key ' + a.key;
    },
  });
  async function runTool(m, ws) {
    const reply = o => ws.send(JSON.stringify(Object.assign({t: 'tool_result', id: m.id}, o)));
    const fn = toolHandlers[m.name];
    if (!fn) return reply({text: 'this page has no tool "' + m.name + '"', is_error: true});
    try {
      const r = await fn(m.args || {});
      reply(typeof r === 'string' ? {text: r} : {text: (r && r.text) || '', result: r && r.result});
    } catch (err) { reply({text: String(err && err.message || err), is_error: true}); }
  }
  function connect() {
    const ws = new WebSocket(`ws://${location.host}/appws?id=${encodeURIComponent(appId)}${isBig ? '&screen=big' : ''}`);
    ws.onmessage = async e => {
      let m; try { m = JSON.parse(e.data); } catch (err) { return; }
      if (m.t === 'tool') return runTool(m, ws);
      if (m.t === 'msgbox') return showBox(m, ws);
      if (m.t === 'msgbox_close') { if (box && box.id === m.id) hideBox(); return; }
      if (m.t === 'vibra') return flashVibra(m.pattern || []);
      if (m.t === 'osk_remote') {   // a field of the external screen has the focus: the keyboard is drawn here, on the console, and types there
        const o = window.__osk; if (!o || !o.showRemote) return;
        if (m.show) { plog('osk remote on'); o.showRemote(m.type, e => ws.send(JSON.stringify({t: 'osk_edit', e})), () => ws.send(JSON.stringify({t: 'osk_closed'}))); }
        else { plog('osk remote off'); o.hideRemote(); }
        return;
      }
      if (m.t === 'notifications') { noteHandlers.forEach(fn => { try { fn(m); } catch (err) { console.error('notifications handler failed', err); } }); return; }
      if (m.t === 'display') return fire(dispHandlers, m);
      if (m.t === 'display_msg') return fire(msgHandlers, m.message);
      if (m.t === 'suspend') return saveBig(m.reason || 'covered', ws);
      if (m.t !== 'close') return;
      if (isBig) return saveBig('closed', ws);   // the app is closed: its big page saves too (the console page answers "closed")
      try { if (closeHandler) await closeHandler(); } catch (err) { console.error('onClose failed', err); }
      ws.send(JSON.stringify({t: 'closed'}));
    };
    ws.onclose = () => setTimeout(connect, 500);
  }
  if (appId) connect();

  // ---- services (files, database, exit) -------------------------------------------------------
  async function call(url, opts) {
    const r = await fetch(url, opts), j = await r.json();
    if (!r.ok) { const err = new Error(j.error || r.statusText); err.status = r.status; err.kind = j.kind; throw err; }
    return j;
  }
  const post = (url, body) => call(url, {method: 'POST', body: JSON.stringify(body)});
  const files = {
    list: (path, ext) => call('/api/fs/list?path=' + encodeURIComponent(path || '') + '&ext=' + encodeURIComponent(ext || '')),
    read: path => call('/api/fs/read?path=' + encodeURIComponent(path)),
    write: (path, text, o) => post('/api/fs/write', {path, text, create: !!(o && o.create)}),
    space: () => call('/api/fs/space'),   // {total, used, free} in bytes: the SD card
    mkdir: path => post('/api/fs/mkdir', {path}),
    rename: (path, name) => post('/api/fs/rename', {path, name}),
    copy: (path, to) => post('/api/fs/copy', {path, to}),
    move: (path, to) => post('/api/fs/move', {path, to}),
    remove: path => post('/api/fs/delete', {path}),
    // start the app that opens this file's type (rejects with status 404 when no app does)
    open: path => post('/api/open', {path}),
    audioUrl: path => '/api/fs/audio?path=' + encodeURIComponent(path),
    videoUrl: path => '/api/fs/video?path=' + encodeURIComponent(path),
    imageUrl: path => '/api/fs/image?path=' + encodeURIComponent(path),
  };
  const db = {
    get: key => call('/api/db/get?key=' + encodeURIComponent(key)).then(r => r.value),
    set: (key, value) => post('/api/db/set', {key, value}),
  };
  // The app's state for the shared window (every app shares ONE window, one at a time): saved when the app is closed (in Platform.onClose),
  // read when it is opened again, so that it "always returns to the same place" (the open file, the dialogue, the folder, the scroll).
  // The app decides what it keeps: any JSON-able object. get() resolves null when nothing was saved.
  const stateKey = isBig ? '__state_ext' : '__state';   // the big page of an app saves its place apart from the console page
  const state = {
    get: () => call('/api/db/get?key=' + stateKey).then(r => { try { const s = r.value ? JSON.parse(r.value) : null; if (s) plog('state restored'); return s; } catch (e) { return null; } }),
    set: obj => post('/api/db/set', {key: stateKey, value: JSON.stringify(obj)}),
  };
  const exit = () => post('/api/exit', {});
  // what the app was started with ({open: path} when the file manager opened a file), given out once; null when started plainly
  const launchArg = () => call('/api/launch-arg').then(r => r.arg);
  // the network: status is for everyone, scan and connect only for the Wi-Fi app
  const net = {
    status: () => call('/api/net/status'),
    scan: () => call('/api/net/scan').then(r => r.networks),
    connect: (ssid, password) => post('/api/net/connect', {ssid, password}),
  };

  // ---- file dialog ----------------------------------------------------------------------------
  // List: D-pad up/down moves, A opens a folder or picks a file, B goes up one level (B at the top resolves null).
  // Buttons "Default" / "Last used" sit at the bottom: D-pad down from the last row reaches them, left/right switches, A presses.
  function pickFile(opts) {
    const ext = (opts && opts.ext) || '';
    return new Promise(resolve => {
      const root = document.createElement('div');
      root.style.cssText = 'position:fixed;inset:0;z-index:2147483646;background:#111;color:#fff;display:flex;flex-direction:column;font:20px "DejaVu Sans",sans-serif;-webkit-user-select:none;user-select:none';
      root.innerHTML = '<div id="h" style="flex:none;height:40px;line-height:40px;padding:0 12px;background:#000;color:#f2a900;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></div>' +
        '<div id="l" style="flex:1;min-height:0;overflow:auto;touch-action:none"></div>' +
        '<div id="b" style="flex:none;display:flex;gap:8px;padding:6px;background:#000"></div>';
      document.body.appendChild(root);
      const head = root.querySelector('#h'), list = root.querySelector('#l'), bar = root.querySelector('#b');
      let cwd = '', rows = [], sel = 0, area = 'list', bsel = 0, last = null;  // last: path of the last used file if it still exists
      const buttons = [{label: 'Default', on: () => true}, {label: 'Last used', on: () => last !== null}];

      function finish(v) { removeEventListener('keydown', onKey, true); root.remove(); resolve(v); }
      function up() { if (!cwd) return finish(null); cwd = cwd.split('/').slice(0, -1).join('/'); load(); }
      function open(i) {
        const r = rows[i]; if (!r) return;
        if (r.up) return up();
        const p = (cwd ? cwd + '/' : '') + r.name;
        if (r.dir) { cwd = p; load(); } else finish({path: p});
      }
      function press(i) { if (!buttons[i].on()) return; finish(i === 0 ? {default: true} : {path: last}); }
      function draw() {
        list.innerHTML = '';
        rows.forEach((r, i) => {
          const d = document.createElement('div');
          d.textContent = r.up ? '..' : r.dir ? r.name + '/' : r.name;
          d.style.cssText = 'height:44px;line-height:44px;padding:0 14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' + (area === 'list' && i === sel ? 'background:#f2a900;color:#000' : r.dir ? 'color:#8cf' : '');
          d.onclick = () => { area = 'list'; sel = i; open(i); };
          list.appendChild(d);
        });
        if (!rows.length) list.textContent = '(empty)';
        const cur = list.children[sel]; if (area === 'list' && cur && cur.scrollIntoView) cur.scrollIntoView({block: 'nearest'});
        bar.innerHTML = '';
        buttons.forEach((b, i) => {
          const d = document.createElement('div'), on = b.on();
          d.textContent = b.label;
          d.style.cssText = 'flex:1;height:44px;line-height:44px;text-align:center;border-radius:5px;' +
            (area === 'btn' && i === bsel ? 'background:#f2a900;color:#000;' : 'background:#2d3640;') + (on ? '' : 'opacity:.35;');
          d.onclick = () => press(i);
          bar.appendChild(d);
        });
      }
      async function load() {
        head.textContent = '/' + cwd;
        let r; try { r = await files.list(cwd, ext); } catch (e) { return finish(null); }
        rows = (cwd ? [{up: true}] : []).concat(r.entries);
        sel = 0; draw();
      }
      // the last used file counts only if it is still there
      async function findLast() {
        try {
          const p = await db.get('lastFile'); if (!p) return;
          const i = p.lastIndexOf('/');
          const r = await files.list(i < 0 ? '' : p.slice(0, i), ext);
          if (r.entries.some(e => !e.dir && e.name === p.slice(i + 1))) { last = p; draw(); }
        } catch (e) {}
      }
      function onKey(e) {
        const k = e.key, stop = () => { e.preventDefault(); e.stopImmediatePropagation(); };
        if (k === 'Escape') { stop(); return up(); }
        if (k === 'Enter') { stop(); return area === 'list' ? open(sel) : press(bsel); }
        if (area === 'list') {
          if (k === 'ArrowUp') sel = Math.max(0, sel - 1);
          else if (k === 'ArrowDown') { if (sel >= rows.length - 1) { area = 'btn'; bsel = 0; } else sel++; }
          else return;
        } else {
          if (k === 'ArrowUp') area = 'list';
          else if (k === 'ArrowLeft' || k === 'ArrowRight') {
            const n = (bsel + (k === 'ArrowLeft' ? buttons.length - 1 : 1)) % buttons.length;
            if (buttons[n].on()) bsel = n;
          } else if (k !== 'ArrowDown') return;
        }
        stop(); draw();
      }
      addEventListener('keydown', onKey, true);

      // drag to scroll the list (the touchscreen arrives as pointer events)
      let d = null, dragged = false;
      list.addEventListener('pointerdown', e => { d = {y: e.clientY, st: list.scrollTop}; dragged = false; });
      list.addEventListener('pointermove', e => { if (!d) return; const dy = e.clientY - d.y; if (!dragged && Math.abs(dy) > 8) { dragged = true; list.setPointerCapture(e.pointerId); } if (dragged) list.scrollTop = d.st - dy; });
      const end = () => { d = null; };
      list.addEventListener('pointerup', end); list.addEventListener('pointercancel', end);
      list.addEventListener('click', e => { if (dragged) { e.stopPropagation(); e.preventDefault(); dragged = false; } }, true);

      load(); findLast();
    });
  }

  // ---- D-pad scrolls the page when nothing else used the key -----------------------------------
  addEventListener('keydown', e => {
    if (e.defaultPrevented) return;
    const t = e.composedPath()[0];
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    const step = 60, d = {ArrowUp: [0, -step], ArrowDown: [0, step], ArrowLeft: [-step, 0], ArrowRight: [step, 0]}[e.key];
    if (d) { scrollBy(d[0], d[1]); e.preventDefault(); }
  });

  // ---- a finger (in the simulator the mouse) drags a long list or feed: one rule for every app -------------------
  // The nearest ancestor that scrolls follows the drag and the click that ends a drag is dropped. A page that lets WebKit pan a real
  // touch by itself (no `touch-action: none` on the scroller) gets only the mouse here, so a finger never scrolls twice. Apps that had
  // their own handler before this one keep it: both set the same scrollTop from the same start, so they agree.
  (function () {
    const editable = el => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
    const scroller = el => {
      for (let e = el; e && e !== document.body && e !== document.documentElement; e = e.parentElement) {
        if (/(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight) return e;
      }
      return null;
    };
    let d = null, dragged = false;
    addEventListener('pointerdown', e => {
      d = null; dragged = false;
      if (e.button !== 0 || editable(e.target)) return;
      const sc = scroller(e.target); if (!sc) return;
      if (e.pointerType !== 'mouse' && getComputedStyle(sc).touchAction !== 'none') return;
      d = {y: e.clientY, st: sc.scrollTop, sc, id: e.pointerId};
    }, true);
    addEventListener('pointermove', e => {
      if (!d || e.pointerId !== d.id) return;
      const dy = e.clientY - d.y;
      if (!dragged && Math.abs(dy) > 8) dragged = true;
      if (dragged) { d.sc.scrollTop = d.st - dy; e.preventDefault(); }
    }, true);
    const end = () => { d = null; };
    addEventListener('pointerup', end, true); addEventListener('pointercancel', end, true);
    addEventListener('click', e => { if (dragged) { e.preventDefault(); e.stopPropagation(); dragged = false; } }, true);
  })();

  // ---- on-screen keyboard ---------------------------------------------------------------------
  const s = document.createElement('script');
  if (isBig) window.__icExternal = true;   // the big screen draws no keyboard: the console's does (osk.js)
  s.src = '/osk-bundle.js';  // osk.js with the enabled layouts and the keyboard's words in the interface language
  s.onload = () => OSK.attach();
  document.head.appendChild(s);
  // the frame of the screen that has the focus, while there is an external screen (focusframe.js)
  const ff = document.createElement('script');
  ff.src = '/focusframe.js';
  document.head.appendChild(ff);

  // ---- MessageBox (the system API, runtime/sysapi.py): drawn over the page; ←/→ choose, A (Enter) confirms, B (Escape) cancels --------------------------------
  let box = null;   // the box on the screen: {id, el, i, n, ws}
  function hideBox() { if (box) { box.el.remove(); box = null; } }
  function showBox(m, ws) {
    if (box && box.id === m.id) { box.ws = ws; return; }   // asked again: a page that has just connected, the next of the queue
    hideBox();
    if (!document.getElementById('bbox-style')) { const st = document.createElement('style'); st.id = 'bbox-style'; st.textContent = '@keyframes bbox-in{from{opacity:0;transform:scale(.94)}to{opacity:1;transform:none}}'; document.head.appendChild(st); }
    const mk = (tag, css, text) => { const e = document.createElement(tag); e.style.cssText = css; if (text !== undefined) e.textContent = text; return e; };
    const el = mk('div', 'position:fixed;inset:0;z-index:2147483647;background:rgba(6,9,12,.74);backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;font:20px "DejaVu Sans",sans-serif;color:#fff;-webkit-user-select:none;user-select:none');
    const card = mk('div', 'width:520px;max-width:92%;background:linear-gradient(180deg,#26303a,#1a2027);border:1px solid rgba(242,169,0,.55);border-radius:18px;padding:24px 26px 22px;text-align:center;box-shadow:0 18px 60px rgba(0,0,0,.6),0 0 0 1px rgba(255,255,255,.04) inset;animation:bbox-in .16s ease-out');
    const icons = {info: ['i', '#3b82f6'], question: ['?', '#f2a900'], warning: ['!', '#f2a900'], error: ['×', '#e5484d']};
    if (icons[m.icon]) card.appendChild(mk('div', 'width:52px;height:52px;line-height:52px;margin:0 auto 12px;border-radius:50%;font-size:32px;font-weight:bold;color:#10151a;box-shadow:0 0 0 6px rgba(255,255,255,.07);background:' + icons[m.icon][1], icons[m.icon][0]));
    if (m.title) card.appendChild(mk('div', 'font-size:24px;font-weight:bold;color:#fff;letter-spacing:.2px;margin-bottom:10px', t(m.title)));
    if (m.text) card.appendChild(mk('div', 'font-size:19px;line-height:1.4;color:#c9d2da;margin-bottom:20px;white-space:pre-wrap;max-height:200px;overflow:hidden', m.text));
    const row = mk('div', 'display:flex;gap:12px;justify-content:center');
    box = {id: m.id, el, i: m.default || 0, n: m.buttons.length, ws, btns: m.buttons.map(b => row.appendChild(mk('div', 'min-width:104px;padding:11px 22px;border-radius:999px;font-weight:bold;border:1px solid rgba(255,255,255,.12);transition:background .1s', t(b))))};
    card.appendChild(row); el.appendChild(card); document.body.appendChild(el);
    paintBox();
  }
  function paintBox() { box.btns.forEach((b, k) => { b.style.background = k === box.i ? '#f2a900' : '#2b343c'; b.style.color = k === box.i ? '#10151a' : '#e6ebf0'; b.style.boxShadow = k === box.i ? '0 0 0 3px rgba(242,169,0,.35)' : 'none'; }); }
  function answerBox(index) { const b = box; hideBox(); try { b.ws.send(JSON.stringify({t: 'msgbox_result', id: b.id, index})); } catch (err) {} }
  addEventListener('keydown', e => {   // while a box is up the page hears nothing: the cursor chooses, A confirms, B cancels
    if (!box) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (e.key === 'ArrowLeft') { box.i = Math.max(0, box.i - 1); paintBox(); }
    else if (e.key === 'ArrowRight') { box.i = Math.min(box.n - 1, box.i + 1); paintBox(); }
    else if (e.key === 'Enter') answerBox(box.i);
    else if (e.key === 'Escape') answerBox(-1);
  }, true);
  // the simulator's stand-in for the vibration motor: the page's edge lights up while it runs
  const vibraTimers = [];
  function flashVibra(pattern) {
    vibraTimers.splice(0).forEach(clearTimeout);
    const st = document.documentElement.style;
    st.outline = '';
    let at = 0;
    pattern.forEach((ms, i) => {
      if (i % 2 === 0) {
        vibraTimers.push(setTimeout(() => { st.outline = '4px solid #f2a900'; st.outlineOffset = '-4px'; }, at));
        vibraTimers.push(setTimeout(() => { st.outline = ''; }, at + ms));
      }
      at += ms;
    });
  }
  const messageBox = o => post('/api/msgbox', Object.assign({wait: true}, o));
  // an agent of this app calls the owner's Camerlengo service through the shell (the commands its manifest's "camerlengo" lists; the shell holds the session and the key)
  const sw = {call: (command, params, agent) => call('/api/agents/sw', {method: 'POST', headers: agent ? {'X-IntelliCat-Agent': appId + '/' + agent} : {}, body: JSON.stringify({command, params: params || {}})})};
  const sound = {list: () => call('/api/sound/list').then(r => r.sounds), play: (name, o) => post('/api/sound/play', Object.assign({name}, o))};
  const vibra = {status: () => call('/api/vibra'), pulse: (pattern, strength) => post('/api/vibra/pulse', {pattern, strength}), stop: () => post('/api/vibra/stop', {})};

  const clipboard = {read: o => o && o.host ? post('/api/clipboard/pull', {}) : call('/api/clipboard'), write: text => post('/api/clipboard', {text}), clear: () => post('/api/clipboard/clear', {})};

  // a system notification: text in the status bar; level "error" (or sound) beeps, vibrate drives the vibration motor
  const notify = (text, o) => post('/api/notify', Object.assign({text, app: appId}, o));

  // system settings: everybody reads them (language, layouts); only Settings and the launcher write them
  const settings = {
    get: () => call('/api/settings'),
    set: (key, value) => post('/api/settings/set', {key, value}),
  };
  // the clock of OS Beroun: {epoch, zone, offset, local: {year..second}, ntp, synced}; the time is in the console's own time zone
  const time = {
    status: () => call('/api/time/status'),
    zones: () => call('/api/time/zones').then(r => r.zones),
    set: f => post('/api/time/set', f),
    setZone: zone => post('/api/time/zone', {zone}),
    setNtp: on => post('/api/time/ntp', {on}),
  };
  // the launcher's notification panel: an app sees and clears only its own notifications (Settings and the launcher: all); do-not-disturb is Settings' to switch.
  // list() -> {items: [{id, app, text, level, at, read}], unread, dnd}, newest first. on(fn): fn({event, items, unread, dnd}) when notifications are posted
  // (event "posted"), dismissed, read, or do-not-disturb changes (the app the notifications belong to gets their text, every other app only
  // {event: "changed", unread, dnd}); returns a function that stops listening.
  const notifications = {
    list: () => call('/api/notifications/list'),
    dismiss: id => post('/api/notifications/dismiss', {id}),
    clear: app => post('/api/notifications/clear', app ? {app} : {}),
    read: () => post('/api/notifications/read', {}),
    dnd: on => post('/api/notifications/dnd', {on}),
    on: fn => { noteHandlers.push(fn); return () => { const i = noteHandlers.indexOf(fn); if (i >= 0) noteHandlers.splice(i, 1); }; },
  };
  // The external (HDMI) screen. A page of an app with a manifest "external" opens its big page there; the two pages (the console's remote and the big page) talk by messages.
  //   status() -> {connected, width, height, focus: console | external, top: app | null, stack: [app...]}; on(fn): fn({event: connected | disconnected | resize | focus | stack, ...status}), returns the stop function
  //   open(arg): the big page of this app goes on top (rejects with status 409 when there is no screen); close(): takes it off; send(msg) / onMessage(fn): to the app's other page, any JSON
  //   launch(): on the big page, once: {arg, resume}: what open() gave, and whether the page was under another one and comes back (restore Platform.state)
  const listen = (list, fn) => { list.push(fn); return () => { const i = list.indexOf(fn); if (i >= 0) list.splice(i, 1); }; };
  const display = {
    status: () => call('/api/display'),
    on: fn => listen(dispHandlers, fn),
    open: arg => post('/api/display/open', {arg}),
    close: () => post('/api/display/close', {}),
    send: message => post('/api/display/send', {message}),
    onMessage: fn => listen(msgHandlers, fn),
    size: () => ({width: innerWidth, height: innerHeight}),
    launch: () => call('/api/display/launch'),
  };
  // Where the app runs and what it can use. capabilities() -> [name]: the capabilities the manifest declared that this device has (check one, never the kind of device);
  // device() -> {kind: intellicat | simulator | hosted | android, touch, width, height}: for the layout and hints to the owner, not for the logic of capabilities
  const capabilities = async () => (await call('/api/capabilities')).capabilities;
  const device = async () => Object.assign(await call('/api/device'), {width: innerWidth, height: innerHeight});
  const t = (s, args) => window.I18N ? I18N.t(s, args) : s;

  window.Platform = {capabilities, device, onClose: fn => { closeHandler = fn; }, onSuspend: fn => { suspendHandler = fn; }, display, exit, launchArg, files, db, state, pickFile, notify, net, settings, time, notifications, t, tools, messageBox, sw, sound, vibra, clipboard};
})();
