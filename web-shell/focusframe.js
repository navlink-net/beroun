// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
// The focus frame of the two screens (docs/system-api.md): while there is an external screen, a thin amber frame runs along the edge of the screen that has the focus
// (SELECT moves it), and flashes when the focus comes to it. Every page of OS Beroun loads this (platform.js, the launcher, the splash with the cat); a page of the external
// screen knows itself by "?ext=1" in its address (or by being the splash). It also hides the pointer of the external screen when it has not moved for a while.
(function () {
  if (window.__focusFrame) return;
  window.__focusFrame = true;
  const ext = /[?&]ext=1\b/.test(location.search) || location.pathname.indexOf('/system/ext-cat/') === 0;
  const style = document.createElement('style');
  style.textContent = '#__ff{position:fixed;inset:0;box-sizing:border-box;border:4px solid #f2a900;pointer-events:none;z-index:2147483646;display:none}' +
    '#__ff.on{display:block}#__ff.flash{animation:__fff .7s ease-out}' +
    '@keyframes __fff{0%{box-shadow:inset 0 0 0 14px rgba(242,169,0,.55)}100%{box-shadow:inset 0 0 0 0 rgba(242,169,0,0)}}' +
    'html.__nocursor,html.__nocursor *{cursor:none!important}';
  const el = document.createElement('div');
  el.id = '__ff';
  (document.head || document.documentElement).appendChild(style);
  const attach = () => document.body ? document.body.appendChild(el) : addEventListener('DOMContentLoaded', () => document.body.appendChild(el));
  attach();

  let mine = null;
  async function tick() {
    let st = null;
    try { st = await (await fetch('/api/display', {cache: 'no-store'})).json(); } catch (e) { return; }
    const now = !!(st.connected && st.focus === (ext ? 'external' : 'console'));
    if (now !== mine) {
      el.classList.toggle('on', now);
      if (now && mine !== null) { el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); }
      mine = now;
    }
  }
  setInterval(tick, 400); tick();

  if (ext) {   // the pointer is only there while it is used
    let t = null;
    const wake = () => { document.documentElement.classList.remove('__nocursor'); clearTimeout(t); t = setTimeout(() => document.documentElement.classList.add('__nocursor'), 4000); };
    addEventListener('mousemove', wake, true); addEventListener('mousedown', wake, true); wake();

    // The pointer of the external screen (the sticks move it, the picture of the screen has no mouse cursor of its own): a small arrow that follows the mouse events and hides after 4 s of rest.
    (function () {
      if (window.__icPointer) return;
      window.__icPointer = true;
      const p = document.createElement('div');
      p.id = '__icptr';
      p.style.cssText = 'position:fixed;left:0;top:0;width:22px;height:32px;pointer-events:none;z-index:2147483647;display:none;filter:drop-shadow(0 0 2px #000)';
      p.innerHTML = '<svg width="22" height="32" viewBox="0 0 22 32"><path d="M2 2 L2 25 L8 19.5 L12.5 29.5 L16.5 27.7 L12 18 L20 18 Z" fill="#fff" stroke="#000" stroke-width="2" stroke-linejoin="round"/></svg>';
      const put = () => (document.body || document.documentElement).appendChild(p);
      if (document.body) put(); else addEventListener('DOMContentLoaded', put);
      let t = null;
      const move = e => {
        if (!p.isConnected) put();
        p.style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px)'; p.style.display = 'block';
        clearTimeout(t); t = setTimeout(() => { p.style.display = 'none'; }, 4000);
      };
      addEventListener('mousemove', move, true); addEventListener('mousedown', move, true);
    })();
  }
})();
