// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
// Quidditch, single player: the simulation (no DOM, no timers; the page steps it, tests/quidditch_sim_test.js steps it in Node).
// Made from the AI and the ball physics of the gamepool game (D:\REPO\gamepool\Quidditch\game.js), with what a human player needs:
//   * the actions of every role are methods the AI and the human both use (grab, throw, steal, hit, dive, catch the snitch);
//   * a seeded random generator, a state that is plain data (snapshot / restore: the app returns to the same place);
//   * the pace and the difficulty: the speeds, how accurate the throws are, how well the opponents' keepers save and their seeker catches the snitch;
//   * the snitch gets tired in short windows (every 14-26 s, 3 s): that is when a seeker can catch it, so a match ends.
// Coordinates (game units): x 0..500 along the pitch (team A attacks to the right, hoops at x=475; team B to the left, hoops at x=25), z 0..180 across, y 5..55 up.
(function (root) {
  'use strict';

  const FIELD = {xMin: 0, xMax: 500, yMin: 5, yMax: 55, zMin: 0, zMax: 180};
  const HOOPS = [
    {x: 25,  z: 75,  y: 15, defTeam: 'A', scorTeam: 'B'}, {x: 25,  z: 90, y: 25, defTeam: 'A', scorTeam: 'B'}, {x: 25,  z: 105, y: 35, defTeam: 'A', scorTeam: 'B'},
    {x: 475, z: 75,  y: 15, defTeam: 'B', scorTeam: 'A'}, {x: 475, z: 90, y: 25, defTeam: 'B', scorTeam: 'A'}, {x: 475, z: 105, y: 35, defTeam: 'B', scorTeam: 'A'},
  ];
  // the seven of a team, in the order of the starting positions (the roster of the gamepool lobby)
  const ROSTER = {
    A: [
      {file: 'gryf_keeper',  name: 'Ron Weasley',        role: 'keeper', x: 25,  z: 90,  y: 25},
      {file: 'gryf_chaser1', name: 'Hermione Granger',   role: 'chaser', x: 180, z: 75,  y: 20},
      {file: 'gryf_chaser2', name: 'Ginny Weasley',      role: 'chaser', x: 180, z: 90,  y: 20},
      {file: 'gryf_chaser3', name: 'Luna Lovegood',      role: 'chaser', x: 180, z: 105, y: 20},
      {file: 'gryf_beater1', name: 'Neville Longbottom', role: 'beater', x: 200, z: 70,  y: 30},
      {file: 'gryf_beater2', name: 'Lavender Brown',     role: 'beater', x: 200, z: 110, y: 30},
      {file: 'gryf_seeker',  name: 'Harry Potter',       role: 'seeker', x: 250, z: 90,  y: 50},
    ],
    B: [
      {file: 'slyt_keeper',  name: 'Daphne Greengrass',   role: 'keeper', x: 475, z: 90,  y: 25},
      {file: 'slyt_chaser1', name: 'Blaise Zabini',       role: 'chaser', x: 320, z: 75,  y: 20},
      {file: 'slyt_chaser2', name: 'Theodore Nott',       role: 'chaser', x: 320, z: 90,  y: 20},
      {file: 'slyt_chaser3', name: 'Pansy Parkinson',     role: 'chaser', x: 320, z: 105, y: 20},
      {file: 'slyt_beater1', name: 'Marcus Flint',        role: 'beater', x: 300, z: 70,  y: 30},
      {file: 'slyt_beater2', name: 'Millicent Bulstrode', role: 'beater', x: 300, z: 110, y: 30},
      {file: 'slyt_seeker',  name: 'Draco Malfoy',        role: 'seeker', x: 250, z: 85,  y: 55},
    ],
  };
  const TEAM_NAME = {A: 'Gryffindor', B: 'Slytherin'};
  const SPEEDS = {keeper: 55, chaser: 85, beater: 75, seeker: 105};

  // the pace of the match and what the difficulty changes (the opponents only: your own team's AI is always "normal")
  const PACE = 0.7;                        // every player's speed
  const MAX_TIME = 420;                    // seconds; at the end the seeker that is nearer to the snitch catches it
  const SNITCH_WINDOW = {first: [40, 60], every: [14, 26], length: 3.0, slow: 0.35, catchRange: 10, aiRange: 10};
  const DIFFICULTY = {
    // aiCatch: the chance that the opponents' seeker takes the Snitch in one tired window (a seeker of your own team is as good as "normal")
    easy:   {speed: 0.96, shotErr: 23, steal: 0.35, intercept: 0.31, save: 0.58, aiCatch: 0.075},
    normal: {speed: 1.00, shotErr: 20, steal: 0.40, intercept: 0.35, save: 0.62, aiCatch: 0.10},
    hard:   {speed: 1.03, shotErr: 18.5, steal: 0.43, intercept: 0.385, save: 0.66, aiCatch: 0.135},
  };

  // ---- the random generator: mulberry32, its state is a number kept in the snapshot ----
  function rand(S) {
    S.rng = (S.rng + 0x6D2B79F5) | 0;
    let t = Math.imul(S.rng ^ (S.rng >>> 15), 1 | S.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  const dist3 = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const attackDir = team => (team === 'A' ? 1 : -1);          // +1: toward x=500
  const opp = team => (team === 'A' ? 'B' : 'A');

  // ---- creating a match ----
  function create(opts) {
    const o = Object.assign({team: 'A', slot: 6, difficulty: 'normal', seed: (Math.random() * 2 ** 31) | 0}, opts || {});
    const S = {
      v: 1, rng: o.seed | 0, time: 0, over: false, result: null,
      score: {A: 0, B: 0}, difficulty: DIFFICULTY[o.difficulty] ? o.difficulty : 'normal',
      humanTeam: o.team, humanSlot: o.slot, human: -1,
      players: [], bludgers: [], msgs: [], flash: null, restart: 0,
      quaffle: {x: 250, y: 20, z: 90, prevX: 250, carrier: -1, flying: false, vx: 0, vy: 0, vz: 0, resetTimer: 0, throwerTeam: null, thrower: -1, throwerGrace: 0, immune: 0},
      snitch: {x: 0, y: 0, z: 0, nx: 0, ny: 0, nz: 1, changeTimer: 0, window: 0, nextWindow: 0},
    };
    for (const team of ['A', 'B']) {
      ROSTER[team].forEach((r, slot) => {
        S.players.push({
          id: S.players.length, team, slot, role: r.role, name: r.name, file: r.file, sx: r.x, sy: r.y, sz: r.z,
          x: r.x, y: r.y, z: r.z, nx: team === 'A' ? 1 : -1, ny: 0, nz: 0, vx: 0, vz: 0, tx: r.x, ty: r.y, tz: r.z,
          stunTime: 0, stealCd: 0, passCd: 0, interceptCd: 0, actCd: 0, dive: 0, hoop: -1, human: false,
        });
        if (team === o.team && slot === o.slot) { S.human = S.players.length - 1; S.players[S.human].human = true; }
      });
    }
    S.players.forEach(p => pickTarget(S, p));
    S.bludgers = [{x: 250, y: 25, z: 70, carrier: -1, flying: false, throwerTeam: null, vx: 0, vy: 0, vz: 0, hitCd: 0}, {x: 250, y: 25, z: 110, carrier: -1, flying: false, throwerTeam: null, vx: 0, vy: 0, vz: 0, hitCd: 0}];
    const sn = S.snitch;
    sn.x = 150 + rand(S) * 200; sn.y = 40 + rand(S) * 15; sn.z = 60 + rand(S) * 60;
    snitchPickDir(S); sn.nextWindow = SNITCH_WINDOW.first[0] + rand(S) * (SNITCH_WINDOW.first[1] - SNITCH_WINDOW.first[0]);
    say(S, 'The match begins');
    return S;
  }

  function say(S, text, color) {
    S.msgs.push({text, color: color || null, t: 3.5});
    if (S.msgs.length > 4) S.msgs.shift();
  }

  function pickTarget(S, p) {
    p.tx = FIELD.xMin + rand(S) * (FIELD.xMax - FIELD.xMin);
    p.ty = FIELD.yMin + rand(S) * (FIELD.yMax - FIELD.yMin);
    p.tz = FIELD.zMin + rand(S) * (FIELD.zMax - FIELD.zMin);
  }
  const diffOf = (S, p) => (p.team === S.humanTeam ? DIFFICULTY.normal : DIFFICULTY[S.difficulty]);

  // ---- the balls ----
  function dropQuaffle(S) {
    const q = S.quaffle;
    q.carrier = -1; q.flying = false; q.vx = q.vy = q.vz = 0;
  }
  // a throw of the quaffle: `err` is how far it can miss (units), `lead` leads a moving target
  function throwQuaffle(S, thrower, tx, ty, tz, err, speed) {
    const q = S.quaffle, sp = speed || 220;
    q.carrier = -1; q.flying = true; q.throwerTeam = thrower.team; q.thrower = thrower.id; q.throwerGrace = 0.4;
    const dx = tx + (rand(S) * 2 - 1) * err - q.x, dy = ty + (rand(S) * 2 - 1) * err * 0.6 - q.y, dz = tz + (rand(S) * 2 - 1) * err - q.z;
    const d = Math.hypot(dx, dy, dz);
    if (d < 0.01) { q.flying = false; return; }
    q.vx = dx / d * sp; q.vy = dy / d * sp; q.vz = dz / d * sp;
  }
  function throwBludger(S, b, thrower, t) {
    b.carrier = -1; b.flying = true; b.throwerTeam = thrower.team; b.hitCd = 0.25;
    const dx = t.x - b.x, dy = t.y - b.y, dz = t.z - b.z, d = Math.hypot(dx, dy, dz);
    if (d < 0.01) { b.flying = false; return; }
    b.vx = dx / d * 200; b.vy = dy / d * 200; b.vz = dz / d * 200;
  }
  function leadOf(p, from, speed) {                // where a teammate will be when a pass arrives
    const t = dist3(from, p) / (speed || 220), sp = SPEEDS[p.role] * PACE;
    return {x: clamp(p.x + p.nx * sp * t * 0.5, FIELD.xMin, FIELD.xMax), y: p.y, z: clamp(p.z + p.nz * sp * t * 0.5, FIELD.zMin, FIELD.zMax)};
  }

  function updateQuaffle(S, dt) {
    const q = S.quaffle;
    q.prevX = q.x;
    if (q.throwerGrace > 0) { q.throwerGrace -= dt; if (q.throwerGrace <= 0) q.thrower = -1; }
    q.immune = Math.max(0, (q.immune || 0) - dt);
    if (q.resetTimer > 0) { q.resetTimer -= dt; return; }
    if (q.carrier >= 0) {
      const c = S.players[q.carrier];
      q.x = c.x; q.y = c.y; q.z = c.z; q.vx = q.vy = q.vz = 0; q.flying = false;
    } else if (q.flying) {
      q.x += q.vx * dt; q.y += q.vy * dt; q.z += q.vz * dt;
      const dec = Math.pow(0.75, dt);
      q.vx *= dec; q.vy *= dec; q.vz *= dec;
      if (Math.hypot(q.vx, q.vy, q.vz) < 15) q.flying = false;
      if (q.x < FIELD.xMin || q.x > FIELD.xMax) { q.vx *= -0.5; q.x = clamp(q.x, FIELD.xMin, FIELD.xMax); }
      if (q.y < FIELD.yMin || q.y > FIELD.yMax) { q.vy *= -0.5; q.y = clamp(q.y, FIELD.yMin, FIELD.yMax); }
      if (q.z < FIELD.zMin || q.z > FIELD.zMax) { q.vz *= -0.5; q.z = clamp(q.z, FIELD.zMin, FIELD.zMax); }
    } else {
      q.y = Math.max(FIELD.yMin, q.y - 5 * dt);
    }
  }

  function resetQuaffle(S) {
    const q = S.quaffle;
    q.x = 250; q.y = 20; q.z = 90; q.prevX = 250; q.carrier = -1; q.flying = false; q.vx = q.vy = q.vz = 0; q.resetTimer = 2; q.thrower = -1; q.throwerGrace = 0;
    S.restart = 2.2;
  }

  function checkGoal(S) {
    const q = S.quaffle;
    if (q.resetTimer > 0) return;
    for (const h of HOOPS) {
      const crossed = (q.prevX <= h.x && q.x > h.x) || (q.prevX >= h.x && q.x < h.x);
      if (crossed && Math.abs(q.z - h.z) < 8 && Math.abs(q.y - h.y) < 8) {
        S.score[h.scorTeam] += 10;
        const who = q.thrower >= 0 ? S.players[q.thrower] : null;
        S.flash = {team: h.scorTeam, text: 'GOAL! +10 ' + TEAM_NAME[h.scorTeam], timer: 2};
        say(S, (who && who.team === h.scorTeam ? who.name : TEAM_NAME[h.scorTeam]) + ' scores!', h.scorTeam === S.humanTeam ? '#7be07b' : '#ff9a7a');
        resetQuaffle(S);
        return;
      }
    }
  }

  function updateBludger(S, b, dt) {
    b.hitCd = Math.max(0, b.hitCd - dt);
    if (b.carrier >= 0) {
      const c = S.players[b.carrier];
      if (c.stunTime > 0) { b.carrier = -1; return; }
      b.x = c.x; b.y = c.y; b.z = c.z; return;
    }
    if (!b.flying) { b.y = Math.max(FIELD.yMin, b.y - 3 * dt); return; }
    b.x += b.vx * dt; b.y += b.vy * dt; b.z += b.vz * dt;
    const dec = Math.pow(0.78, dt);
    b.vx *= dec; b.vy *= dec; b.vz *= dec;
    if (Math.hypot(b.vx, b.vy, b.vz) < 15) b.flying = false;
    if (b.hitCd <= 0) {
      for (const p of S.players) {
        if (p.stunTime > 0 || p.team === b.throwerTeam) continue;
        if (dist3(b, p) < 14) {
          stun(S, p, 3);
          b.flying = false; b.vx = b.vy = b.vz = 0; b.hitCd = 1.5;
          if (p.human) say(S, 'A bludger hits you!', '#ff7a5c'); else if (b.throwerTeam === S.humanTeam) say(S, p.name + ' is hit!', '#7be07b');
          break;
        }
      }
    }
    if (b.x < FIELD.xMin || b.x > FIELD.xMax) { b.vx *= -0.6; b.x = clamp(b.x, FIELD.xMin, FIELD.xMax); }
    if (b.y < FIELD.yMin || b.y > FIELD.yMax) { b.vy *= -0.6; b.y = clamp(b.y, FIELD.yMin, FIELD.yMax); }
    if (b.z < FIELD.zMin || b.z > FIELD.zMax) { b.vz *= -0.6; b.z = clamp(b.z, FIELD.zMin, FIELD.zMax); }
  }

  function stun(S, p, secs) {
    p.stunTime = secs; p.vx = p.vz = 0;
    if (S.quaffle.carrier === p.id) { dropQuaffle(S); S.quaffle.throwerGrace = 0.3; S.quaffle.thrower = p.id; }   // the hit player drops the quaffle
    for (const b of S.bludgers) if (b.carrier === p.id) b.carrier = -1;
  }

  // ---- the snitch: erratic and faster than any seeker, but tired for 3 seconds every 14-26 ----
  function snitchPickDir(S) {
    const sn = S.snitch, theta = rand(S) * Math.PI * 2, phi = (rand(S) - 0.5) * 1.2;
    sn.nx = Math.cos(phi) * Math.cos(theta); sn.ny = Math.sin(phi) * 0.4; sn.nz = Math.cos(phi) * Math.sin(theta);
    const len = Math.hypot(sn.nx, sn.ny, sn.nz) || 1;
    sn.nx /= len; sn.ny /= len; sn.nz /= len;
    sn.changeTimer = 0.6 + rand(S) * 1.4;
  }
  function updateSnitch(S, dt) {
    const sn = S.snitch;
    if (sn.window > 0) { sn.window -= dt; if (sn.window <= 0) { sn.window = 0; sn.nextWindow = SNITCH_WINDOW.every[0] + rand(S) * (SNITCH_WINDOW.every[1] - SNITCH_WINDOW.every[0]); } }
    else {
      sn.nextWindow -= dt;
      if (sn.nextWindow <= 0) { sn.window = SNITCH_WINDOW.length; say(S, 'The Snitch is tiring: catch it now!', '#ffd24a'); }
    }
    sn.changeTimer -= dt;
    let evading = false;
    if (sn.window <= 0) {
      for (const p of S.players) {
        if (p.role !== 'seeker' || p.stunTime > 0) continue;
        const d = dist3(sn, p);
        if (d < 90) {
          const fx = (sn.x - p.x) / d, fy = (sn.y - p.y) / d, fz = (sn.z - p.z) / d, perp = rand(S) * 0.6 - 0.3;
          sn.nx = fx - fz * perp; sn.ny = fy * 0.4; sn.nz = fz + fx * perp;
          const len = Math.hypot(sn.nx, sn.ny, sn.nz) || 1;
          sn.nx /= len; sn.ny /= len; sn.nz /= len;
          evading = true; sn.changeTimer = 0.3;
          break;
        }
      }
    }
    if (!evading && sn.changeTimer <= 0) snitchPickDir(S);
    const rz = 60, rx = 80, ry = 12, rs = 3.5;                    // the walls push it back
    let ax = 0, ay = 0, az = 0;
    if (sn.x - FIELD.xMin < rx) ax += (1 - (sn.x - FIELD.xMin) / rx) * rs;
    if (FIELD.xMax - sn.x < rx) ax -= (1 - (FIELD.xMax - sn.x) / rx) * rs;
    if (sn.z - FIELD.zMin < rz) az += (1 - (sn.z - FIELD.zMin) / rz) * rs;
    if (FIELD.zMax - sn.z < rz) az -= (1 - (FIELD.zMax - sn.z) / rz) * rs;
    if (sn.y - 30 < ry) ay += (1 - (sn.y - 30) / ry) * rs;
    if (FIELD.yMax - sn.y < ry) ay -= (1 - (FIELD.yMax - sn.y) / ry) * rs;
    sn.nx += ax; sn.ny += ay; sn.nz += az;
    const rl = Math.hypot(sn.nx, sn.ny, sn.nz) || 1;
    sn.nx /= rl; sn.ny /= rl; sn.nz /= rl;
    const speed = 260 * (sn.window > 0 ? SNITCH_WINDOW.slow : 1);
    sn.x += sn.nx * speed * dt; sn.y += sn.ny * speed * dt; sn.z += sn.nz * speed * dt;
    if (sn.x < FIELD.xMin || sn.x > FIELD.xMax) { sn.nx = -sn.nx; sn.x = clamp(sn.x, FIELD.xMin, FIELD.xMax); }
    if (sn.y < 30 || sn.y > FIELD.yMax) { sn.ny = -sn.ny; sn.y = clamp(sn.y, 30, FIELD.yMax); }
    if (sn.z < FIELD.zMin || sn.z > FIELD.zMax) { sn.nz = -sn.nz; sn.z = clamp(sn.z, FIELD.zMin, FIELD.zMax); }
  }
  function snitchCaught(S, team, by) {
    if (S.over) return;
    S.score[team] += 150; S.over = true;
    const win = S.score.A > S.score.B ? 'A' : S.score.B > S.score.A ? 'B' : null;
    S.result = {winner: win, caughtBy: team, by: by ? by.name : null, score: {A: S.score.A, B: S.score.B}, time: S.time, youWon: win === S.humanTeam, draw: !win};
  }

  // ---- the actions of the roles: the AI and the human use the same ----
  function nearestOf(list, from) { return list.reduce((a, b) => (!a || dist3(from, b) < dist3(from, a) ? b : a), null); }
  // the target of a throw that is closest to the direction (ax, az) the player aims at (or the nearest when there is no aim)
  function bestAligned(list, p, ax, az) {
    const m = Math.hypot(ax, az);
    if (m < 0.3) return nearestOf(list, p);
    let best = null, bs = -9;
    for (const t of list) {
      const dx = t.x - p.x, dz = t.z - p.z, d = Math.hypot(dx, dz) || 1;
      const s = (dx * ax + dz * az) / (d * m) - d / 700;      // the alignment, a nearer one a little better
      if (s > bs) { bs = s; best = t; }
    }
    return best;
  }
  function shoot(S, p, hoopIdx, err) {
    const h = HOOPS[hoopIdx];
    p.hoop = -1;
    const d = Math.hypot(h.x - p.x, h.z - p.z);
    throwQuaffle(S, p, h.x, h.y, h.z, err + d * 0.03);
  }
  function passTo(S, p, to, err) {
    const lead = leadOf(to, p);
    throwQuaffle(S, p, lead.x, lead.y, lead.z, err || 3);
    p.passCd = 1;
  }
  function trySteal(S, p, carrier, chance) {
    if (p.stealCd > 0 || S.quaffle.immune > 0) return false;           // whoever has just taken the quaffle keeps it for a moment
    p.stealCd = 1.5;
    if (rand(S) < chance) { S.quaffle.carrier = p.id; S.quaffle.thrower = -1; S.quaffle.immune = 0.9; return true; }
    return false;
  }
  function hitBludger(S, p, b, target) {
    throwBludger(S, b, p, target);
    p.actCd = 0.6;
  }
  function seekerCatchAttempt(S, p, chance) {
    const sn = S.snitch;
    if (sn.window > 0 && dist3(p, sn) < (p.human ? SNITCH_WINDOW.catchRange : SNITCH_WINDOW.aiRange) && rand(S) < chance) { snitchCaught(S, p.team, p); return true; }
    return false;
  }

  // ---- the AI ----
  function aiMove(S, p) {
    switch (p.role) {
      case 'chaser': chaserAI(S, p); break;
      case 'keeper': keeperAI(S, p); break;
      case 'seeker': seekerAI(S, p); break;
      case 'beater': beaterAI(S, p); break;
    }
  }
  function beaterAI(S, p) {
    const mine = S.bludgers.find(b => b.carrier === p.id);
    if (mine) {
      const t = nearestOf(S.players.filter(o => o.team !== p.team && o.stunTime <= 0), p);
      if (t) hitBludger(S, p, mine, t);
      pickTarget(S, p);
      return;
    }
    const free = S.bludgers.filter(b => b.carrier < 0 && !b.flying);
    if (!free.length) return;
    const nb = nearestOf(free, p);
    const beaters = S.players.filter(o => o.team === p.team && o.role === 'beater');
    if (nearestOf(beaters, nb) === p) {
      p.tx = nb.x; p.ty = nb.y; p.tz = nb.z;
      if (dist3(p, nb) < 15) nb.carrier = p.id;
    }
  }
  function chaserAI(S, p) {
    const q = S.quaffle, d = diffOf(S, p);
    if (q.carrier !== p.id) p.hoop = -1;
    if (q.resetTimer > 0) { p.tx = p.sx; p.ty = p.sy; p.tz = p.sz; return; }          // back to the formation after a goal
    const mates = S.players.filter(o => o.team === p.team && o.role === 'chaser');
    if (q.carrier < 0 && !q.flying) {
      if (nearestOf(mates.filter(m => m.stunTime <= 0), q) === p) {
        p.tx = q.x; p.ty = q.y; p.tz = q.z;
        if (dist3(p, q) < 15) { q.carrier = p.id; q.thrower = -1; }
      } else { p.tx = p.team === 'A' ? 440 : 60; p.ty = 25; }
      return;
    }
    if (q.flying) {
      if (p.id === q.thrower) return;
      p.tx = q.x; p.ty = q.y; p.tz = q.z;
      if (dist3(p, q) < 15 && p.interceptCd <= 0) {
        p.interceptCd = 0.3;
        if (rand(S) < (p.team === q.throwerTeam ? 0.9 : d.intercept)) { q.carrier = p.id; q.flying = false; }
      }
      return;
    }
    if (q.carrier === p.id) {
      if (p.hoop < 0) { const mine = HOOPS.map((h, i) => i).filter(i => HOOPS[i].defTeam !== p.team); p.hoop = mine[Math.floor(rand(S) * mine.length)]; }
      const h = HOOPS[p.hoop];
      p.tx = h.x - attackDir(p.team) * 60; p.ty = h.y; p.tz = h.z;
      const inZone = p.team === 'A' ? p.x > 380 : p.x < 120;
      if (inZone) { shoot(S, p, p.hoop, d.shotErr); return; }
      const near = nearestOf(S.players.filter(o => o.team !== p.team && o.role !== 'seeker' && o.stunTime <= 0), p);
      if (near && dist3(p, near) < 22 && p.passCd <= 0) {
        const allies = S.players.filter(o => o !== p && o.team === p.team && (o.role === 'chaser' || o.role === 'keeper') && o.stunTime <= 0);
        const ahead = allies.filter(a => (p.team === 'A' ? a.x > p.x : a.x < p.x));
        const pool = (ahead.length ? ahead : allies).filter(a => dist3(p, a) > 20);
        if (pool.length) { passTo(S, p, nearestOf(pool, p), 3); p.hoop = -1; }
      }
      return;
    }
    const carrier = q.carrier >= 0 ? S.players[q.carrier] : null;
    if (carrier && carrier.team === p.team) { p.hoop = -1; p.tx = p.team === 'A' ? 440 : 60; p.ty = 25; return; }
    if (carrier) {
      const nd = nearestOf(mates.filter(m => m.stunTime <= 0), carrier);
      if (nd === p) {
        p.tx = carrier.x; p.ty = carrier.y; p.tz = carrier.z;
        if (dist3(p, carrier) < 10) trySteal(S, p, carrier, d.steal);
      } else {
        const others = mates.filter(c => c !== nd);
        p.tx = p.team === 'A' ? 150 : 350; p.ty = 20; p.tz = others.indexOf(p) === 0 ? 75 : 105;
      }
    }
  }
  function keeperAI(S, p) {
    const q = S.quaffle, d = diffOf(S, p);
    if (q.resetTimer > 0) return;
    const homeX = p.team === 'A' ? 30 : 470, hoops = HOOPS.filter(h => h.defTeam === p.team);
    const carrier = q.carrier >= 0 ? S.players[q.carrier] : null;
    const threat = dist3(p, q) < 80 || (carrier && carrier.team !== p.team);
    if (threat) { const nh = hoops.reduce((a, b) => (dist3(q, a) < dist3(q, b) ? a : b)); p.tx = homeX; p.ty = nh.y; p.tz = nh.z; }
    else { p.tx = homeX; p.ty = 25; p.tz = 90; }
    if (q.flying && q.throwerTeam !== p.team && dist3(p, q) < 15) {
      if (p.interceptCd <= 0) { p.interceptCd = 0.3; if (rand(S) < d.save) { q.carrier = p.id; q.flying = false; } }
    } else if (q.carrier < 0 && !q.flying && dist3(p, q) < 15) { q.carrier = p.id; q.thrower = -1; }
    if (q.carrier === p.id) {
      const ally = S.players.filter(o => o.team === p.team && o.role === 'chaser' && o.stunTime <= 0).sort((a, b) => (p.team === 'A' ? b.x - a.x : a.x - b.x))[0];
      if (ally) passTo(S, p, ally, 4);
    }
  }
  function seekerAI(S, p) {
    const sn = S.snitch;
    p.tx = sn.x; p.ty = sn.y; p.tz = sn.z;
    if (!p.human && sn.window > 0) {
      // the opponent's chance to take it in the window; your own team's seeker is as good as "normal"
      // the goals count: a seeker whose team leads catches more often, one whose team trails less (each goal of the lead is +12%, from a quarter to double), so that the Chaser,
      // the Beater and the Keeper you fly decide matches too; without it the Snitch of the AI seekers decided every match whatever you did (tests/quidditch_balance.js)
      const lead = (S.score[p.team] - S.score[opp(p.team)]) / 10;
      const chance = clamp(diffOf(S, p).aiCatch * clamp(1 + lead * 0.12, 0.25, 2.0), 0, 0.6);
      const perAttempt = 1 - Math.pow(1 - chance, 1 / 3);                             // about three attempts fit in a window
      if (dist3(p, sn) < SNITCH_WINDOW.aiRange && p.interceptCd <= 0) { p.interceptCd = 0.5; if (rand(S) < perAttempt) snitchCaught(S, p.team, p); }
    }
  }

  // ---- the human ----
  // input: {mx, mz: where to fly (-1..1, in the pitch's axes), ax, az: where to aim (-1..1, 0 = at the moving direction), a, x: the buttons pressed since the last step}
  function humanControl(S, p, dt, input) {
    const q = S.quaffle, i = input || {};
    const mx = clamp(i.mx || 0, -1, 1), mz = clamp(i.mz || 0, -1, 1);
    let ax = i.ax || 0, az = i.az || 0;
    if (Math.hypot(ax, az) < 0.3) { ax = mx; az = mz; }                      // no aim: along the move
    if (Math.hypot(ax, az) < 0.3) { ax = attackDir(p.team); az = 0; }         // standing still: toward the goal you attack
    p.aimx = ax; p.aimz = az;
    const sp = SPEEDS[p.role] * PACE;
    const m = Math.min(1, Math.hypot(mx, mz));
    const wx = m > 0.05 ? mx / Math.hypot(mx, mz) * m * sp : 0, wz = m > 0.05 ? mz / Math.hypot(mx, mz) * m * sp : 0;
    p.vx += (wx - p.vx) * Math.min(1, dt * 9); p.vz += (wz - p.vz) * Math.min(1, dt * 9);
    // the height goes by itself to what matters (the ball, the hoop, the snitch), up to 30 units a second
    let ty = p.y;
    const carrier = q.carrier === p.id;
    if (p.role === 'seeker') ty = S.snitch.y;
    else if (p.role === 'beater') {   // the height follows the nearest free bludger (the one you are flying to), not the first one in the list: found when a Beater was flown with the real buttons
      const free = S.bludgers.filter(bl => bl.carrier < 0), nb = nearestOf(free.filter(bl => !bl.flying).length ? free.filter(bl => !bl.flying) : free, p);
      ty = nb ? nb.y : 25;
    }
    else if (carrier) { const best = HOOPS.filter(h => h.defTeam !== p.team).reduce((a, b) => (Math.abs(a.z - p.z) < Math.abs(b.z - p.z) ? a : b)); ty = best.y; }
    else if (q.carrier < 0 || dist3(p, q) < 90) ty = q.y; else ty = 25;
    p.ty = clamp(ty, FIELD.yMin, FIELD.yMax);
    const dy = clamp(p.ty - p.y, -30 * dt, 30 * dt);
    p.x += p.vx * dt; p.z += p.vz * dt; p.y += dy;
    const vlen = Math.hypot(p.vx, p.vz);
    if (vlen > 6) { p.nx = p.vx / vlen; p.nz = p.vz / vlen; p.ny = dy / (dt || 1) / Math.max(sp, 1); }

    // grabbing by itself: a free quaffle or bludger within reach, a pass of the team
    if (p.role === 'chaser' || p.role === 'keeper') {
      if (q.carrier < 0 && !q.flying && q.resetTimer <= 0 && dist3(p, q) < 14) { q.carrier = p.id; q.thrower = -1; }
      else if (q.flying && p.id !== q.thrower && q.throwerTeam === p.team && dist3(p, q) < 14 && p.interceptCd <= 0) { p.interceptCd = 0.3; if (rand(S) < 0.9) { q.carrier = p.id; q.flying = false; } }
    }
    if (p.role === 'beater' && !S.bludgers.some(b => b.carrier === p.id)) {
      const nb = nearestOf(S.bludgers.filter(b => b.carrier < 0 && !b.flying), p);
      if (nb && dist3(p, nb) < 15) nb.carrier = p.id;
    }
    if (i.a) humanA(S, p, ax, az);
    if (i.x) humanX(S, p, ax, az);
  }
  function humanA(S, p, ax, az) {
    const q = S.quaffle;
    if (p.actCd > 0) return;
    if (p.role === 'chaser') {
      if (q.carrier === p.id) {
        const hoops = HOOPS.map((h, i) => i).filter(i => HOOPS[i].defTeam !== p.team).map(i => ({i, x: HOOPS[i].x, z: HOOPS[i].z}));
        const h = bestAligned(hoops, p, ax, az);
        shoot(S, p, h.i, 5);
        p.actCd = 0.3;
      } else if (q.carrier >= 0 && S.players[q.carrier].team !== p.team) {
        const c = S.players[q.carrier];
        if (dist3(p, c) < 16) trySteal(S, p, c, 0.55);
      } else if (q.carrier < 0 && dist3(p, q) < 22 && q.resetTimer <= 0) { q.carrier = p.id; q.flying = false; q.thrower = -1; }
    } else if (p.role === 'beater') {
      const b = S.bludgers.find(bl => bl.carrier === p.id);
      if (b) {
        const t = bestAligned(S.players.filter(o => o.team !== p.team && o.stunTime <= 0), p, ax, az);
        if (t) { hitBludger(S, p, b, t); say(S, 'You hit the bludger at ' + t.name); }
      }
    } else if (p.role === 'keeper') {
      if (q.carrier === p.id) {
        const t = bestAligned(S.players.filter(o => o.team === p.team && o.role === 'chaser' && o.stunTime <= 0), p, ax, az);
        if (t) { passTo(S, p, t, 3); p.actCd = 0.3; }
      } else if (p.dive <= 0 && p.actCd <= 0) { p.dive = 0.4; p.actCd = 1.4; }
    } else if (p.role === 'seeker') {
      if (!seekerCatchAttempt(S, p, 0.97)) p.actCd = 0.7;
    }
  }
  function humanX(S, p, ax, az) {
    const q = S.quaffle;
    if (q.carrier !== p.id || p.passCd > 0) return;
    const t = bestAligned(S.players.filter(o => o !== p && o.team === p.team && (o.role === 'chaser' || o.role === 'keeper') && o.stunTime <= 0 && dist3(p, o) > 15), p, ax, az);
    if (t) passTo(S, p, t, 3);
  }
  // what A and X do right now: the page writes it in the hint line
  function hints(S) {
    const p = S.players[S.human], q = S.quaffle;
    let a = '', x = '';
    if (p.role === 'chaser') {
      if (q.carrier === p.id) { a = 'Shoot'; x = 'Pass'; }
      else if (q.carrier >= 0 && S.players[q.carrier].team !== p.team) a = 'Steal';
      else if (q.carrier < 0) a = 'Grab';
    } else if (p.role === 'beater') a = S.bludgers.some(b => b.carrier === p.id) ? 'Hit the bludger' : 'Fetch a bludger';
    else if (p.role === 'keeper') { a = q.carrier === p.id ? 'Throw' : 'Dive'; }
    else if (p.role === 'seeker') a = S.snitch.window > 0 ? 'CATCH!' : 'Catch the Snitch';
    return {a, x};
  }

  // ---- one step ----
  function step(S, dt, input) {
    if (S.over) return;
    dt = Math.min(dt, 0.05);
    S.time += dt;
    S.restart = Math.max(0, S.restart - dt);
    for (const m of S.msgs) m.t -= dt;
    S.msgs = S.msgs.filter(m => m.t > 0);
    if (S.flash) { S.flash.timer -= dt; if (S.flash.timer <= 0) S.flash = null; }

    for (const p of S.players) {
      if (p.stunTime > 0) { p.stunTime -= dt; p.y = Math.max(FIELD.yMin, p.y - 25 * dt); p.vx = p.vz = 0; continue; }
      p.stealCd = Math.max(0, p.stealCd - dt); p.passCd = Math.max(0, p.passCd - dt); p.interceptCd = Math.max(0, p.interceptCd - dt); p.actCd = Math.max(0, p.actCd - dt);
      if (p.dive > 0) {                                  // the keeper's dive: a wide reach for a moment
        p.dive -= dt;
        const q = S.quaffle;
        if (q.flying && q.throwerTeam !== p.team && dist3(p, q) < 26 && rand(S) < 0.9 * dt * 6) { q.carrier = p.id; q.flying = false; p.dive = 0; say(S, 'What a save!', '#7be07b'); }
      }
      if (p.human) { humanControl(S, p, dt, input); }
      else {
        aiMove(S, p);
        const dx = p.tx - p.x, dy = p.ty - p.y, dz = p.tz - p.z, d = Math.hypot(dx, dy, dz);
        if (d < 5) { if (p.role === 'beater' && !S.bludgers.some(b => b.carrier < 0 && !b.flying)) pickTarget(S, p); }   // arrived: only a beater with nothing to fetch wanders on
        else {
          const sp = SPEEDS[p.role] * PACE * diffOf(S, p).speed;
          p.nx = dx / d; p.ny = dy / d; p.nz = dz / d;
          p.x += p.nx * sp * dt; p.y += p.ny * sp * dt; p.z += p.nz * sp * dt;
          p.vx = p.nx * sp; p.vz = p.nz * sp;
        }
      }
      p.x = clamp(p.x, FIELD.xMin, FIELD.xMax); p.y = clamp(p.y, FIELD.yMin, FIELD.yMax); p.z = clamp(p.z, FIELD.zMin, FIELD.zMax);
    }
    updateQuaffle(S, dt);
    checkGoal(S);
    for (const b of S.bludgers) updateBludger(S, b, dt);
    updateSnitch(S, dt);
    // the human seeker needs A (humanA); the AI seekers catch inside seekerAI. At the end of the time the nearer seeker takes it.
    if (!S.over && S.time >= MAX_TIME) {
      const seekers = S.players.filter(p => p.role === 'seeker');
      const s = nearestOf(seekers, S.snitch);
      say(S, 'Time is up: the Snitch is caught');
      snitchCaught(S, s.team, s);
    }
  }
  // ---- saving: the state is plain data ----
  const snapshot = S => JSON.parse(JSON.stringify(S));
  const restore = s => JSON.parse(JSON.stringify(s));

  root.QuidditchSim = {create, step, snapshot, restore, hints, ROSTER, TEAM_NAME, HOOPS, FIELD, SPEEDS, DIFFICULTY, MAX_TIME, PACE, dist3, CATCH_RANGE: SNITCH_WINDOW.catchRange};
  if (typeof module !== 'undefined' && module.exports) module.exports = root.QuidditchSim;
})(typeof window !== 'undefined' ? window : globalThis);
