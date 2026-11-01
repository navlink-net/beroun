// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
// IntelliCat on-screen keyboard (English QWERTY) - overlay that types into the page's own focused field.
//   <script src="/osk.js"></script><script>OSK.attach()</script>
// It opens when an editable element gets focus and closes when focus leaves (or on its Hide key), scrolls the
// field above the keyboard, and edits the field through the browser's editing commands, so the page sees
// ordinary beforeinput/input events (React/Vue-controlled fields included).
// Input while it is open: touch / mouse, or D-pad (arrows) + A = press key, B = backspace, X = talk while held,
// Y = show/hide, SELECT = selection mode in the field (arrows select, A: the menu Copy / Cut / Paste / Select All) (the simulator delivers arrows, Enter, Escape, space, Tab, F2).
// B on an EMPTY field is not a backspace: the page gets it as Escape (every app takes B as "back": it leaves the field, the next B goes
// back a screen). Holding B to erase never leaves the field by accident: only a fresh press on an already empty field does.
// R2 (Caroline), R1 (power) and L1/L2 (volume) are system buttons: the keyboard, like every app, never sees them.
(function () {
  // ---- layouts: data (web-shell/layouts/*.json), the enabled ones come with the page in window.__OSK_CONFIG ----------
  // (the bundle served at /osk-bundle.js: {order: [ids], layouts: {id: layout}, strings: {"Shift": "...", ...}}).
  // A layout: {id, label, rows: [3 rows of letters], symbolsTop?: [10 keys], symbolsRow3?: [10 keys]}.
  const DEFAULT_LAYOUT = {id: 'en', label: 'EN', rows: [
    'q w e r t y u i o p'.split(' '), 'a s d f g h j k l'.split(' '), 'z x c v b n m'.split(' ')]};
  const cfg = () => window.__OSK_CONFIG || {};
  const tr = s => (cfg().strings && cfg().strings[s]) || s;  // the keyboard's own words in the interface language
  function enabledLayouts() {
    const c = cfg(), out = [];
    (c.order || ['en']).forEach(id => { const l = (c.layouts && c.layouts[id]) || (id === 'en' ? DEFAULT_LAYOUT : null); if (l) out.push(l); });
    return out.length ? out : [DEFAULT_LAYOUT];
  }
  const K = k => ({k});
  const digits = '1 2 3 4 5 6 7 8 9 0'.split(' ');
  function buildLayers(l) {
    const gapped = r => r.length < 10 ? [{gap: 1}].concat(r.map(K), [{gap: 1}]) : r.map(K);
    const fnRow = main => [
      {id: main, label: main === 'sym' ? '?123' : 'ABC', w: 2, cls: 'fn'}, {id: 'lang', label: l.label, w: 2, cls: 'fn'}, {id: 'mic', label: 'Mic', w: 2, cls: 'fn'},
      {k: ','}, {id: 'space', label: 'space', w: 5, k: ' '}, {k: '.'}, {id: 'enter', label: 'Enter', w: 3, cls: 'fn'}, {id: 'hide', label: 'Hide', w: 2, cls: 'fn'}];
    const bksp = {id: 'bksp', label: '⌫', w: 3, cls: 'fn'};
    return {
      letters: [digits.map(K), l.rows[0].map(K), gapped(l.rows[1]),
                [{id: 'shift', label: 'Shift', w: 3, cls: 'fn'}].concat(l.rows[2].map(K), [bksp]), fnRow('sym')],
      symbols: [(l.symbolsTop || digits).map(K), '@ # $ % & * - + ( )'.split(' ').map(K), gapped(l.symbolsRow3 || '! " \' : ; / ? = _'.split(' ')),
                [{id: 'sym2', label: '~[{', w: 3, cls: 'fn'}].concat('< > [ ] { } \\'.split(' ').map(K), [bksp]), fnRow('abc')],
    };
  }
  const cased = ch => ch.toUpperCase() !== ch.toLowerCase();

  const CSS = `
    :host { all: initial; }
    #wrap { position: fixed; left: 0; right: 0; bottom: 0; z-index: 2147483647; background: #111; padding: 4px 4px 4px; direction: ltr;
            font-family: "DejaVu Sans", sans-serif; -webkit-user-select: none; user-select: none; touch-action: none; }
    #kb { display: flex; flex-direction: column; gap: 4px; }
    .row { display: flex; gap: 4px; height: 46px; }
    .key { position: relative; display: flex; align-items: center; justify-content: center; background: #2d3640; color: #fff; border-radius: 5px; font-size: 20px; }
    .key.has-alt::after { content: ''; position: absolute; top: 4px; right: 5px; width: 4px; height: 4px; border-radius: 50%; background: #7a8a99; }
    .altpop { position: absolute; display: flex; gap: 4px; padding: 4px; background: #0b0f13; border-radius: 6px; box-shadow: 0 4px 14px rgba(0, 0, 0, .7); z-index: 2; }
    .altkey { width: 42px; height: 46px; display: flex; align-items: center; justify-content: center; background: #3a4654; color: #fff; border-radius: 5px; font-size: 22px; }
    .altkey.focus { outline: 3px solid #fff; outline-offset: -3px; }
    .key.fn { background: #1d242b; font-size: 16px; color: #ccc; }
    .key.on { background: #f2a900; color: #000; }
    .key.caps { box-shadow: inset 0 -4px 0 #000; }
    .key.down { background: #4b5b6b; }
    .key.rec { background: #d32f2f; color: #fff; }
    .key.focus { outline: 3px solid #fff; outline-offset: -3px; }`;

  // ---- keyboard component -------------------------------------------------------------------
  // opts.onKey({type:'char',text} | {type:'backspace'} | {type:'enter'} | {type:'hide'})
  function createKeyboard(root, opts) {
    const layouts = enabledLayouts();
    let li = 0, LAYERS = buildLayers(layouts[0]);
    let layer = 'letters', shift = false, caps = false, lastShift = 0, focus = {r: 1, c: 4}, keys = [], repeat = null, micOn = false;
    const emit = e => opts.onKey && opts.onKey(e);

    // Keys with alternates (accented letters): hold the key (or A on it) and pick one; a quick press types the key itself.
    let altPop = null;  // {el, items: [{el, ch}], i}
    const altOf = def => (layer === 'letters' && def.k && layouts[li].alt && layouts[li].alt[def.k]) || null;
    function closeAlt() { if (altPop) { altPop.el.remove(); altPop = null; } }
    function altChoose(ch) {
      closeAlt();
      if (shift && cased(ch)) ch = ch.toUpperCase();
      emit({type: 'char', text: ch});
      if (shift && !caps) { shift = false; draw(); }
    }
    function altShow(keyEl, def) {
      closeAlt();
      const alts = altOf(def); if (!alts) return;
      const wrapEl = root.parentNode, kr = keyEl.getBoundingClientRect(), wr = wrapEl.getBoundingClientRect();
      const pop = document.createElement('div'); pop.className = 'altpop';
      const w = alts.length * 46 + 8;
      pop.style.left = Math.max(0, Math.min(wr.width - w, kr.left - wr.left - 6)) + 'px';
      pop.style.bottom = (wr.bottom - kr.top + 4) + 'px';
      const items = alts.map((ch, i) => {
        const a = document.createElement('div'); a.className = 'altkey' + (i === 0 ? ' focus' : '');
        a.textContent = shift && cased(ch) ? ch.toUpperCase() : ch;
        a.addEventListener('pointerdown', ev => { ev.preventDefault(); ev.stopPropagation(); });
        a.addEventListener('pointerup', ev => { ev.preventDefault(); altChoose(ch); });
        pop.appendChild(a);
        return {el: a, ch};
      });
      wrapEl.appendChild(pop);
      altPop = {el: pop, items, i: 0};
    }
    function altMove(d) {
      if (!altPop) return;
      altPop.items[altPop.i].el.classList.remove('focus');
      altPop.i = Math.max(0, Math.min(altPop.items.length - 1, altPop.i + d));
      altPop.items[altPop.i].el.classList.add('focus');
    }
    let altKeyTimer = 0, altKeyShown = false;  // the A button held on a key with alternates

    // the microphone works while it is held: the Mic key (touch), A on it, or the X button
    function mic(on) {
      if (on === micOn) return;
      micOn = on;
      keys.forEach(row => row.forEach(k => { if (k.def.id === 'mic') k.el.classList.toggle('rec', on); }));
      emit({type: 'mic', on});
    }

    function press(key) {
      if (key.id === 'mic') return mic(true);
      if (key.id === 'lang') {  // the next enabled layout (nothing to do when there is only one)
        if (layouts.length > 1) { li = (li + 1) % layouts.length; LAYERS = buildLayers(layouts[li]); shift = caps = false; draw(); }
        return;
      }
      if (key.id === 'shift') {
        const now = Date.now();
        if (now - lastShift < 400) { caps = true; shift = true; } else { caps = false; shift = !shift; }
        lastShift = now; draw(); return;
      }
      if (key.id === 'sym' || key.id === 'abc') { layer = key.id === 'sym' ? 'symbols' : 'letters'; shift = caps = false; draw(); return; }
      if (key.id === 'sym2') return;
      if (key.id === 'bksp') return emit({type: 'backspace'});
      if (key.id === 'enter') return emit({type: 'enter'});
      if (key.id === 'hide') return emit({type: 'hide'});
      let ch = key.k;
      if (layer === 'letters' && shift && cased(ch)) ch = ch.toUpperCase();
      emit({type: 'char', text: ch});
      if (ch === '@' && layer === 'symbols') { layer = 'letters'; shift = caps = false; draw(); return; }   // an address goes on with the domain: letters
      if (shift && !caps) { shift = false; draw(); }
    }

    function draw() {
      root.innerHTML = ''; keys = [];
      LAYERS[layer].forEach((row, r) => {
        const rowEl = document.createElement('div'); rowEl.className = 'row'; keys[r] = [];
        row.forEach(def => {
          const el = document.createElement('div');
          el.style.flex = (def.w || def.gap || 2) + ' 1 0';
          if (def.gap) { rowEl.appendChild(el); return; }
          el.className = 'key ' + (def.cls || '') + (def.id === 'shift' && shift ? ' on' : '') + (def.id === 'shift' && caps ? ' caps' : '');
          let label = def.label ? tr(def.label) : def.k;
          if (layer === 'letters' && !def.label && shift && cased(def.k)) label = def.k.toUpperCase();
          el.textContent = label;
          keys[r].push({el, def});
          const hasAlt = !!altOf(def);
          if (hasAlt) el.classList.add('has-alt');
          let altTimer = 0, altShown = false;
          el.addEventListener('pointerdown', ev => {
            ev.preventDefault(); closeAlt(); setFocus(r, keys[r].findIndex(k => k.el === el)); el.classList.add('down');
            if (hasAlt) { altShown = false; altTimer = setTimeout(() => { altTimer = 0; altShown = true; altShow(el, def); }, 450); return; }
            press(def);
            if (def.id === 'bksp') { clearTimeout(repeat); clearInterval(repeat); repeat = setTimeout(() => { repeat = setInterval(() => emit({type: 'backspace'}), 70); }, 400); }
          });
          if (def.id === 'mic' && micOn) el.classList.add('rec');
          const up = () => { el.classList.remove('down'); clearTimeout(repeat); clearInterval(repeat); if (def.id === 'mic') mic(false); };
          el.addEventListener('pointerup', ev => {
            up();
            if (!hasAlt) return;
            if (altTimer) { clearTimeout(altTimer); altTimer = 0; press(def); return; }   // a quick press
            if (altShown && altPop) {  // released over an alternate (a touch is captured by the key itself): take it
              const sh = root.getRootNode(), hit = sh.elementFromPoint && sh.elementFromPoint(ev.clientX, ev.clientY);
              const it = hit && altPop.items.find(x => x.el === hit);
              if (it) altChoose(it.ch);
            }
          });
          el.addEventListener('pointerleave', () => { if (altTimer) { clearTimeout(altTimer); altTimer = 0; } if (!hasAlt) up(); });
          el.addEventListener('pointercancel', () => { if (altTimer) { clearTimeout(altTimer); altTimer = 0; } up(); });
          rowEl.appendChild(el);
        });
        root.appendChild(rowEl);
      });
      setFocus(Math.min(focus.r, keys.length - 1), focus.c);
    }

    function setFocus(r, c) {
      r = Math.max(0, Math.min(keys.length - 1, r)); c = Math.max(0, Math.min(keys[r].length - 1, c));
      keys.forEach(row => row.forEach(k => k.el.classList.remove('focus')));
      focus = {r, c}; keys[r][c].el.classList.add('focus');
    }
    const center = k => { const b = k.el.getBoundingClientRect(); return b.left + b.width / 2; };
    function move(dr, dc) {
      if (dc) return setFocus(focus.r, focus.c + dc);
      const nr = Math.max(0, Math.min(keys.length - 1, focus.r + dr));
      if (nr === focus.r) return;
      const x = center(keys[focus.r][focus.c]);
      let best = 0, bd = 1e9;
      keys[nr].forEach((k, i) => { const d = Math.abs(center(k) - x); if (d < bd) { bd = d; best = i; } });
      setFocus(nr, best);
    }
    const find = id => { for (const row of keys) for (const k of row) if (k.def.id === id) return k.def; };

    // D-pad / buttons; returns true when the key was consumed
    function handleKey(e) {
      if (altPop) {  // the alternates are open: left/right pick, A takes (on release), B closes
        if (e.key === 'ArrowLeft') altMove(-1);
        else if (e.key === 'ArrowRight') altMove(1);
        else if (e.key === 'Escape') closeAlt();
        return true;
      }
      if (e.key === 'Enter' && !e.repeat) {  // A on a key with alternates: held, it opens them; a quick press types the key
        const f = keys[focus.r][focus.c];
        if (altOf(f.def)) { altKeyShown = false; altKeyTimer = setTimeout(() => { altKeyTimer = 0; altKeyShown = true; altShow(f.el, f.def); }, 450); return true; }
      }
      if (e.key === 'Enter' && e.repeat && (altKeyTimer || altKeyShown)) return true;
      const map = {ArrowLeft: () => move(0, -1), ArrowRight: () => move(0, 1), ArrowUp: () => move(-1, 0), ArrowDown: () => move(1, 0),
        Enter: () => press(keys[focus.r][focus.c].def),
        Escape: () => emit({type: 'backspace'}),
        ' ': () => mic(true)};  // the X button: talk while it is held (a space is typed with the touch key); Y (Tab) shows/hides the whole keyboard and SELECT (F2) is the selection mode: see attach()
      if (!map[e.key]) return false;
      if (e.repeat && (e.key === ' ' || e.key === 'Enter')) return true;  // holding is not typing
      map[e.key](); return true;
    }
    // released X, or A that pressed the Mic key
    function handleKeyUp(e) {
      if (e.key === 'Enter' && (altKeyTimer || altKeyShown)) {
        if (altKeyTimer) { clearTimeout(altKeyTimer); altKeyTimer = 0; press(keys[focus.r][focus.c].def); }   // a quick press
        else if (altPop) { altChoose(altPop.items[altPop.i].ch); }
        altKeyShown = false;
        return true;
      }
      if (!micOn || (e.key !== ' ' && e.key !== 'Enter')) return false;
      mic(false); return true;
    }
    function setLayer(name) { layer = name; shift = caps = false; draw(); }
    const stopMic = () => mic(false);

    draw();
    return {redraw: draw, handleKey, handleKeyUp, setLayer, stopMic, altOpen: () => !!altPop};
  }

  // ---- overlay: follows focus in the page --------------------------------------------------------
  function isEditable(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.isContentEditable) return true;
    if (el.tagName === 'TEXTAREA') return !el.readOnly && !el.disabled;
    if (el.tagName === 'INPUT') return !el.readOnly && !el.disabled && /^(text|search|email|url|tel|password|number)?$/i.test(el.type);
    return false;
  }
  const deepActive = () => { let a = document.activeElement; while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement; return a; };

  function attach() {
    if (window.__osk) return window.__osk;
    // Android: the text is typed with Android's own keyboard, which the owner switches to from the buttons' keyboard; this one is the device's
    if (/Android/.test(navigator.userAgent)) return null;
    const host = document.createElement('div');
    const sh = host.attachShadow({mode: 'closed'});
    sh.innerHTML = '<style>' + CSS + '</style><div id="wrap"><div id="kb"></div></div>';
    const wrap = sh.getElementById('wrap'), kbRoot = sh.getElementById('kb');
    const spacer = document.createElement('div');
    spacer.style.cssText = 'height:0;pointer-events:none;flex:none';
    let target = null, visible = false, softHidden = false;  // softHidden: put away with Y or Hide, the field keeps the focus

    // keep the focus in the page field when a keyboard key is pressed
    for (const ev of ['pointerdown', 'mousedown', 'touchstart']) wrap.addEventListener(ev, e => e.preventDefault());

    function exec(cmd, val) { return document.execCommand(cmd, false, val); }
    // voice input: the shell records while the mic is held, recognizes it (ai:stt + Caroline's checks) and the text goes into the field
    let voice = null;
    const post = url => fetch(url, {method: 'POST', body: '{}'});
    function startVoice() { if (voice || (!target && !remote)) return; voice = {el: target, remote}; post('/api/stt/start').catch(() => {}); }
    async function stopVoice() {
      if (!voice) return;
      const v = voice; voice = null;
      try {
        const r = await post('/api/stt/stop');
        if (!r.ok) return;  // the shell has already beeped and put the reason into the status bar
        const j = await r.json();
        if (j.text && v.remote) v.remote.send({type: 'char', text: j.text});
        else if (j.text) { if (deepActive() !== v.el) v.el.focus(); exec('insertText', j.text); }
      } catch (err) {}
    }

    // REMOTE: the keyboard is drawn here for a field of the external screen; what is pressed goes there (remote.send), nothing is typed here
    let remote = null;   // {send(edit), closed()} while this keyboard serves the big screen
    const kb = createKeyboard(kbRoot, {onKey: e => {
      if (remote) {
        if (e.type === 'mic') return e.on ? startVoice() : stopVoice();
        if (e.type === 'hide') { const r = remote; hideRemote(); r.closed(); return; }
        remote.send(e);
        if (e.type === 'enter') { const r = remote; hideRemote(); r.closed(); }
        return;
      }
      if (e.type === 'mic') return e.on ? startVoice() : stopVoice();
      applyEdit(target, e);
    }});
    // an edit (a key of a keyboard: this one, or the console's for a field of the external screen) into the field el
    function applyEdit(el, e) {
      if (!el) return;
      if (e.type === 'char') exec('insertText', e.text);
      else if (e.type === 'backspace') exec('delete');
      else if (e.type === 'hide') { hideSoft(); }
      else if (e.type === 'enter') {
        if (el.tagName === 'TEXTAREA') { exec('insertText', '\n'); return; }
        if (el.isContentEditable) { exec('insertParagraph'); return; }
        // the page may take Enter itself (a login form moving to the next field): then the keyboard stays as it is
        const down = new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true});
        down.__osk = true;  // our own event: the keyboard must not take it for a key of the device
        el.dispatchEvent(down);
        if (down.defaultPrevented) return;
        for (const type of ['keypress', 'keyup']) {
          const ev = new KeyboardEvent(type, {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true});
          ev.__osk = true;
          el.dispatchEvent(ev);
        }
        if (el.form) { if (el.form.requestSubmit) el.form.requestSubmit(); else el.form.submit(); }
        hide(true);
      }
    }

    function ensureVisible() {
      if (!target) return;
      const kbH = wrap.getBoundingClientRect().height;
      spacer.style.height = kbH + 'px';
      try { target.scrollIntoView({block: 'nearest', inline: 'nearest'}); } catch (e) {}
      const r = target.getBoundingClientRect(), avail = innerHeight - kbH - 6;
      if (r.bottom > avail) scrollBy(0, r.bottom - avail);
      else if (r.top < 6) scrollBy(0, r.top - 6);
    }
    // pages that lay themselves out (a full-screen editor) listen to this and shrink above the keyboard
    function announce() {
      const h = visible ? wrap.getBoundingClientRect().height : 0;
      window.dispatchEvent(new CustomEvent('osk', {detail: {height: h}}));
    }
    // the shell is told when a field of the console's screen gets the focus (the keyboard comes up) and when it loses it: while it has it, the PC's own keyboard (the console
    // window's) types into the field too, also after its first letter has put the soft keyboard away
    let typingSaid = false;
    function sayTyping(on) {
      if (on === typingSaid) return;
      typingSaid = on;
      try { navigator.sendBeacon((window.__icShell || '') + '/api/osk/typing', JSON.stringify({on})); } catch (e) {}
    }
    addEventListener('pagehide', () => sayTyping(false));
    function show(el) {
      target = el; softHidden = false;
      if (!visible) { visible = true; (document.body || document.documentElement).appendChild(host); (document.body || document.documentElement).appendChild(spacer); }
      sayTyping(true);
      kb.setLayer(/^(number|tel)$/i.test(el.type || '') ? 'symbols' : 'letters');
      requestAnimationFrame(() => { announce(); ensureVisible(); });
    }
    function hide(blur) {
      softHidden = false;
      sayTyping(false);   // also when the keyboard was put away (Y, the first letter of the PC's keyboard) and the field now loses the focus
      if (!visible) return;
      kb.stopMic(); selOff();
      if (blur && target && target.blur) target.blur();
      visible = false; target = null; host.remove(); spacer.remove();
      announce();
    }
    function showRemote(type, send, closed) {
      if (visible && !remote) hide(false);
      remote = {send, closed}; softHidden = false;
      if (!visible) { visible = true; (document.body || document.documentElement).appendChild(host); }
      kb.setLayer(/^(number|tel)$/i.test(type || '') ? 'symbols' : 'letters');
      requestAnimationFrame(announce);
    }
    function hideRemote() {
      if (!remote) return;
      remote = null;
      if (visible) { kb.stopMic(); visible = false; host.remove(); announce(); }
    }

    // Y / the Hide key: the keyboard goes away, the field keeps the focus, and the keys of the device are the page's again
    function hideSoft() {
      if (!visible) return;
      kb.stopMic();
      visible = false; softHidden = true; host.remove(); spacer.remove();
      announce();   // the field keeps the focus: the PC's keyboard still types into it
    }

    // ---- selection mode: SELECT turns it on and off in the focused field, the arrows move the end of the selection, A opens the menu
    // Copy / Cut / Paste / Select All (left/right choose, A runs, B closes). The clipboard is the device's one (shelld, /api/clipboard/osk, gated by a recent touch or button);
    // window.__icClipShell is the shell's origin for a page of another site (the device browser). Without the shell the text stays in this page.
    const SEL_ITEMS = ['Copy', 'Cut', 'Paste', 'Select All'];
    const selHost = document.createElement('div'), selSh = selHost.attachShadow({mode: 'closed'});
    selSh.innerHTML = '<style>:host{all:initial}#bar{position:fixed;left:0;right:0;z-index:2147483647;display:flex;gap:4px;padding:4px;background:#111;direction:ltr;' +
      'font:18px "DejaVu Sans",sans-serif;color:#fff;-webkit-user-select:none;user-select:none;touch-action:none}' +
      '.it{flex:1;padding:10px 4px;text-align:center;border-radius:5px;background:#2d3640}.it.focus{background:#f2a900;color:#000;outline:3px solid #fff;outline-offset:-3px}' +
      '.tag{padding:10px 10px;border-radius:5px;background:#1d242b;color:#f2a900;font-weight:bold}</style><div id="bar"></div>';
    const selBar = selSh.getElementById('bar');
    for (const ev of ['pointerdown', 'mousedown', 'touchstart']) selBar.addEventListener(ev, e => e.preventDefault());
    let sel = null;   // {el, a, f, menu, i}: the anchor and the moving end of the selection
    const swallow = new Set();   // keys whose keydown the selection mode took: their keyup is not the page's either
    let localClip = '';
    const clipBase = () => window.__icClipShell || '';
    async function clipSet(text) { localClip = text; try { await fetch(clipBase() + '/api/clipboard/osk', {method: 'POST', body: text}); } catch (e) {} }
    async function clipGet() { try { const r = await fetch(clipBase() + '/api/clipboard/osk'); if (r.ok) return (await r.json()).text || ''; } catch (e) {} return localClip; }
    const textField = el => (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') && typeof el.selectionStart === 'number';
    const selText = el => textField(el) ? el.value.slice(el.selectionStart, el.selectionEnd) : String(getSelection());
    function selApply() { const s = sel; if (textField(s.el)) s.el.setSelectionRange(Math.min(s.a, s.f), Math.max(s.a, s.f), s.f < s.a ? 'backward' : 'forward'); }
    function selDraw() {
      if (!sel) return;
      selBar.style.bottom = (visible ? wrap.getBoundingClientRect().height : 0) + 'px';
      selBar.innerHTML = '';
      const tag = document.createElement('div'); tag.className = 'tag'; tag.textContent = tr('Select'); selBar.appendChild(tag);
      SEL_ITEMS.forEach((name, i) => {
        const it = document.createElement('div'); it.className = 'it' + (sel.menu && i === sel.i ? ' focus' : ''); it.textContent = tr(name);
        it.addEventListener('pointerup', ev => { ev.preventDefault(); selRun(name); });
        selBar.appendChild(it);
      });
    }
    function selOn() {
      const el = deepActive();
      if (!isEditable(el)) return false;
      const t = textField(el);
      sel = {el, a: t ? el.selectionStart : 0, f: t ? el.selectionEnd : 0, menu: false, i: 0};
      (document.body || document.documentElement).appendChild(selHost);
      selDraw(); requestAnimationFrame(selDraw);
      return true;
    }
    function selOff() { if (!sel) return; sel = null; selHost.remove(); }
    function selMove(dx, dy) {
      const s = sel, el = s.el;
      if (textField(el)) {
        const v = el.value;
        if (dx) s.f = Math.max(0, Math.min(v.length, s.f + dx));
        else if (el.tagName === 'INPUT') s.f = dy < 0 ? 0 : v.length;
        else {   // a textarea: the line above or below, in the same column
          const ls = s.f ? v.lastIndexOf('\n', s.f - 1) + 1 : 0, col = s.f - ls;
          if (dy < 0) { if (!ls) s.f = 0; else { const pe = ls - 1, ps = pe ? v.lastIndexOf('\n', pe - 1) + 1 : 0; s.f = Math.min(ps + col, pe); } }
          else { const nl = v.indexOf('\n', s.f); if (nl < 0) s.f = v.length; else { const ns = nl + 1, ne = v.indexOf('\n', ns); s.f = Math.min(ns + col, ne < 0 ? v.length : ne); } }
        }
        selApply();
      } else if (el.isContentEditable) {
        try { getSelection().modify('extend', (dx || dy) < 0 ? 'backward' : 'forward', dx ? 'character' : 'line'); } catch (e) {}
      }
    }
    async function selRun(name) {
      const s = sel; if (!s) return;
      const el = s.el;
      if (name === 'Select All') {
        if (textField(el)) { s.a = 0; s.f = el.value.length; selApply(); } else if (el.isContentEditable) exec('selectAll');
        s.menu = false; selDraw(); return;
      }
      if (name === 'Copy' || name === 'Cut') {
        const t = selText(el);
        if (el.type === 'password' || !t) { s.menu = false; selDraw(); return; }   // nothing selected; a password never goes to the clipboard
        await clipSet(t);
        if (name === 'Cut') { if (deepActive() !== el) el.focus(); exec('delete'); }
      } else {
        const t = await clipGet();
        if (t) { if (deepActive() !== el) el.focus(); if (textField(el)) selApply(); exec('insertText', t); }
      }
      if (sel === s) selOff();
    }
    // a key of the device while the mode is on; true when it was taken
    function selKey(e) {
      const k = e.key;
      if (k === 'F2') { if (!e.repeat) selOff(); return true; }
      if (sel.menu) {
        if (k === 'ArrowLeft') sel.i = (sel.i + SEL_ITEMS.length - 1) % SEL_ITEMS.length;
        else if (k === 'ArrowRight') sel.i = (sel.i + 1) % SEL_ITEMS.length;
        else if (k === 'Enter') { if (!e.repeat) selRun(SEL_ITEMS[sel.i]); }
        else if (k === 'Escape') { if (!e.repeat) sel.menu = false; }
        else if (k !== 'ArrowUp' && k !== 'ArrowDown') return false;
        selDraw(); return true;
      }
      if (k === 'ArrowLeft') selMove(-1, 0);
      else if (k === 'ArrowRight') selMove(1, 0);
      else if (k === 'ArrowUp') selMove(0, -1);
      else if (k === 'ArrowDown') selMove(0, 1);
      else if (k === 'Enter') { if (!e.repeat) { sel.menu = true; sel.i = 0; } }
      else if (k === 'Escape') { if (!e.repeat) selOff(); }
      else return false;
      selDraw(); return true;
    }

    if (window.__icExternal) return window.__osk = attachExternal();
    addEventListener('focusin', e => { const el = e.composedPath()[0]; if (isEditable(el)) show(el); }, true);
    addEventListener('focusout', () => setTimeout(() => { const a = deepActive(); if (!isEditable(a)) hide(false); else target = a; }, 60), true);
    addEventListener('resize', () => { if (visible) ensureVisible(); });
    // while open, the keyboard owns the D-pad and the buttons
    addEventListener('keydown', e => {
      if (e.__osk) return;
      if (sel) {   // the selection mode owns the D-pad; a letter of a real keyboard ends it (the field is typed into)
        if (e.key.length === 1 && e.key !== ' ' && !e.ctrlKey && !e.altKey && !e.metaKey) selOff();
        else if (selKey(e)) { swallow.add(e.key); e.preventDefault(); e.stopImmediatePropagation(); return; }
      } else if (e.key === 'F2' && !e.repeat && (visible || softHidden) && isEditable(deepActive()) && selOn()) { swallow.add('F2'); e.preventDefault(); e.stopImmediatePropagation(); return; }
      // a real keyboard (the PC's, typing into the simulator; a USB one): the device's buttons never give a letter, so the first one puts the soft keyboard away (Y brings it back)
      // and from then on Enter, space and Escape are the field's, not the soft keyboard's A, X and B
      if (visible && e.key.length === 1 && e.key !== ' ' && !e.ctrlKey && !e.altKey && !e.metaKey) { hideSoft(); return; }
      if (e.key === 'Tab' && (visible || (softHidden && isEditable(deepActive())))) {  // Y: show / hide the keyboard
        e.preventDefault(); e.stopImmediatePropagation();
        if (!e.repeat) { if (visible) hideSoft(); else show(deepActive()); }
        return;
      }
      if (visible && e.key === 'Escape' && !kb.altOpen() && target && !(target.isContentEditable ? target.textContent : target.value)) {
        if (e.repeat) { e.preventDefault(); e.stopImmediatePropagation(); }   // a held B that has erased everything stops there
        return;                                                                 // a fresh B on an empty field: the page's own B
      }
      if (visible && kb.handleKey(e)) { e.preventDefault(); e.stopImmediatePropagation(); }
    }, true);
    addEventListener('keyup', e => { if (!e.__osk && swallow.delete(e.key)) { e.preventDefault(); e.stopImmediatePropagation(); return; } if (!e.__osk && visible && kb.handleKeyUp(e)) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);
    // the field may already be focused when the script loads
    const a = deepActive(); if (isEditable(a)) show(a);

    return window.__osk = {show, hide, hideSoft, showRemote, hideRemote};

    // A page of the EXTERNAL screen (docs/system-api.md): the keyboard is never drawn on the big screen, only on the console's. This page tells the shell that a field of
    // its own has the focus (or no longer has it) over the shell's /oskws channel, and puts in what the console's keyboard sends back. window.__icShell: the shell's origin for a page
    // of another site (the browser's window on the big screen); the pages of our apps are of the shell's origin.
    function attachExternal() {
      const shell = (window.__icShell || location.origin).replace(/^http/, 'ws');
      let ws = null, field = null, shown = false;
      const say = m => { try { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); } catch (e) {} };
      (function connect() {
        try { ws = new WebSocket(shell + '/oskws'); } catch (e) { return; }
        ws.onmessage = ev => {
          let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
          if (m.t !== 'edit' || !m.e) return;
          const el = isEditable(deepActive()) ? deepActive() : field;
          if (!el) return;
          if (deepActive() !== el) el.focus();
          target = el;
          applyEdit(el, m.e);
          if (m.e.type === 'enter') shown = false;
        };
        ws.onopen = () => { const a = deepActive(); if (isEditable(a)) { field = a; shown = true; say({t: 'focus', type: a.type || ''}); } };
        ws.onclose = () => setTimeout(connect, 1000);
      })();
      addEventListener('focusin', e => { const el = e.composedPath()[0]; if (isEditable(el)) { field = el; shown = true; say({t: 'focus', type: el.type || ''}); } }, true);
      addEventListener('focusout', () => setTimeout(() => { const a = deepActive(); if (isEditable(a)) { field = a; return; } if (shown) { shown = false; field = null; say({t: 'blur'}); } }, 60), true);
      addEventListener('pagehide', () => { if (shown) say({t: 'blur'}); });
      return {show() {}, hide() {}, hideSoft() {}, showRemote() {}, hideRemote() {}};
    }
  }

  window.OSK = {attach, createKeyboard};
})();
