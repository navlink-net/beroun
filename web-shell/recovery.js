// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
// The device's screen while an image is flashed (runtime/flashd.py): the states of /api/flash drawn over everything, as a recovery screen would, with the
// firmware versions (from -> to) and the progress. Also: after a start of a new system this page tells the shell the launcher is up (POST /api/boot/confirm),
// and a notice that the system was rolled back (a new system that did not start) is shown until B.
// Only B (Escape), or a touch, is taken while the screen is up. Nothing else reaches the launcher.
(() => {
  const el = document.createElement('div');
  el.id = 'recovery';
  el.style.cssText = 'position:fixed;inset:0;z-index:2147483647;display:none;flex-direction:column;align-items:center;justify-content:center;' +
    'background:#101820;color:#fff;font:20px/1.35 sans-serif;text-align:center;padding:20px;box-sizing:border-box';
  el.innerHTML = '<div id="rc-title" style="font-size:32px;font-weight:700;margin-bottom:10px"></div>' +
    '<div id="rc-ver" style="font-size:24px;font-weight:600;color:#9fd3ff;margin-bottom:6px"></div>' +
    '<div id="rc-text" style="max-width:580px"></div>' +
    '<div id="rc-bar" style="position:relative;width:480px;max-width:92%;height:30px;border:2px solid #fff;border-radius:6px;margin-top:20px;overflow:hidden">' +
    '<div id="rc-fill" style="height:100%;width:0;background:#3ba55c"></div>' +
    '<div id="rc-pct" style="position:absolute;inset:0;font:700 20px/30px sans-serif;text-shadow:0 0 3px #000"></div></div>' +
    '<div id="rc-hint" style="margin-top:22px;font-size:17px;opacity:.75"></div>';
  const $ = id => el.querySelector('#' + id);
  let state = 'idle', shown = '';
  const other = a => a === 'a' ? 'B' : 'A';   // the slot that is written is the one that is not running
  const ver = s => s.to ? 'Firmware ' + s.from + '  →  ' + s.to + (s.to_build ? ' (' + s.to_build + ')' : '') : '';

  function draw(s) {
    state = s.state;
    const notice = s.state === 'idle' && s.notice;
    if (s.state === 'idle' && !notice) { el.style.display = 'none'; shown = ''; return; }
    el.style.display = 'flex';
    const fill = $('rc-fill'), bar = $('rc-bar'), hint = $('rc-hint'), pct = Math.round((s.progress || 0) * 100);
    bar.style.display = 'none'; hint.textContent = ''; fill.style.background = '#3ba55c'; $('rc-ver').textContent = ver(s);
    if (notice) {
      state = 'notice'; $('rc-title').textContent = 'Update failed'; $('rc-ver').textContent = ''; $('rc-text').textContent = s.notice; hint.textContent = 'B: OK';
    } else if (s.state === 'recovery') {
      $('rc-title').textContent = 'Upgrade is ongoing...'; $('rc-text').textContent = 'Restarting into recovery. USB connected to the PC.';
    } else if (s.state === 'verifying') {
      $('rc-title').textContent = 'Upgrade is ongoing...'; $('rc-text').textContent = 'Checking the firmware: signature, decryption' + (s.encrypted ? ' (encrypted)' : '');
      bar.style.display = 'block'; fill.style.width = pct + '%'; $('rc-pct').textContent = pct + '%';
    } else if (s.state === 'writing') {
      $('rc-title').textContent = 'Upgrade is ongoing...'; $('rc-text').textContent = 'Writing slot ' + other(s.active);
      bar.style.display = 'block'; fill.style.width = pct + '%'; $('rc-pct').textContent = pct + '%'; hint.textContent = 'Do not disconnect the cable';
    } else if (s.state === 'done') {
      $('rc-title').textContent = 'Upgrade complete'; $('rc-text').textContent = s.reason; bar.style.display = 'block'; fill.style.width = '100%'; $('rc-pct').textContent = '100%';
      hint.textContent = 'Restarting…';
    } else if (s.state === 'failed') {
      $('rc-title').textContent = 'Upgrade failed'; $('rc-text').textContent = s.reason; bar.style.display = 'block'; fill.style.width = '100%'; $('rc-pct').textContent = '';
      fill.style.background = '#d04040'; hint.textContent = 'The system was not changed. B: back';
    }
  }

  async function poll() {
    try { const r = await fetch('/api/flash'); if (r.ok) draw(await r.json()); } catch (e) { /* the shell is restarting */ }
  }
  const dismiss = () => { if (state === 'failed' || state === 'notice') fetch('/api/flash/dismiss', {method: 'POST', body: '{}'}).then(poll).catch(() => {}); };
  window.addEventListener('keydown', e => {
    if (el.style.display === 'none') return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (e.key === 'Escape' || e.key === 'Backspace' || e.key === 'GoBack') dismiss();
  }, true);
  el.addEventListener('pointerdown', dismiss);
  const mount = () => {
    document.body.appendChild(el); poll(); setInterval(poll, 600);
    // the launcher has been drawn and has stayed up for a while: a new system on trial may be confirmed (it does not, if this page never gets here)
    setTimeout(() => { fetch('/api/boot/confirm', {method: 'POST', body: '{}'}).catch(() => {}); }, 8000);
  };
  if (document.body) mount(); else addEventListener('DOMContentLoaded', mount);
})();
