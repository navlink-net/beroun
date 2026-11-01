// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
// OS Beroun 3D helper: what a Babylon.js app needs on a console that has the cursor, A (Enter) and B (Escape) and no mouse.
//   <script src="/platform.js"></script> <script src="/lib/babylon/babylon.js"></script> <script src="/lib/beroun-3d.js"></script>
//
//   Beroun3D.engine(canvas, opts)         a BABYLON.Engine for the console's screen: devicePixelRatio ignored (the window is 640x480 at 1), no antialiasing, low-power GPU; resizes with the window.
//                                         opts: {antialias, adaptToDeviceRatio, stencil, preserveDrawingBuffer} (all off by default), passed to Babylon as they are.
//   Beroun3D.camera(scene, kind, opts)    a camera driven by the cursor keys alone; Babylon's own keyboard, mouse and touch inputs are removed from it. kind:
//        "orbit"  turns around opts.target (Vector3, default the origin): Left/Right turn it, Up/Down tilt it; opts.radius (default 8), opts.beta (default 1.1 rad)
//        "walk"   at opts.position (Vector3, default (0, 1.7, -6)): Up/Down walk forward/back along the ground, Left/Right turn; opts.speed (units/s, default 3), opts.turn (rad/s, default 1.6)
//        "orbit" also takes opts.turn (rad/s, default 1.6). The key repeat of the keyboard is not used: a held key moves the camera by time, so the speed does not depend on the frame rate.
//   Beroun3D.onAction(fn)                 A (Enter) was pressed: fn(event); returns a function that removes it. B (Escape) is the app's own (a menu, Platform.exit()): the helper leaves it alone.
//   Beroun3D.keys                         {up, down, left, right}: true while held, for an app that moves things itself.
//
// The helper handles keys in the capture phase and stops the D-pad from scrolling the page while a camera of it exists; with no camera the keys stay the page's.
(function () {
  const KEYS = {ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right'};
  const keys = {up: false, down: false, left: false, right: false};
  const actions = new Set();
  let users = 0;   // cameras alive: while there is one the D-pad belongs to the scene

  const editing = e => { const t = e.composedPath()[0]; return t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)); };
  addEventListener('keydown', e => {
    if (editing(e)) return;
    const k = KEYS[e.key];
    if (k) { keys[k] = true; if (users) e.preventDefault(); return; }
    if (e.key === 'Enter' && !e.repeat) actions.forEach(fn => { try { fn(e); } catch (err) { console.error('Beroun3D action failed', err); } });
  }, true);
  addEventListener('keyup', e => { const k = KEYS[e.key]; if (k) { keys[k] = false; if (users) e.preventDefault(); } }, true);
  addEventListener('blur', () => { for (const k in keys) keys[k] = false; });

  function engine(canvas, opts) {
    const o = Object.assign({antialias: false, adaptToDeviceRatio: false, stencil: false, preserveDrawingBuffer: false, powerPreference: 'low-power'}, opts || {});
    const eng = new BABYLON.Engine(canvas, o.antialias, o, o.adaptToDeviceRatio);
    addEventListener('resize', () => eng.resize());
    return eng;
  }

  function camera(scene, kind, opts) {
    const o = opts || {};
    const V = BABYLON.Vector3;
    let cam;
    if (kind === 'orbit') {
      cam = new BABYLON.ArcRotateCamera('beroun-orbit', Math.PI / 4, o.beta == null ? 1.1 : o.beta, o.radius == null ? 8 : o.radius, o.target || V.Zero(), scene);
      cam.lowerBetaLimit = 0.1; cam.upperBetaLimit = Math.PI / 2 - 0.05;
    } else if (kind === 'walk') {
      cam = new BABYLON.UniversalCamera('beroun-walk', o.position || new V(0, 1.7, -6), scene);
      cam.setTarget(cam.position.add(new V(0, 0, 1)));
    } else {
      throw new Error('Beroun3D.camera: kind is "orbit" or "walk"');
    }
    cam.inputs.clear();   // no mouse, touch, keyboard or gamepad input of Babylon's own
    const turn = o.turn || 1.6, speed = o.speed || 3;
    const step = scene.onBeforeRenderObservable.add(() => {
      const dt = Math.min(scene.getEngine().getDeltaTime(), 100) / 1000;   // a stall (a hidden page, a slow frame) is not a jump
      const lr = (keys.right ? 1 : 0) - (keys.left ? 1 : 0), ud = (keys.up ? 1 : 0) - (keys.down ? 1 : 0);
      if (kind === 'orbit') {
        cam.alpha -= lr * turn * dt;
        cam.beta = Math.min(cam.upperBetaLimit, Math.max(cam.lowerBetaLimit, cam.beta - ud * turn * dt));
      } else {
        const yaw = lr * turn * dt;
        if (yaw) { const r = cam.rotation; cam.rotation = new V(r.x, r.y + yaw, r.z); }
        if (ud) {
          const f = cam.getDirection(V.Forward()); f.y = 0; f.normalize();
          cam.position.addInPlace(f.scale(ud * speed * dt));
        }
      }
    });
    users++;
    cam.onDisposeObservable.add(() => { scene.onBeforeRenderObservable.remove(step); users--; });
    if (!scene.activeCamera) scene.activeCamera = cam;
    return cam;
  }

  function onAction(fn) { actions.add(fn); return () => actions.delete(fn); }

  window.Beroun3D = {engine, camera, onAction, keys};
})();
