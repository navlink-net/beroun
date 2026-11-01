// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
// GameExt: a single-player game that moves to the external (HDMI) screen by itself (docs/system-api.md).
//
//   <script src="/platform.js"></script><script src="/gameext.js"></script>
//   GameExt.init({id: 'pirates', title: 'Pirates', splash: 'assets/splash.webp', text: 'Playing on the big screen',
//                 snapshot: () => state object (what the game keeps), restore: state => the game goes on from it,
//                 save: () => the game saves its own Platform.state (the old onClose), ready: promise of the game's first start})
//   GameExt.away()      true while the game runs on the other screen: the console page draws nothing (the splash is up)
//   GameExt.tools({name: async args => text})   Caroline's tools of the game (the manifest's "caroline": {"tools": [...]}); she calls the console page, and while the game is on the big
//                       screen the console page hands the call on to the big page (display messages) and gives its answer back: the same tools work wherever the game runs
//
// The manifest says "external": {"entry": "index.html", "state": true, "input": "game"}: the page is the same file on both screens, the address of the big one has ?ext=1.
// One game, one place: with a screen connected the game is on it, the console shows the splash (a picture, the game's name) and nothing runs behind it. The cable plugged in
// during a game moves the game over at that place (paused: A goes on); pulled out, or another app takes the big screen, it comes back the same way.
// The place travels in the app's db under "handoff" ({ts, state}); whichever page leaves writes it (the big one in onSuspend), the other one reads it. A game on the big screen owns the
// sticks, X and Y (the sticks are the gamepad, X and Y are space and Tab): no mouse there (the owner, 2026-10-28).
window.GameExt = (function () {
  const P = window.Platform;
  const isBig = /[?&]ext=1(&|$)/.test(location.search);
  const KEY = 'handoff';
  let o = null, isAway = false, opening = false, splash = null, leftAt = 0;
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  async function readHandoff() { try { const v = await P.db.get(KEY); return v ? JSON.parse(v) : null; } catch (e) { return null; } }
  async function writeHandoff() {
    try { o.save(); } catch (e) {}
    try { await P.db.set(KEY, JSON.stringify({ts: Date.now(), from: isBig ? 'big' : 'console', state: o.snapshot()})); } catch (e) {}
  }

  function buildSplash() {
    const d = document.createElement('div');
    d.style.cssText = 'position:fixed;inset:0;z-index:2147483000;background:#000 url("' + o.splash + '") center/cover no-repeat;display:none;font-family:"DejaVu Sans",sans-serif;color:#fff;-webkit-user-select:none;user-select:none';
    d.innerHTML = '<div style="position:absolute;left:0;right:0;bottom:0;padding:44px 24px 22px;text-align:center;background:linear-gradient(0deg,rgba(0,0,0,.82),rgba(0,0,0,0))">' +
      '<div style="font:900 34px/1.1 Nunito,\'DejaVu Sans\',sans-serif;letter-spacing:.02em;text-shadow:0 2px 8px #000"></div>' +
      '<div style="margin-top:8px;font-size:17px;color:#f2e2b6;text-shadow:0 1px 4px #000"></div></div>';
    d.firstChild.children[0].textContent = o.title;
    d.firstChild.children[1].textContent = o.text;
    document.body.appendChild(d);
    return d;
  }
  const showSplash = () => { if (!splash) splash = buildSplash(); splash.style.display = 'block'; };
  const hideSplash = () => { if (splash) splash.style.display = 'none'; };

  // on the console, while the game is away, B leaves the app (the top level of an app: B is Exit); nothing else reaches the game under the splash
  addEventListener('keydown', e => {
    if (isBig || !isAway) return;
    e.stopImmediatePropagation(); e.preventDefault();
    if (e.key === 'Escape' && !e.repeat) P.exit();
  }, true);

  // the game goes to the big screen
  async function goBig(open) {
    if (isAway || isBig) return;
    isAway = true; opening = open; leftAt = Date.now();
    await writeHandoff();
    showSplash();
    if (!open) return;
    try { await P.display.open({}); }
    catch (e) { isAway = false; hideSplash(); }    // no screen after all: the game stays here
    finally { opening = false; }
  }

  // the game comes back to the console at the place the big page left it
  async function comeBack() {
    if (!isAway) return;
    let h = null;
    for (let i = 0; i < 12; i++) {                      // the big page writes it while it is covered or unplugged: wait for it a moment
      h = await readHandoff();
      if (h && h.from === 'big' && h.ts >= leftAt) break;
      await sleep(150);
    }
    isAway = false;
    if (h && h.from === 'big' && h.ts >= leftAt) { try { o.restore(h.state); } catch (e) {} }
    hideSplash();
  }

  async function start() {
    const h = await readHandoff();
    if (h && h.state !== undefined) { try { o.restore(h.state); } catch (e) {} }   // whichever page left last has the newest place
    if (isBig || !P.display) return;
    try { const s = await P.display.status(); if (s.connected) await goBig(true); } catch (e) {}
  }

  function init(opts) {
    o = opts;
    if (!P || !P.display || !P.db) { P && P.onClose && P.onClose(opts.save); return; }   // a plain browser: the game just runs
    const leave = async () => { if (isBig || !isAway) await writeHandoff(); };
    P.onClose(leave);
    if (P.onSuspend) P.onSuspend(leave);
    P.display.on(e => {
      if (isBig) return;
      if (e.event === 'connected' && !isAway) goBig(true);
      else if (e.event === 'disconnected' && isAway) comeBack();
      else if (isAway && !opening && e.top !== o.id) comeBack();                    // another app took the big screen
      else if (!isAway && e.top === o.id) goBig(false);                              // somebody (Caroline) opened the big page: the game is there
    });
    Promise.resolve(o.ready).then(start, start);
  }

  // Caroline's tools: the page that runs the game answers; the console page forwards to the big one while the game is away
  const waits = {};
  let seq = 0;
  function tools(map) {
    if (!P || !P.tools) return;
    const run = async (name, args) => { try { return String(await map[name](args || {})); } catch (e) { return 'Error: ' + (e && e.message || e); } };
    for (const name of Object.keys(map)) {
      P.tools.handle(name, args => {
        if (isBig || !isAway) return run(name, args);
        return new Promise(resolve => {
          const id = ++seq;
          waits[id] = resolve;
          P.display.send({t: 'tool', id, name, args: args || {}}).catch(() => {});
          setTimeout(() => { if (waits[id]) { delete waits[id]; resolve('the page on the big screen did not answer'); } }, 8000);
        });
      });
    }
    if (P.display && P.display.onMessage) P.display.onMessage(async m => {
      if (!m) return;
      if (m.t === 'tool' && isBig && map[m.name]) P.display.send({t: 'tool_result', id: m.id, text: await run(m.name, m.args)}).catch(() => {});
      else if (m.t === 'tool_result' && waits[m.id]) { const r = waits[m.id]; delete waits[m.id]; r(m.text); }
    });
  }

  return {init, tools, away: () => isAway, big: isBig};
})();
