// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
// Injected by the browser host into every page it shows (together with osk.js).
// The simulator's touchscreen arrives as a mouse, and WPE does not pan on a mouse drag: scroll the page by hand.
// (A real touchscreen sends touch events that WebKit pans on its own.)
(function () {
  if (window.__icInject) return;
  window.__icInject = true;
  if (window.__icExternal) {
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
  let d = null, dragged = false;
  const editable = el => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  function scroller(el) {
    for (let e = el; e && e !== document.body && e !== document.documentElement; e = e.parentElement) {
      const s = getComputedStyle(e);
      if (/(auto|scroll)/.test(s.overflowY + s.overflowX) && (e.scrollHeight > e.clientHeight || e.scrollWidth > e.clientWidth)) return e;
    }
    return document.scrollingElement || document.documentElement;
  }
  addEventListener('pointerdown', e => {
    if (e.pointerType !== 'mouse' || e.button !== 0 || editable(e.target)) return;
    const sc = scroller(e.target);
    d = {x: e.clientX, y: e.clientY, sc, sl: sc.scrollLeft, st: sc.scrollTop}; dragged = false;
  }, true);
  addEventListener('pointermove', e => {
    if (!d) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (!dragged && Math.hypot(dx, dy) > 8) dragged = true;
    if (dragged) { d.sc.scrollLeft = d.sl - dx; d.sc.scrollTop = d.st - dy; e.preventDefault(); }
  }, true);
  const end = () => { d = null; };
  addEventListener('pointerup', end, true);
  addEventListener('pointercancel', end, true);
  addEventListener('click', e => { if (dragged) { e.preventDefault(); e.stopPropagation(); dragged = false; } }, true);
})();

// B is Escape on the device. A page that handles Escape (a dialog, its own menu) keeps it; when it does not, the browser's way out: collapse what is expanded (a menu button with
// aria-expanded="true"), else close an open <dialog>, else let go of the field or button that has the focus.
(function () {
  addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    setTimeout(() => {
      if (e.defaultPrevented) return;                  // the page took it
      const open = Array.from(document.querySelectorAll('[aria-expanded="true"]')).filter(b => b.offsetParent !== null || getComputedStyle(b).position === 'fixed');
      if (open.length) { open.forEach(b => b.click()); return; }
      const dlg = document.querySelector('dialog[open]');
      if (dlg) { dlg.close(); return; }
      const a = document.activeElement;
      if (a && a !== document.body && a.blur) a.blur();
    }, 0);
  });
})();

// A thin scrollbar on the pages of the browser too: it shows where the caret of a long page is.
(function () {
  const st = document.createElement('style');
  st.textContent = '::-webkit-scrollbar{width:6px;height:6px}::-webkit-scrollbar-track{background:rgba(128,128,128,.25)}::-webkit-scrollbar-thumb{background:#f2a900;border-radius:3px}';
  (document.head || document.documentElement).appendChild(st);
})();

// osk.js (the bundle with the layouts) is injected before this script: start the keyboard.
(function () { if (window.OSK && OSK.attach) OSK.attach(); })();
